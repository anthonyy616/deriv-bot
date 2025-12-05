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
        
        # --- IMMUTABLE GRID ANCHORS ---
        self.anchor_center = None 
        self.anchor_top = None
        self.anchor_bottom = None
        
        # --- Virtual Triggers ---
        self.virtual_buy_trigger = None  
        self.virtual_sell_trigger = None 
        
        # --- State Memory ---
        # Tracks which level is currently being WATCHED (Top/Center/Bottom)
        self.last_target_source = None 
        
        # --- General State ---
        self.current_step = 0
        self.session = None
        self.last_processed_ticket = 0
        self.current_price = 0.0
        
        # --- Lifecycle ---
        self.start_time = 0
        self.last_pos_count = 0
        self.is_resetting = False 
        self.reset_timestamp = 0
        self.iteration = 1

    @property
    def config(self):
        self.config_manager.load_config()
        return self.config_manager.get_config()

    async def start_ticker(self):
        print("🔄 Config Change: Forcing Grid Reset...")
        self.is_resetting = True
        self.reset_timestamp = time.time()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.start_time = time.time()
        
        print("🧹 Startup: Clearing legacy orders...")
        try:
            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")
        except: pass

        # Sync History
        try:
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=600", timeout=5) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    if deals: self.last_processed_ticket = max(d['ticket'] for d in deals)
        except: pass

        self.reset_cycle()
        print(f"✅ Strategy Started: {self.symbol}")

    async def stop(self):
        self.running = False
        if self.session: await self.session.close()
        print("🛑 Strategy Stopped.")

    def reset_cycle(self):
        """Full reset of the grid geometry."""
        self.anchor_center = None
        self.anchor_top = None
        self.anchor_bottom = None
        
        self.virtual_buy_trigger = None
        self.virtual_sell_trigger = None
        self.last_target_source = None
        
        self.current_step = 0
        self.is_resetting = False
        print(f"🔄 Cycle Reset: Waiting for new Anchor (Iteration {self.iteration})...")

    async def on_external_tick(self, tick_data):
        if not self.running: return

        ask = float(tick_data['ask'])
        bid = float(tick_data['bid'])
        self.current_price = ask 
        current_pos_count = tick_data.get('positions_count', 0)
        
        # 1. CRITICAL: Check for SL/TP (Nuclear Reset) INSTANTLY
        if current_pos_count < self.last_pos_count and not self.is_resetting and self.current_step > 0:
            print(f"🚨 POSITIONS DROPPED. NUCLEAR RESET TRIGGERED!")
            self.trigger_nuclear_reset()
            self.last_pos_count = current_pos_count
            return

        self.last_pos_count = current_pos_count

        # 2. Handle Reset State
        if self.is_resetting:
            if current_pos_count == 0:
                if self.is_time_up():
                    print("🛑 Max Runtime Reached. Stopping.")
                    await self.stop()
                    return
                print("✅ Account Cleaned. Starting New Iteration.")
                self.iteration += 1
                self.reset_cycle()
            else:
                if time.time() - self.reset_timestamp > 2:
                    await self.session.post(f"{self.mt5_bridge_url}/close_all")
                    self.reset_timestamp = time.time()
            return

        # 3. Initialization
        if self.anchor_center is None:
            self.init_immutable_grid(ask)
            return

        # 4. Check Limits
        max_pos = int(self.config.get('max_positions', 5))
        if self.current_step >= max_pos:
            return 

        if self.is_time_up():
            return

        # 5. SNIPER LOGIC
        if self.virtual_buy_trigger and ask >= self.virtual_buy_trigger:
            print(f"⚡ SNIPER: Buy Hit {self.virtual_buy_trigger}")
            # Source must be passed deterministically
            source = "top" if self.virtual_buy_trigger == self.anchor_top else "center"
            await self.execute_market_order("buy", ask, source)
            
        elif self.virtual_sell_trigger and bid <= self.virtual_sell_trigger:
            print(f"⚡ SNIPER: Sell Hit {self.virtual_sell_trigger}")
            source = "bottom" if self.virtual_sell_trigger == self.anchor_bottom else "center"
            await self.execute_market_order("sell", bid, source)

    def trigger_nuclear_reset(self):
        self.is_resetting = True
        self.reset_timestamp = time.time()
        asyncio.create_task(self.session.post(f"{self.mt5_bridge_url}/close_all"))

    def is_time_up(self):
        max_mins = int(self.config.get('max_runtime_minutes', 0))
        if max_mins == 0: return False
        if (time.time() - self.start_time) / 60 > max_mins: return True
        return False

    def init_immutable_grid(self, price):
        spread = float(self.config.get('spread', 6.0))
        
        self.anchor_center = price
        self.anchor_top = price + spread
        self.anchor_bottom = price - spread
        
        # Initial State: Watch Top and Bottom
        self.virtual_buy_trigger = self.anchor_top
        self.virtual_sell_trigger = self.anchor_bottom
        self.last_target_source = "start" # Mark the initial dual watch state
        
        print(f"⚓ ANCHOR: {self.anchor_center} | Spread: {spread} | Full Distance: {self.anchor_top - self.anchor_bottom}")

    async def execute_market_order(self, direction, price, source):
        vol = self.get_volume(self.current_step)
        print(f"🚀 FIRING {direction.upper()} | Source: {source} | Lot: {vol}")
        
        success = await self.send_market_request(direction, price, vol)
        if not success:
            print("❌ Order Rejected. Retrying logic...")
            return

        self.current_step += 1
        
        # --- DETERMINISTIC PING PONG TRANSITION (FIXES 22 PIP GAP) ---
        
        if direction == "buy":
            self.virtual_buy_trigger = None # Lock Buy
            
            if source == "top":
                # Buy @ Top -> Next Target: Sell @ Center (10 pips away)
                self.virtual_sell_trigger = self.anchor_center
                self.last_target_source = "center" 
                print(f"➡ Next Target: SELL @ Center ({self.anchor_center:.2f})")
                
            elif source == "center":
                # Buy @ Center -> Next Target: Sell @ Bottom (10 pips away)
                self.virtual_sell_trigger = self.anchor_bottom
                self.last_target_source = "bottom" 
                print(f"➡ Next Target: SELL @ Bottom ({self.anchor_bottom:.2f})")

        elif direction == "sell":
            self.virtual_sell_trigger = None # Lock Sell
            
            if source == "bottom":
                # Sell @ Bottom -> Next Target: Buy @ Center (10 pips away)
                self.virtual_buy_trigger = self.anchor_center
                self.last_target_source = "center"
                print(f"➡ Next Target: BUY @ Center ({self.anchor_center:.2f})")
                
            elif source == "center":
                # Sell @ Center -> Next Target: Buy @ Top (10 pips away)
                self.virtual_buy_trigger = self.anchor_top
                self.last_target_source = "top"
                print(f"➡ Next Target: BUY @ Top ({self.anchor_top:.2f})")

    async def run_logic_loop(self):
        while self.running:
            try:
                # We do not call check_sl_tp here to avoid race conditions. 
                # It is called on every tick (on_external_tick).
                pass
            except: pass
            await asyncio.sleep(0.5)

    async def check_sl_tp(self):
        # NOTE: This function is now DEPRECATED as the logic moved to on_external_tick.
        # It remains for context.
        pass

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
            "magic": self.iteration, 
            "comment": f"S{self.current_step}-I{self.iteration}"
        }
        try:
            async with self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload) as resp:
                if resp.status == 200: return True
                return False
        except: return False

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        if not step_lots: return 0.01
        if step < len(step_lots): return step_lots[step]
        return step_lots[-1]

    def get_status(self):
        return {
            "running": self.running,
            "current_price": self.current_price,
            "open_positions": self.open_positions,
            "step": self.current_step,
            "iteration": self.iteration,
            "is_resetting": self.is_resetting,
            "anchor": self.anchor_center,
            "next_buy": self.virtual_buy_trigger,
            "next_sell": self.virtual_sell_trigger
        }