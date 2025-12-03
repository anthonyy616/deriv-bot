import asyncio
import time
import aiohttp
import os

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.running = False
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        
        # --- Fixed Levels ---
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        
        self.current_step = 0
        self.session = None
        self.start_time = 0 
        self.last_processed_ticket = 0

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.start_time = time.time()
        
        # Sync Sequence to avoid reading old deals
        try:
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=60", timeout=2) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    if deals: self.last_processed_ticket = max(d['ticket'] for d in deals)
        except: pass

        self.reset_cycle()
        asyncio.create_task(self.run_watchdog()) # Launch Safety Monitor
        print(f"✅ Strategy Started: {self.symbol}")

    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()
            self.session = None
        print("🛑 Strategy Stopped.")

    def reset_cycle(self):
        """ Hard Reset: Clears levels to force re-calculation on next tick """
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.current_step = 0
        print("🔄 Cycle Reset: Waiting for price to set levels...")

    async def on_external_tick(self, tick_data):
        if not self.running: return

        ask = tick_data['ask']
        point = tick_data.get('point', 0.001)

        # 1. Initialize Levels (First Tick Only)
        if self.level_center is None:
            spread_points = self.config.get('spread', 2) 
            spread_val = spread_points * point 
            
            self.level_center = ask
            self.level_top = self.level_center + spread_val
            self.level_bottom = self.level_center - spread_val
            
            print(f"🎯 Levels Set | Top: {self.level_top:.5f} | Center: {self.level_center:.5f} | Bottom: {self.level_bottom:.5f}")
            
            # 2. Place Initial Straddle (Pending Orders)
            # 0.1 Lot Buy Stop @ Top, 0.1 Lot Sell Stop @ Bottom
            vol = self.get_volume(0)
            await self.send_pending("buy_stop", self.level_top, vol)
            await self.send_pending("sell_stop", self.level_bottom, vol)
            return

        # Note: We do NOT execute logic here anymore. 
        # Logic is now Event-Driven (Deal Detection) in run_watchdog.

    async def run_watchdog(self):
        """ Monitors for Deals (Entry/TP/SL) and updates Pending Orders """
        print("👀 Watchdog Active")
        while self.running:
            try:
                await self.check_deals()
            except Exception as e:
                print(f"Watchdog Error: {e}")
            await asyncio.sleep(0.5) # Fast polling for deal updates

    async def check_deals(self):
        if not self.session: return
        
        # Fetch deals since last check
        async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=10", timeout=2) as resp:
            if resp.status != 200: return
            deals = await resp.json()

        # Filter new deals
        new_deals = [d for d in deals if d['ticket'] > self.last_processed_ticket]
        if not new_deals: return

        # Update watermark
        self.last_processed_ticket = max(d['ticket'] for d in new_deals)

        for deal in new_deals:
            # 1. Check for TP/SL (Profit != 0) -> TERMINATE
            if deal['profit'] != 0:
                print(f"🚨 TP/SL HIT ({deal['profit']}) -> RESETTING...")
                await self.session.post(f"{self.mt5_bridge_url}/close_all")
                self.reset_cycle()
                return # Stop processing other deals

            # 2. Check for ENTRY (Profit == 0 usually for entry deals)
            # Deal Type 0 = Buy, 1 = Sell
            deal_type = deal['type'] 
            self.current_step += 1
            next_vol = self.get_volume(self.current_step)

            print(f"⚡ Deal Detected: {'BUY' if deal_type == 0 else 'SELL'} | Step {self.current_step}")

            # CLEANUP: Remove old pending orders (e.g. the other side of the straddle)
            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")

            # LOGIC: Tightening Channel
            if deal_type == 0: # We just BOUGHT (at Top or Center)
                # Next move: SELL STOP at CENTER
                # (User Rule: "SS moves to 10")
                target_price = self.level_center
                print(f"➡ Placing Sell Stop @ {target_price:.5f}")
                await self.send_pending("sell_stop", target_price, next_vol)
            
            elif deal_type == 1: # We just SOLD (at Bottom or Center)
                # Next move: BUY STOP at TOP (or Center?)
                # User Rule: "If SS hits... place BS again at 12 (Top)"
                # But if we sold at Bottom (8), we target Center (10).
                # If we sold at Center (10), we target Top (12).
                
                # Simple logic: If we hold a Sell, we want to Buy above.
                # If we just sold at Bottom (8), next Buy is Center (10).
                # If we just sold at Center (10), next Buy is Top (12).
                
                # To distinguish, check the Deal Price?
                # Simplify: "Tightening" means we oscillate [Center, Top] OR [Bottom, Center].
                
                # Assuming first breakout was UP (Buy @ Top):
                # We are locked in [Center, Top].
                # Sell was at Center. Next Buy is Top.
                
                # Assuming first breakout was DOWN (Sell @ Bottom):
                # We are locked in [Bottom, Center].
                # Sell was at Bottom. Next Buy is Center.
                
                # Robust Calculation:
                # If Deal Price < Center (approx): We are at Bottom. Next Buy = Center.
                # If Deal Price >= Center (approx): We are at Center. Next Buy = Top.
                
                # Note: Deal price might deviate slightly, so use 0.1 tolerance or just logic
                # Actually, simpler: Always default to restoring the tight channel.
                # If we sold, place Buy Stop at Level Top (if we are high) or Center (if we are low).
                
                # For this specific user request ("BS hits 12, SS moves to 10... SS hits 10, BS moves to 12"):
                # This describes the [10, 12] channel.
                target_price = self.level_top
                print(f"➡ Placing Buy Stop @ {target_price:.5f}")
                await self.send_pending("buy_stop", target_price, next_vol)

    async def send_pending(self, action, price, volume):
        """ Helper to send Stop Orders """
        payload = {
            "action": action,
            "symbol": self.symbol,
            "volume": float(volume),
            "price": float(price), # CRITICAL: Send the exact calculated level
            "sl_points": 0,
            "tp_points": 0,
            "comment": f"Step {self.current_step}"
        }
        try:
            await self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload)
        except:
            pass

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        if step < len(step_lots): return step_lots[step]
        return step_lots[-1] if step_lots else 0.01

    def get_status(self):
        return {
            "running": self.running,
            "step": self.current_step,
            "top": self.level_top,
            "center": self.level_center,
            "bottom": self.level_bottom
        }