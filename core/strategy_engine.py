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
        
        # --- VIRTUAL LEVELS (In Memory Only) ---
        self.virtual_buy_trigger = None  
        self.virtual_sell_trigger = None 
        
        # --- Grid State ---
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.current_step = 0
        
        self.session = None
        self.last_processed_ticket = 0
        self.current_price = 0.0

    @property
    def config(self):
        # Force reload from file to ensure UI updates are caught immediately
        self.config_manager.load_config()
        return self.config_manager.get_config()

    async def start_ticker(self):
        print("🔄 Config Change: Resetting Virtual Grid...")
        self.reset_cycle()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        
        # 1. Clean the Slate (Janitor Duty)
        # Even though we are virtual now, we must ensure no OLD pending orders exist.
        print("🧹 Startup: Clearing legacy orders...")
        try:
            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")
        except: pass

        # 2. Sync History watermark
        try:
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=600", timeout=5) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    if deals: self.last_processed_ticket = max(d['ticket'] for d in deals)
        except: pass

        self.reset_cycle()
        asyncio.create_task(self.run_logic_loop()) 
        print(f"✅ Sniper Strategy Started: {self.symbol}")

    async def stop(self):
        self.running = False
        if self.session: await self.session.close()
        print("🛑 Strategy Stopped.")

    def reset_cycle(self):
        """Wipes the virtual board."""
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.virtual_buy_trigger = None
        self.virtual_sell_trigger = None
        self.current_step = 0
        print("🔄 Virtual Grid Reset: Waiting for price...")

    async def on_external_tick(self, tick_data):
        """
        The 'Sniper Scope'. Checks price every tick.
        """
        if not self.running: return

        ask = float(tick_data['ask'])
        bid = float(tick_data['bid'])
        self.current_price = ask 

        # 1. Initialization (First Tick Only)
        if self.level_center is None:
            self.init_grid(ask)
            return

        # 2. Check Triggers
        # Only proceed if we haven't hit max positions
        max_pos = int(self.config.get('max_positions', 5))
        if self.current_step >= max_pos:
            return

        # --- SNIPER LOGIC ---
        # If Price crosses the line -> FIRE Market Order immediately.
        
        # BUY TRIGGER
        if self.virtual_buy_trigger and ask >= self.virtual_buy_trigger:
            print(f"⚡ SNIPER: Price {ask} hit Buy Level {self.virtual_buy_trigger}")
            await self.execute_market_order("buy", ask)
            
        # SELL TRIGGER
        elif self.virtual_sell_trigger and bid <= self.virtual_sell_trigger:
            print(f"⚡ SNIPER: Price {bid} hit Sell Level {self.virtual_sell_trigger}")
            await self.execute_market_order("sell", bid)

    def init_grid(self, price):
        spread = float(self.config.get('spread', 6.0))
        
        self.level_center = price
        self.level_top = price + spread
        self.level_bottom = price - spread
        
        # Initial State: Virtual Buy at Top, Virtual Sell at Bottom
        self.virtual_buy_trigger = self.level_top
        self.virtual_sell_trigger = self.level_bottom
        
        print(f"🎯 Grid Initialized (Step 0):")
        print(f"   Top (Buy):    {self.level_top:.2f}")
        print(f"   Center:       {self.level_center:.2f}")
        print(f"   Bottom (Sell): {self.level_bottom:.2f}")

    async def execute_market_order(self, direction, price):
        """
        Fires the actual trade and advances the state.
        """
        # 1. Get correct volume for this step (Just-in-Time)
        vol = self.get_volume(self.current_step)
        
        # 2. Execute Market Order
        print(f"🚀 FIRING {direction.upper()} | Step {self.current_step} | Lot: {vol}")
        success = await self.send_market_request(direction, price, vol)
        
        if not success:
            print("❌ Misfire: Broker rejected order. Retrying logic next tick.")
            return

        # 3. Advance State (The "Ping-Pong" Logic)
        self.current_step += 1
        
        if direction == "buy":
            # We bought. Disable Buy Trigger. Enable Sell Trigger.
            self.virtual_buy_trigger = None 
            
            # Logic: Snap to nearest grid level to determine next move
            dist_top = abs(price - self.level_top)
            dist_center = abs(price - self.level_center)
            
            if dist_top < dist_center:
                # Bought at Top -> Next Target: Sell Center
                self.virtual_sell_trigger = self.level_center
                print(f"➡ Next Target: SELL @ Center ({self.level_center:.2f})")
            else:
                # Bought at Center -> Next Target: Sell Bottom
                self.virtual_sell_trigger = self.level_bottom
                print(f"➡ Next Target: SELL @ Bottom ({self.level_bottom:.2f})")

        elif direction == "sell":
            # We sold. Disable Sell Trigger. Enable Buy Trigger.
            self.virtual_sell_trigger = None
            
            dist_bottom = abs(price - self.level_bottom)
            dist_center = abs(price - self.level_center)
            
            if dist_bottom < dist_center:
                # Sold at Bottom -> Next Target: Buy Center
                self.virtual_buy_trigger = self.level_center
                print(f"➡ Next Target: BUY @ Center ({self.level_center:.2f})")
            else:
                # Sold at Center -> Next Target: Buy Top
                self.virtual_buy_trigger = self.level_top
                print(f"➡ Next Target: BUY @ Top ({self.level_top:.2f})")

    async def run_logic_loop(self):
        """Background loop to check for TP/SL (Nuclear Reset)."""
        while self.running:
            try:
                await self.check_sl_tp()
            except: pass
            await asyncio.sleep(1.0)

    async def check_sl_tp(self):
        if not self.session: return
        
        async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=600") as resp:
            if resp.status != 200: return
            deals = await resp.json()

        new_deals = [d for d in deals if d['ticket'] > self.last_processed_ticket]
        if not new_deals: return
        self.last_processed_ticket = max(d['ticket'] for d in new_deals)

        for deal in new_deals:
            # DEAL_ENTRY_OUT (1) = Closed Position
            # Profit != 0 = TP/SL Hit
            if deal.get('entry', 0) == 1 or float(deal['profit']) != 0:
                print(f"🚨 TRADE CLOSED (Profit: {deal['profit']}) -> NUCLEAR RESET")
                await self.session.post(f"{self.mt5_bridge_url}/close_all")
                self.reset_cycle()
                return

    async def send_market_request(self, direction, price, volume):
        # Retrieve SL/TP (Raw Values from Config)
        if "buy" in direction:
            sl = float(self.config.get('buy_stop_sl', 0))
            tp = float(self.config.get('buy_stop_tp', 0))
        else:
            sl = float(self.config.get('sell_stop_sl', 0))
            tp = float(self.config.get('sell_stop_tp', 0))

        payload = {
            "action": direction,  # "buy" or "sell" (Market)
            "symbol": self.symbol,
            "volume": float(volume),
            "price": float(price), # Ignored for market entry, but passed for logs
            "sl_points": sl,
            "tp_points": tp,
            "comment": f"Step {self.current_step}"
        }
        
        try:
            async with self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload) as resp:
                if resp.status == 200: return True
                print(f"❌ Bridge Rejected: {await resp.text()}")
                return False
        except Exception as e:
            print(f"❌ Connection Error: {e}")
            return False

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        print(f"🔍 Lot Lookup: Step {step} | Config: {step_lots}")
        
        if not step_lots: return 0.01
        if step < len(step_lots): return step_lots[step]
        return step_lots[-1]

    def get_status(self):
        return {
            "running": self.running,
            "current_price": self.current_price,
            "step": self.current_step,
            "top": self.level_top,
            "center": self.level_center,
            "bottom": self.level_bottom,
            "next_buy": self.virtual_buy_trigger,
            "next_sell": self.virtual_sell_trigger
        }