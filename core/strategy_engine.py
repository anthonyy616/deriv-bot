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
        self.virtual_buy_trigger = None  # Price to trigger a BUY
        self.virtual_sell_trigger = None # Price to trigger a SELL
        
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
        return self.config_manager.get_config()

    async def start_ticker(self):
        print("🔄 Config Change: Resetting Virtual Grid...")
        self.reset_cycle()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        
        # Sync history watermark
        try:
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=600", timeout=5) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    if deals: self.last_processed_ticket = max(d['ticket'] for d in deals)
        except: pass

        self.reset_cycle()
        asyncio.create_task(self.run_logic_loop()) # Main Brain
        print(f"✅ Strategy Started (Virtual Mode): {self.symbol}")

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
        Calculates levels on the first tick, then monitors triggers on every subsequent tick.
        """
        if not self.running: return

        # 1. Update Price
        ask = float(tick_data['ask'])
        bid = float(tick_data['bid'])
        self.current_price = ask 

        # 2. Initialization (First Tick Only)
        if self.level_center is None:
            self.init_grid(ask)
            return

        # 3. Check Virtual Triggers (The "Virtual Order" Logic)
        # We only check triggers if we haven't maxed out positions
        max_pos = int(self.config.get('max_positions', 5))
        if self.current_step >= max_pos:
            return

        # Check BUY Trigger
        if self.virtual_buy_trigger and ask >= self.virtual_buy_trigger:
            print(f"⚡ VIRTUAL BUY TRIGGER HIT @ {ask} (Target: {self.virtual_buy_trigger})")
            await self.execute_market_order("buy", ask)
            
        # Check SELL Trigger
        elif self.virtual_sell_trigger and bid <= self.virtual_sell_trigger:
            print(f"⚡ VIRTUAL SELL TRIGGER HIT @ {bid} (Target: {self.virtual_sell_trigger})")
            await self.execute_market_order("sell", bid)

    def init_grid(self, price):
        """Sets the initial Top/Center/Bottom and Virtual Triggers."""
        spread = float(self.config.get('spread', 6.0))
        
        self.level_center = price
        self.level_top = price + spread
        self.level_bottom = price - spread
        
        # Initial State: Pending Buy at Top, Pending Sell at Bottom
        self.virtual_buy_trigger = self.level_top
        self.virtual_sell_trigger = self.level_bottom
        
        print(f"🎯 Grid Initialized (Virtual):")
        print(f"   Top (Buy Trig): {self.level_top}")
        print(f"   Center:         {self.level_center}")
        print(f"   Bottom (Sel Trig): {self.level_bottom}")

    async def execute_market_order(self, direction, price):
        """
        Fires the actual trade to the broker and updates the virtual state.
        """
        # 1. Get correct volume for this step
        vol = self.get_volume(self.current_step)
        
        # 2. Send Market Order (IOC)
        print(f"🚀 EXECUTING {direction.upper()} | Step {self.current_step} | Lot: {vol}")
        success = await self.send_market_request(direction, price, vol)
        
        if not success:
            print("❌ Order failed via Bridge. Retrying next tick...")
            return # Don't advance state if order failed

        # 3. Advance State (The "Ping-Pong" Logic)
        self.current_step += 1
        
        if direction == "buy":
            # We just bought. 
            # 1. Disable Buy Trigger (Can't buy again immediately)
            self.virtual_buy_trigger = None 
            
            # 2. Set Sell Trigger (One level down)
            # If we bought at Top, Sell Trigger is Center
            # If we bought at Center, Sell Trigger is Bottom
            dist_top = abs(price - self.level_top)
            dist_center = abs(price - self.level_center)
            
            if dist_top < dist_center:
                self.virtual_sell_trigger = self.level_center
                print(f"➡ Next Action: Wait for SELL @ Center ({self.level_center})")
            else:
                self.virtual_sell_trigger = self.level_bottom
                print(f"➡ Next Action: Wait for SELL @ Bottom ({self.level_bottom})")

        elif direction == "sell":
            # We just sold.
            # 1. Disable Sell Trigger
            self.virtual_sell_trigger = None
            
            # 2. Set Buy Trigger (One level up)
            dist_bottom = abs(price - self.level_bottom)
            dist_center = abs(price - self.level_center)
            
            if dist_bottom < dist_center:
                self.virtual_buy_trigger = self.level_center
                print(f"➡ Next Action: Wait for BUY @ Center ({self.level_center})")
            else:
                self.virtual_buy_trigger = self.level_top
                print(f"➡ Next Action: Wait for BUY @ Top ({self.level_top})")

    async def run_logic_loop(self):
        """Background loop to check for TP/SL (Nuclear Reset)."""
        while self.running:
            try:
                await self.check_sl_tp()
            except Exception as e:
                print(f"Logic Loop Error: {e}")
            await asyncio.sleep(1.0)

    async def check_sl_tp(self):
        if not self.session: return
        
        # Check recent deals for any 'EXIT' types (SL/TP)
        async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=600") as resp:
            if resp.status != 200: return
            deals = await resp.json()

        new_deals = [d for d in deals if d['ticket'] > self.last_processed_ticket]
        if not new_deals: return
        self.last_processed_ticket = max(d['ticket'] for d in new_deals)

        for deal in new_deals:
            # DEAL_ENTRY_OUT (1) means a position was closed
            # If ANY position closes, we reset.
            if deal.get('entry', 0) == 1 or deal['profit'] != 0:
                print(f"🚨 POSITION CLOSED (Profit: {deal['profit']}) -> NUCLEAR RESET")
                await self.session.post(f"{self.mt5_bridge_url}/close_all")
                self.reset_cycle()
                return

    async def send_market_request(self, direction, price, volume):
        """Sends immediate market order."""
        # Get SL/TP distances
        if "buy" in direction:
            sl = float(self.config.get('buy_stop_sl', 0))
            tp = float(self.config.get('buy_stop_tp', 0))
        else:
            sl = float(self.config.get('sell_stop_sl', 0))
            tp = float(self.config.get('sell_stop_tp', 0))

        payload = {
            "action": direction, # "buy" or "sell" (Market)
            "symbol": self.symbol,
            "volume": float(volume),
            "price": float(price), # Ignored for market, but good for logs
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
        # TRACER: Prove we are reading the array
        print(f"🔍 Lot Logic: Step {step} | Array: {step_lots}")
        
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
            # Debug info for UI
            "next_buy": self.virtual_buy_trigger,
            "next_sell": self.virtual_sell_trigger
        }