import asyncio
import time
import requests
import os

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
        
    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        print(f"Strategy listening for ticks on: {self.symbol}")

    async def start(self):
        self.running = True
        self.start_time = time.time()
        self.iteration = 0
        self.pending_orders = []
        self.positions = []
        self.iteration_state = "idle"
        
        self.initial_balance = self.get_account_balance()
        print(f"✅ Strategy Started. Symbol: {self.symbol} | Start Balance: {self.initial_balance}")
        
        asyncio.create_task(self.main_loop())

    async def main_loop(self):
        while self.running:
            await asyncio.sleep(1)
            if self.check_stopping_conditions():
                await self.stop()
                break

    async def on_external_tick(self, tick_data):
        if not self.running: return
        if tick_data['symbol'] != self.symbol: return

        # 1. Extract Data
        ask = tick_data['ask']
        bid = tick_data['bid']
        point = tick_data.get('point', 0.01) # Default to 0.01 if missing
        
        self.cmp = ask
        
        # 2. Process Strategy with full context
        await self.process_strategy(ask, bid, point)

    async def process_strategy(self, ask, bid, point):
        # STAGE 1: Place Initial Virtual Grid
        if self.iteration_state == "idle" and not self.pending_orders and not self.positions:
            await self.place_brackets(ask, bid, point, is_initial=True)
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
                self.pending_orders.clear()
                print(f"⚡ TRIGGER: {triggered_order['type']} at {ask}")
                await self.execute_trade_and_chain(triggered_order, ask, bid, point)

    async def place_brackets(self, ask, bid, point, is_initial=False):
        """Calculates distances using User Pips ($1). NO FORCED SPREAD LOGIC."""
        
        # 1. Get User Configuration ($)
        user_spread_usd = self.config.get('spread', 8)
        
        # 2. DIRECTLY use the user's spread (No 2x Rule)
        # We trust the user knows what they are doing.
        final_spread_usd = user_spread_usd
        
        if is_initial:
            print(f"🎯 Grid Placed. Gap: ${final_spread_usd:.2f} (User Defined)")

        # 3. Calculate Levels (Price)
        buy_stop_price = ask + final_spread_usd
        sell_stop_price = bid - final_spread_usd
        
        # 4. Calculate SL/TP Distances (Convert $ -> Points)
        sl_usd = self.config.get('sl_dist', 24)
        tp_usd = self.config.get('tp_dist', 16)
        
        sl_points = int(sl_usd / point)
        tp_points = int(tp_usd / point)
        
        self.pending_orders = [
            {'type': 'BUY_STOP', 'price': buy_stop_price, 'sl': sl_points, 'tp': tp_points},
            {'type': 'SELL_STOP', 'price': sell_stop_price, 'sl': sl_points, 'tp': tp_points}
        ]
        
        if is_initial:
            print(f"   Buy Stop: {buy_stop_price:.2f} | Sell Stop: {sell_stop_price:.2f}")

    async def execute_trade_and_chain(self, order, ask, bid, point):
        action = "buy" if order['type'] == 'BUY_STOP' else "sell"
        volume = self.config.get('lot_size', 0.01)
        
        # Payload uses POINTS for SL/TP
        payload = {
            "action": action, 
            "symbol": self.symbol, 
            "volume": volume,
            "sl_points": int(order['sl']), 
            "tp_points": int(order['tp']),
            "comment": f"Grid-Itr-{self.iteration}"
        }
        
        try:
            response = requests.post(f"{self.mt5_bridge_url}/execute_signal", json=payload, timeout=2)
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Trade OPENED: {data.get('order_id')}")
                self.positions.append(data)
                self.iteration += 1
                
                if len(self.positions) < self.config.get('max_positions', 5):
                    # Place next bracket around current price
                    await self.place_brackets(ask, bid, point)
                else:
                    print("🛑 Max positions reached.")
                    self.iteration_state = "max_cap_reached"
            else:
                print(f"❌ Execution Failed: {response.text}")
                self.iteration_state = "idle"
        except Exception as e:
            print(f"❌ Bridge Connection Error: {e}")

    def check_stopping_conditions(self):
        # Time Check
        max_mins = self.config.get('max_runtime_minutes', 0)
        if max_mins > 0:
            elapsed = (time.time() - self.start_time) / 60
            if elapsed >= max_mins:
                print(f"⏰ Time Limit Reached. Stopping.")
                return True

        # Drawdown Check
        max_dd = self.config.get('max_drawdown_usd', 0)
        if max_dd > 0:
            current_equity = self.get_account_equity()
            if current_equity > 0:
                drawdown = self.initial_balance - current_equity
                if drawdown >= max_dd:
                    print(f"📉 Max Drawdown Reached (-${drawdown:.2f}). Stopping.")
                    return True
        return False

    def get_account_balance(self):
        try:
            res = requests.get(f"{self.mt5_bridge_url}/account_info", timeout=1)
            return res.json().get('balance', 0)
        except: return 0

    def get_account_equity(self):
        try:
            res = requests.get(f"{self.mt5_bridge_url}/account_info", timeout=1)
            return res.json().get('equity', 0)
        except: return 0

    def get_status(self):
        return {
            "running": self.running,
            "symbol": self.symbol,
            "current_price": self.cmp,
            "positions_count": len(self.positions),
            "pending_orders_count": len(self.pending_orders),
            "config": self.config,
            "iteration": self.iteration,
            "state": self.iteration_state
        }

    async def stop(self):
        self.running = False
        self.pending_orders = []
        print("🛑 Strategy Engine Stopped.")