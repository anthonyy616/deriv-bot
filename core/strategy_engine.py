import asyncio
import time
import requests
import os
from typing import List

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.cmp = None
        self.pending_orders = [] 
        self.positions = []      
        self.running = False
        self.iteration = 0
        self.iteration_state = "idle"
        self.start_time = 0
        self.initial_balance = 0
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        
        # FIXED LEVELS (The "Channel")
        self.upper_level = None
        self.lower_level = None
        self.current_step = 0
        
    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        print(f"Strategy listening for ticks on: {self.symbol}")

    async def start(self):
        self.running = True
        self.start_time = time.time()
        self.iteration = 0
        self.reset_state()
        
        self.initial_balance = self.get_account_balance()
        print(f"✅ Strategy Started. Symbol: {self.symbol} | Start Balance: {self.initial_balance}")
        
        asyncio.create_task(self.main_loop())

    def reset_state(self):
        """Resets the iteration state to allow a fresh start."""
        self.pending_orders = []
        self.positions = []
        self.iteration_state = "idle"
        self.upper_level = None
        self.lower_level = None
        self.current_step = 0

    async def main_loop(self):
        while self.running:
            await asyncio.sleep(1)
            
            # 1. Update Positions
            self.update_positions()
            
            # 2. Check Stopping Conditions (TP/SL/Max)
            if await self.check_stopping_conditions():
                continue # If reset occurred, skip to next loop

    async def on_external_tick(self, tick_data):
        if not self.running: return
        if tick_data['symbol'] != self.symbol: return

        # 1. Extract Data
        ask = tick_data['ask']
        bid = tick_data['bid']
        point = tick_data.get('point', 0.01)
        
        self.cmp = ask
        
        # 2. Process Strategy
        await self.process_strategy(ask, bid, point)

    async def process_strategy(self, ask, bid, point):
        # STAGE 1: Place Initial Virtual Grid
        if self.iteration_state == "idle":
            await self.place_initial_brackets(ask, bid, point)
            self.iteration_state = "active"
            return

        # STAGE 2: Check Virtual Triggers
        if self.iteration_state == "active":
            triggered_order = None
            for order in self.pending_orders:
                # BUY STOP Trigger (Ask price hits level)
                if order['type'] == 'BUY_STOP' and ask >= order['price']:
                    triggered_order = order; break
                # SELL STOP Trigger (Bid price hits level)
                elif order['type'] == 'SELL_STOP' and bid <= order['price']:
                    triggered_order = order; break
            
            if triggered_order:
                print(f"⚡ TRIGGER: {triggered_order['type']} at {ask}")
                await self.execute_trade_and_chain(triggered_order, ask, bid, point)

    async def place_initial_brackets(self, ask, bid, point):
        """Calculates Fixed Upper/Lower levels and locks them in."""
        user_spread_usd = self.config.get('spread', 8)
        
        # LOCK IN THE LEVELS (Ping-Pong Center)
        self.upper_level = ask + user_spread_usd
        self.lower_level = bid - user_spread_usd
        
        print(f"🎯 New Iteration: Upper Locked @ {self.upper_level:.2f} | Lower Locked @ {self.lower_level:.2f}")

        # Create Initial Orders at these levels
        self.pending_orders = [
            self.create_virtual_order('BUY_STOP', self.upper_level, point),
            self.create_virtual_order('SELL_STOP', self.lower_level, point)
        ]

    def create_virtual_order(self, order_type, price, point):
        """Creates order dict with correct Unit Conversion ($ -> Points)."""
        # Get TP/SL in DOLLARS (User Input)
        if 'BUY' in order_type:
            tp_usd = self.config.get('buy_stop_tp', 16)
            sl_usd = self.config.get('buy_stop_sl', 24)
        else:
            tp_usd = self.config.get('sell_stop_tp', 16)
            sl_usd = self.config.get('sell_stop_sl', 24)
            
        # CONVERSION: Dollars / PointValue = MT5 Points
        # Example: $10 / 0.01 = 1000 Points
        return {
            'type': order_type,
            'price': price,
            'sl': int(sl_usd / point),
            'tp': int(tp_usd / point)
        }

    async def execute_trade_and_chain(self, order, ask, bid, point):
        # 1. Get Lot Size for Current Step
        step_lots = self.config.get('step_lots', [])
        # Ensure we don't go out of bounds
        if self.current_step < len(step_lots):
            volume = float(step_lots[self.current_step])
        else:
            volume = float(step_lots[-1]) if step_lots else 0.01
            
        # 2. Execute Trade
        action = "buy" if order['type'] == 'BUY_STOP' else "sell"
        payload = {
            "action": action, 
            "symbol": self.symbol, 
            "volume": volume,
            "sl_points": int(order['sl']), 
            "tp_points": int(order['tp']),
            "comment": f"Step-{self.current_step}"
        }
        
        try:
            response = requests.post(f"{self.mt5_bridge_url}/execute_signal", json=payload, timeout=2)
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Trade OPENED: {data.get('order_id')} | Step {self.current_step} | Lot {volume}")
                self.positions.append(data)
                self.current_step += 1
                
                # 3. PING-PONG LOGIC
                # Clear pending orders
                self.pending_orders.clear()
                
                max_pos = self.config.get('max_positions', 5)
                
                if self.current_step < max_pos:
                    if action == "buy":
                        # At Upper Level -> Place SELL STOP at Lower Level
                        self.pending_orders.append(
                            self.create_virtual_order('SELL_STOP', self.lower_level, point)
                        )
                        print(f"   🏓 Ping: Waiting for Price to Drop to {self.lower_level:.2f}")
                        
                    else: # action == "sell"
                        # At Lower Level -> Place BUY STOP at Upper Level
                        self.pending_orders.append(
                            self.create_virtual_order('BUY_STOP', self.upper_level, point)
                        )
                        print(f"   🏓 Pong: Waiting for Price to Rise to {self.upper_level:.2f}")
                else:
                    print("🛑 Max positions reached. Waiting for TP or SL...")
                    self.iteration_state = "max_cap_reached"
                    
            else:
                print(f"❌ Execution Failed: {response.text}")
        except Exception as e:
            print(f"❌ Bridge Connection Error: {e}")

    async def check_stopping_conditions(self):
        """Checks for TP/SL hits via recent deals."""
        try:
            # 1. Get Recent Deals (last 10 seconds)
            res = requests.get(f"{self.mt5_bridge_url}/recent_deals?seconds=10", timeout=1)
            if res.status_code != 200: return False
            
            deals = res.json()
            if not deals: return False
            
            tp_hit = False
            sl_hit = False
            
            for deal in deals:
                if deal['profit'] > 0:
                    tp_hit = True
                elif deal['profit'] < 0:
                    sl_hit = True
            
            # LOGIC: If TP or SL is hit, close everything and restart iteration
            if tp_hit:
                print("🎉 Take Profit Hit! Resetting Iteration...")
                await self.close_all_and_restart()
                return True
                
            if sl_hit:
                print("💀 Stop Loss Hit! Resetting Iteration...")
                await self.close_all_and_restart()
                return True

            # Max Time Check
            max_mins = self.config.get('max_runtime_minutes', 0)
            if max_mins > 0:
                elapsed = (time.time() - self.start_time) / 60
                if elapsed >= max_mins:
                    print("⏰ Time Limit Reached. Stopping Bot.")
                    await self.stop_bot_completely()
                    return True

        except Exception as e:
            print(f"Error checking stopping conditions: {e}")
            
        return False

    async def close_all_and_restart(self):
        """Closes all trades, resets state, and starts a fresh grid."""
        try:
            requests.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
        except: pass
        
        self.reset_state()
        print("🔄 System Reset. Calculating new levels on next tick...")

    async def stop_bot_completely(self):
        """Closes trades and STOPS the bot."""
        try:
            requests.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
        except: pass
        
        await self.stop()

    def update_positions(self):
        # Sync position count from bridge if needed
        pass

    def get_account_balance(self):
        try:
            res = requests.get(f"{self.mt5_bridge_url}/account_info", timeout=1)
            return res.json().get('balance', 0)
        except: return 0

    def get_status(self):
        return {
            "running": self.running,
            "symbol": self.symbol,
            "current_price": self.cmp,
            "positions_count": self.current_step, 
            "pending_orders_count": len(self.pending_orders),
            "config": self.config,
            "iteration": self.iteration,
            "state": self.iteration_state
        }

    async def stop(self):
        self.running = False
        self.pending_orders = []
        print("🛑 Strategy Engine Stopped.")