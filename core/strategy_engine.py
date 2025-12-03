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

    async def start_ticker(self):
        """Called by server.py when config changes to restart logic"""
        self.reset_cycle()

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
        # point = tick_data.get('point', 0.001) # IGNORE POINT for user inputs

        # 1. Initialize Levels (First Tick Only)
        if self.level_center is None:
            # FIX: Use raw value from config (e.g., 6.0) directly, do not multiply by point
            spread_val = float(self.config.get('spread', 6.0))
            
            self.level_center = ask
            self.level_top = self.level_center + spread_val
            self.level_bottom = self.level_center - spread_val
            
            print(f"🎯 Levels Set | Top: {self.level_top:.5f} | Center: {self.level_center:.5f} | Bottom: {self.level_bottom:.5f}")
            
            # 2. Place Initial Straddle (Pending Orders)
            vol = self.get_volume(0)
            await self.send_pending("buy_stop", self.level_top, vol)
            await self.send_pending("sell_stop", self.level_bottom, vol)
            return

    async def run_watchdog(self):
        """ Monitors for Deals (Entry/TP/SL) and updates Pending Orders """
        # print("👀 Watchdog Active") # Reduce log spam
        while self.running:
            try:
                await self.check_deals()
            except Exception as e:
                print(f"Watchdog Error: {e}")
            await asyncio.sleep(0.5) 

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
                return 

            # 2. Check for ENTRY (Profit == 0 usually for entry deals)
            deal_type = deal['type'] 
            self.current_step += 1
            next_vol = self.get_volume(self.current_step)

            print(f"⚡ Deal Detected: {'BUY' if deal_type == 0 else 'SELL'} | Step {self.current_step}")

            # CLEANUP: Remove old pending orders
            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")

            # LOGIC: Tightening Channel
            if deal_type == 0: # Bought
                # Next move: SELL STOP at CENTER
                target_price = self.level_center
                print(f"➡ Placing Sell Stop @ {target_price:.5f}")
                await self.send_pending("sell_stop", target_price, next_vol)
            
            elif deal_type == 1: # Sold
                # Next move: BUY STOP at TOP
                target_price = self.level_top
                print(f"➡ Placing Buy Stop @ {target_price:.5f}")
                await self.send_pending("buy_stop", target_price, next_vol)

    async def send_pending(self, action, price, volume):
        """ Helper to send Stop Orders with Configured SL/TP """
        
        # FIX: Retrieve TP/SL from config based on direction
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
            "sl_points": sl_dist, # Sending raw distance (e.g. 24.0)
            "tp_points": tp_dist, # Sending raw distance (e.g. 16.0)
            "comment": f"Step {self.current_step}"
        }
        try:
            await self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload)
        except Exception as e:
            print(f"❌ Failed to send order: {e}")

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