import asyncio
import time
import requests
import os
from typing import Dict, List, Optional

class GridStrategy:
    def __init__(self, config_manager, symbol=None):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'Volatility 20 Index')
        self.cmp = None
        self.pending_orders = []
        self.positions = []
        self.running = False
        self.iteration = 0
        self.iteration_state = "idle"
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        
        # Load risk params
        self.risk_percent = float(self.config_manager.get_config().get('risk_percent', 0.5))

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        """
        Passive ticker. In this architecture, ticks are pushed to us via on_external_tick.
        We just verify we are ready.
        """
        print(f"Strategy ready to receive ticks for {self.symbol}")

    async def start(self):
        self.running = True
        self.start_time = time.time()
        self.iteration = 0
        print(f"Starting Grid Strategy on {self.symbol} (Bridge Mode)")
        asyncio.create_task(self.main_loop())

    async def main_loop(self):
        while self.running:
            await asyncio.sleep(1)
            # Logic that doesn't depend on ticks (e.g. time checks) can go here.
            
            # Simple cleanup of closed positions (mock logic since we don't get callbacks yet)
            # In a real full bridge, we'd poll the bridge for open positions.
            # For now, we assume positions are managed by SL/TP on the MT5 side.
            pass

    async def on_external_tick(self, tick_data):
        """Called by the API Server when a tick is received from the Bridge."""
        if not self.running:
            return

        try:
            # tick_data: {'symbol': 'FX20', 'bid': 1.23, 'ask': 1.24, 'time': ...}
            if tick_data['symbol'] != self.symbol:
                return

            # Use ASK for Buy triggers, BID for Sell triggers? 
            # For simplicity, use mid or just ask for now as 'current price'
            price = tick_data['ask'] 
            point = tick_data.get('point')
            self.cmp = price
            
            await self.process_strategy(price, point)
        except Exception as e:
            print(f"Error processing tick: {e}")

    async def process_strategy(self, price, point=None):
        # STAGE 1: Place initial grid if idle
        if self.iteration_state == "idle" and not self.pending_orders and not self.positions:
            await self.place_initial_grid(price, point)
            self.iteration_state = "building"
            return

        # STAGE 2: Check pending orders for triggers
        if self.iteration_state == "building":
            triggered_order = None
            
            # Check for triggers
            for order in self.pending_orders:
                if (order['type'] == 'BUY_STOP' and price >= order['price']) or \
                   (order['type'] == 'SELL_STOP' and price <= order['price']):
                    triggered_order = order
                    break
            
            if triggered_order:
                self.pending_orders.clear()
                print(f"⚡ Triggered {triggered_order['type']} at {price:.2f}")
                await self.execute_trade_logic(triggered_order, price, point)

    async def place_initial_grid(self, price, point=None):
        spread_points = self.config.get('spread', 100)
        
        # Calculate Spread in Price terms
        # If we have 'point', use it. Otherwise guess.
        if point:
            spread_price = spread_points * point
        else:
            # Fallback heuristic if point is missing (shouldn't happen with new bridge)
            spread_price = spread_points * 0.00001 if "FX" in self.symbol else spread_points * 0.01
            print(f"⚠️ Warning: 'point' not received. Guessing spread_price={spread_price}")

        tp_dist = 160 # Points
        sl_dist = 240 # Points
        
        buy_stop_price = price + spread_price
        sell_stop_price = price - spread_price
        
        buy_stop = {
            'type': 'BUY_STOP',
            'price': buy_stop_price,
            'sl_points': sl_dist,
            'tp_points': tp_dist
        }
        
        sell_stop = {
            'type': 'SELL_STOP',
            'price': sell_stop_price,
            'sl_points': sl_dist,
            'tp_points': tp_dist
        }
        
        self.pending_orders = [buy_stop, sell_stop]
        print(f"🎯 Iteration {self.iteration + 1}: Price={price:.5f}, Spread={spread_points}pts ({spread_price:.5f})")
        print(f"   Waiting for BUY >= {buy_stop_price:.5f} or SELL <= {sell_stop_price:.5f}")

    async def execute_trade_logic(self, order, current_price, point=None):
        action = "buy" if order['type'] == 'BUY_STOP' else "sell"
        
        # Calculate Lot Size (Mock or Config)
        volume = self.config.get('lot_size', 0.01)
        
        payload = {
            "action": action,
            "symbol": self.symbol,
            "volume": volume,
            "sl_points": int(order['sl_points']),
            "tp_points": int(order['tp_points']),
            "comment": f"Grid Itr {self.iteration}"
        }
        
        try:
            # Send signal to bridge
            # We use requests.post (sync) inside async? Better to use aiohttp or run_in_executor.
            # For simplicity in this migration, we'll use requests but it blocks the loop briefly.
            # Given low frequency, it's acceptable.
            response = requests.post(f"{self.mt5_bridge_url}/execute_signal", json=payload, timeout=2)
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Trade Executed via Bridge: {data}")
                
                # Record position
                self.positions.append({
                    "id": data.get("order_id"),
                    "type": order['type'],
                    "entry_price": data.get("price")
                })
                
                # Place Bracket (Next Grid)
                # Logic: If we opened a trade, we place new stops around THIS trade?
                # The previous logic was: "Place NEW BRACKET around the FILLED PRICE"
                # We can do that.
                
                await self.place_next_bracket(data.get("price"), point)
                
            else:
                print(f"❌ Bridge Error: {response.text}")
                self.iteration_state = "idle"
                
        except Exception as e:
            print(f"❌ Failed to contact Bridge: {e}")
            self.iteration_state = "idle"

    async def place_next_bracket(self, anchor_price, point=None):
        if len(self.positions) >= self.config.get('max_positions', 5):
            print("Max positions reached. Waiting for clear.")
            self.iteration_state = "waiting_close"
            return

        spread_points = self.config.get('spread', 100)
        
        # Calculate Spread in Price terms
        if point:
            spread_price = spread_points * point
        else:
             # Fallback
            spread_price = spread_points * 0.00001 if "FX" in self.symbol else spread_points * 0.01

        tp_dist = 160
        sl_dist = 240
        
        buy_stop_price = anchor_price + spread_price
        sell_stop_price = anchor_price - spread_price
        
        self.pending_orders = [
            {'type': 'BUY_STOP', 'price': buy_stop_price, 'sl_points': sl_dist, 'tp_points': tp_dist},
            {'type': 'SELL_STOP', 'price': sell_stop_price, 'sl_points': sl_dist, 'tp_points': tp_dist}
        ]
        print(f"📍 New Bracket Placed around {anchor_price:.5f}")

    def get_status(self):
        # Try to fetch real-time data from bridge
        bridge_data = {}
        try:
            res = requests.get(f"{self.mt5_bridge_url}/account_info", timeout=0.5)
            if res.status_code == 200:
                bridge_data = res.json()
        except:
            pass # Bridge might be down

        return {
            "running": self.running,
            "symbol": self.symbol,
            "current_price": bridge_data.get("current_price", self.cmp),
            "positions_count": bridge_data.get("positions_count", len(self.positions)),
            "pending_orders_count": bridge_data.get("pending_orders_count", len(self.pending_orders)),
            "config": self.config,
            "iteration": self.iteration,
            "state": self.iteration_state
        }

    async def stop(self):
        self.running = False
        print("Strategy Stopped")

