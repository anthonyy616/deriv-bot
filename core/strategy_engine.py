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
        
        # Strategy State
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
                continue # If stopped or reset, skip processing

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
        """Sets the fixed Upper and Lower levels based on initial spread."""
        user_spread_usd = self.config.get('spread', 8)
        
        # Define Fixed Levels
        self.upper_level = ask + user_spread_usd
        self.lower_level = bid - user_spread_usd
        
        print(f"🎯 Initial Grid: Upper={self.upper_level:.2f} | Lower={self.lower_level:.2f}")

        # Create Virtual Orders
        self.pending_orders = [
            self.create_virtual_order('BUY_STOP', self.upper_level, point),
            self.create_virtual_order('SELL_STOP', self.lower_level, point)
        ]

    def create_virtual_order(self, order_type, price, point):
        # Get TP/SL from Config (Split Inputs)
        if 'BUY' in order_type:
            tp_usd = self.config.get('buy_stop_tp', 16)
            sl_usd = self.config.get('buy_stop_sl', 24)
        else:
            tp_usd = self.config.get('sell_stop_tp', 16)
            sl_usd = self.config.get('sell_stop_sl', 24)
            
        return {
            'type': order_type,
            'price': price,
            'sl': int(sl_usd / point),
            'tp': int(tp_usd / point)
        }

    async def execute_trade_and_chain(self, order, ask, bid, point):
        # 1. Get Lot Size for Current Step
        step_lots = self.config.get('step_lots', [])
        if self.current_step < len(step_lots):
            volume = step_lots[self.current_step]
        else:
            volume = step_lots[-1] if step_lots else 0.01
            
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
                print(f"✅ Trade OPENED: {data.get('order_id')} | Step {self.current_step}")
                self.positions.append(data)
                self.current_step += 1
                
                # 3. Ping-Pong Logic (Update Pending Orders)
                self.pending_orders.clear()
                
                if self.current_step < self.config.get('max_positions', 5):
                    if action == "buy":
                        # If Buy triggered, place Sell Stop at Lower Level
                        self.pending_orders.append(
                            self.create_virtual_order('SELL_STOP', self.lower_level, point)
                        )
                        print(f"   🏓 Ping: Placed SELL STOP at {self.lower_level:.2f}")
                    else:
                        # If Sell triggered, place Buy Stop at Upper Level
                        self.pending_orders.append(
                            self.create_virtual_order('BUY_STOP', self.upper_level, point)
                        )
                        print(f"   🏓 Pong: Placed BUY STOP at {self.upper_level:.2f}")
                else:
                    print("🛑 Max positions reached. Waiting for outcome.")
                    
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
                # Check if this deal belongs to our session (optional, but good)
                # For now, assume all deals on this symbol are relevant
                if deal['profit'] > 0:
                    tp_hit = True
                elif deal['profit'] < 0:
                    sl_hit = True
            
            if tp_hit:
                print("🎉 Take Profit Hit! Restarting Strategy...")
                await self.close_all_and_restart()
                return True
                
            if sl_hit:
                print("💀 Stop Loss Hit!")
                # Check Max Positions Rule
                max_pos = self.config.get('max_positions', 5)
                # Note: self.positions might not be perfectly synced, but current_step is
                if self.current_step >= max_pos:
                     print("   And Max Positions Reached. STOPPING BOT.")
                     await self.stop_bot_completely()
                     return True
                else:
                     print("   Stopping Bot (Standard SL Rule).")
                     await self.stop_bot_completely()
                     return True

        except Exception as e:
            print(f"Error checking stopping conditions: {e}")
            
        return False

    async def close_all_and_restart(self):
        try:
            requests.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
        except: pass
        
        self.reset_state()
        print("🔄 Strategy Reset. Waiting for next tick...")

    async def stop_bot_completely(self):
        try:
            requests.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
        except: pass
        
        await self.stop()

    def update_positions(self):
        # Sync position count from bridge
        try:
            res = requests.get(f"{self.mt5_bridge_url}/account_info", timeout=1)
            if res.status_code == 200:
                data = res.json()
                # We can update self.positions list if needed, but count is enough for UI
                pass
        except: pass

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
            "positions_count": self.current_step, # Use step as proxy for open positions count
            "pending_orders_count": len(self.pending_orders),
            "config": self.config,
            "iteration": self.iteration,
            "state": self.iteration_state
        }

    async def stop(self):
        self.running = False
        self.pending_orders = []
        print("🛑 Strategy Engine Stopped.")