import asyncio
import time
import aiohttp
import os
import math

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.running = False
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        
        # --- Grid Levels (Fixed upon initialization) ---
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        
        self.current_step = 0
        self.session = None
        self.last_processed_ticket = 0
        
        # For UI Display
        self.current_price = 0.0

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        """Resets logic when config changes."""
        self.reset_cycle()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        
        # Sync with existing deals to avoid processing old history
        try:
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=60", timeout=2) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    if deals: 
                        self.last_processed_ticket = max(d['ticket'] for d in deals)
        except: pass

        self.reset_cycle()
        asyncio.create_task(self.run_watchdog())
        print(f"✅ Strategy Started: {self.symbol}")

    async def stop(self):
        self.running = False
        if self.session: 
            await self.session.close()
            self.session = None
        print("🛑 Strategy Stopped.")

    def reset_cycle(self):
        """Clears levels and resets step counter."""
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.current_step = 0
        print("🔄 Cycle Reset: Waiting for tick to define grid...")

    async def on_external_tick(self, tick_data):
        """Handles the very first setup (placing initial Buy/Sell stops)."""
        if not self.running: return

        ask = tick_data['ask']
        self.current_price = ask # Update for UI

        # 1. Initialize Levels (Only if not set)
        if self.level_center is None:
            # Interpret 'spread' as Raw Price Radius (e.g. 6.0)
            spread_val = float(self.config.get('spread', 6.0))
            
            # Define exact grid levels
            self.level_center = ask
            self.level_top = self.level_center + spread_val
            self.level_bottom = self.level_center - spread_val
            
            print(f"🎯 Grid Set | Top: {self.level_top:.2f} | Center: {self.level_center:.2f} | Bottom: {self.level_bottom:.2f}")
            
            # 2. Place Initial Straddle (Step 0 Lots)
            vol = self.get_volume(0)
            
            # Place both sides
            await self.send_pending("buy_stop", self.level_top, vol)
            await self.send_pending("sell_stop", self.level_bottom, vol)

    async def run_watchdog(self):
        """Background task to monitor executions."""
        while self.running:
            try:
                await self.check_deals()
            except Exception as e:
                print(f"Watchdog Error: {e}")
            await asyncio.sleep(0.5) 

    async def check_deals(self):
        if not self.session: return
        
        # 1. Poll Bridge
        async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=10") as resp:
            if resp.status != 200: return
            deals = await resp.json()

        # 2. Filter New Deals
        new_deals = [d for d in deals if d['ticket'] > self.last_processed_ticket]
        if not new_deals: return
        
        # Update watermark
        self.last_processed_ticket = max(d['ticket'] for d in new_deals)

        for deal in new_deals:
            # --- CASE A: TP/SL Hit (Nuclear Reset) ---
            # If profit is not 0, it means a position closed (TP/SL).
            if deal['profit'] != 0:
                print(f"🚨 TP/SL HIT ({deal['profit']}) -> RESETTING EVERYTHING...")
                await self.session.post(f"{self.mt5_bridge_url}/close_all")
                self.reset_cycle()
                return 

            # --- CASE B: Entry Execution (Next Step) ---
            # Profit is 0, so this is a new market entry.
            self.current_step += 1
            max_pos = int(self.config.get('max_positions', 5))

            print(f"⚡ Deal Detected: {deal['type']} (0=Buy, 1=Sell) | Step {self.current_step}")

            # 1. Cancel all remaining pending orders (The "Push/Pop" logic)
            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")

            # 2. Check Limit
            if self.current_step >= max_pos:
                print("🛑 Max positions reached. No new orders.")
                return

            # 3. Determine Next Move
            next_vol = self.get_volume(self.current_step)
            deal_type = deal['type']  # 0 = Buy, 1 = Sell
            
            # We snap the execution price to our known grid levels to prevent drift
            # logic: "Where did we just execute?"
            exec_price = deal.get('price', 0) # Fallback if price missing, though rare
            
            target_price = 0.0
            next_action = ""

            if deal_type == 0: 
                # === We just BOUGHT ===
                # Logic: We must now place a SELL STOP.
                # If we bought at Top, new Sell is at Center.
                # If we bought at Center (during oscillating down), new Sell is at Bottom.
                
                dist_to_top = abs(exec_price - self.level_top)
                dist_to_center = abs(exec_price - self.level_center)
                
                if dist_to_top < dist_to_center:
                    target_price = self.level_center # Bought at Top -> Sell Center
                else:
                    target_price = self.level_bottom # Bought at Center -> Sell Bottom
                
                next_action = "sell_stop"

            elif deal_type == 1:
                # === We just SOLD ===
                # Logic: We must now place a BUY STOP.
                # If we sold at Bottom, new Buy is at Center.
                # If we sold at Center (during oscillating up), new Buy is at Top.

                dist_to_bottom = abs(exec_price - self.level_bottom)
                dist_to_center = abs(exec_price - self.level_center)

                if dist_to_bottom < dist_to_center:
                    target_price = self.level_center # Sold at Bottom -> Buy Center
                else:
                    target_price = self.level_top    # Sold at Center -> Buy Top

                next_action = "buy_stop"

            # 4. Place the Next Order
            print(f"➡ Placing {next_action} @ {target_price:.5f} (Vol: {next_vol})")
            await self.send_pending(next_action, target_price, next_vol)

    async def send_pending(self, action, price, volume):
        """ Sends order with configured SL/TP distances """
        # Get SL/TP from Config (Raw Values)
        if "buy" in action.lower():
            sl_dist = float(self.config.get('buy_stop_sl', 0))
            tp_dist = float(self.config.get('buy_stop_tp', 0))
        else:
            sl_dist = float(self.config.get('sell_stop_sl', 0))
            tp_dist = float(self.config.get('sell_stop_tp', 0))

        payload = {
            "action": action,
            "symbol": self.symbol,
            "volume": float(volume),
            "price": float(price),
            "sl_points": sl_dist,
            "tp_points": tp_dist,
            "comment": f"Step {self.current_step}"
        }
        try:
            await self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload)
        except Exception as e:
            print(f"❌ Failed to send order: {e}")

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        if not step_lots: return 0.01
        
        # If step exceeds array, use the last defined lot size
        if step < len(step_lots):
            return step_lots[step]
        return step_lots[-1]

    def get_status(self):
        return {
            "running": self.running,
            "current_price": self.current_price,
            "step": self.current_step,
            "top": self.level_top,
            "center": self.level_center,
            "bottom": self.level_bottom
        }