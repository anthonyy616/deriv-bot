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
        
        # --- VIRTUAL LEVELS ---
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
        
        # --- Lifecycle Management ---
        self.start_time = 0
        self.open_positions = 0 # For UI

    @property
    def config(self):
        self.config_manager.load_config()
        return self.config_manager.get_config()

    async def start_ticker(self):
        print("🔄 Config Change: Resetting Virtual Grid...")
        self.reset_cycle()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.start_time = time.time() # Start the Clock
        
        # 1. Clean the Slate
        print("🧹 Startup: Clearing legacy orders...")
        try:
            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")
        except: pass

        # 2. Sync History
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
        """Wipes the virtual board to start a fresh iteration."""
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.virtual_buy_trigger = None
        self.virtual_sell_trigger = None
        self.current_step = 0
        print("🔄 Cycle Reset: Ready for new Grid.")

    async def on_external_tick(self, tick_data):
        if not self.running: return

        ask = float(tick_data['ask'])
        bid = float(tick_data['bid'])
        self.current_price = ask 
        
        # UI Update: Update open positions from Engine
        self.open_positions = tick_data.get('positions_count', 0)

        # 1. Initialization (First Tick Only)
        if self.level_center is None:
            self.init_grid(ask)
            return

        # 2. Check Triggers
        # Only place new trades if we haven't hit max positions
        max_pos = int(self.config.get('max_positions', 5))
        if self.current_step >= max_pos:
            return # Wait for SL/TP to trigger the reset

        # --- SOFT STOP CHECK ---
        # If time is up, we do NOT place new orders. We just manage existing ones.
        if self.is_time_up():
            return

        # 3. Sniper Logic
        if self.virtual_buy_trigger and ask >= self.virtual_buy_trigger:
            print(f"⚡ SNIPER: Price {ask} hit Buy Level {self.virtual_buy_trigger}")
            await self.execute_market_order("buy", ask)
            
        elif self.virtual_sell_trigger and bid <= self.virtual_sell_trigger:
            print(f"⚡ SNIPER: Price {bid} hit Sell Level {self.virtual_sell_trigger}")
            await self.execute_market_order("sell", bid)

    def is_time_up(self):
        max_mins = int(self.config.get('max_runtime_minutes', 0))
        if max_mins == 0: return False # 0 means infinite
        
        elapsed_mins = (time.time() - self.start_time) / 60
        if elapsed_mins > max_mins:
            # We don't print here to avoid log spam, but we return True
            return True
        return False

    def init_grid(self, price):
        spread = float(self.config.get('spread', 6.0))
        
        self.level_center = price
        self.level_top = price + spread
        self.level_bottom = price - spread
        
        self.virtual_buy_trigger = self.level_top
        self.virtual_sell_trigger = self.level_bottom
        
        print(f"🎯 Grid Initialized (Step 0) | Spread: {spread}")

    async def execute_market_order(self, direction, price):
        vol = self.get_volume(self.current_step)
        
        print(f"🚀 FIRING {direction.upper()} | Step {self.current_step} | Lot: {vol}")
        success = await self.send_market_request(direction, price, vol)
        
        if not success:
            print("❌ Misfire: Broker rejected order.")
            return

        self.current_step += 1
        
        if direction == "buy":
            self.virtual_buy_trigger = None 
            dist_top = abs(price - self.level_top)
            dist_center = abs(price - self.level_center)
            
            if dist_top < dist_center:
                self.virtual_sell_trigger = self.level_center
                print(f"➡ Next Target: SELL @ Center ({self.level_center:.2f})")
            else:
                self.virtual_sell_trigger = self.level_bottom
                print(f"➡ Next Target: SELL @ Bottom ({self.level_bottom:.2f})")

        elif direction == "sell":
            self.virtual_sell_trigger = None
            dist_bottom = abs(price - self.level_bottom)
            dist_center = abs(price - self.level_center)
            
            if dist_bottom < dist_center:
                self.virtual_buy_trigger = self.level_center
                print(f"➡ Next Target: BUY @ Center ({self.level_center:.2f})")
            else:
                self.virtual_buy_trigger = self.level_top
                print(f"➡ Next Target: BUY @ Top ({self.level_top:.2f})")

    async def run_logic_loop(self):
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
            # 1. Detect if a position CLOSED (Entry Out or Profit != 0)
            if deal.get('entry', 0) == 1 or float(deal['profit']) != 0:
                print(f"🚨 TRADE CLOSED (Profit: {deal['profit']}) -> NUCLEAR RESET")
                
                # 2. Nuclear Reset: Close everything else
                await self.session.post(f"{self.mt5_bridge_url}/close_all")
                
                # 3. Lifecycle Check: Should we restart?
                if self.is_time_up():
                    print("🛑 Max Runtime Exceeded. Stopping Bot.")
                    await self.stop()
                else:
                    print("🔄 Starting Next Iteration...")
                    self.reset_cycle()
                return

    async def send_market_request(self, direction, price, volume):
        if "buy" in direction:
            sl = float(self.config.get('buy_stop_sl', 0))
            tp = float(self.config.get('buy_stop_tp', 0))
        else:
            sl = float(self.config.get('sell_stop_sl', 0))
            tp = float(self.config.get('sell_stop_tp', 0))

        payload = {
            "action": direction,
            "symbol": self.symbol,
            "volume": float(volume),
            "price": float(price),
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
        # print(f"🔍 Lot Lookup: Step {step} | Config: {step_lots}") 
        if not step_lots: return 0.01
        if step < len(step_lots): return step_lots[step]
        return step_lots[-1]

    def get_status(self):
        return {
            "running": self.running,
            "current_price": self.current_price,
            "open_positions": self.open_positions, # NOW POPULATED
            "step": self.current_step,
            "top": self.level_top,
            "center": self.level_center,
            "bottom": self.level_bottom,
            "next_buy": self.virtual_buy_trigger,
            "next_sell": self.virtual_sell_trigger
        }