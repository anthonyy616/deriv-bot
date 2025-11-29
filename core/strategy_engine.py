import asyncio
import time
import requests
import os
from typing import Dict, List, Optional

class GridStrategy:
    def __init__(self, config_manager, symbol=None):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX20')
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
            self.cmp = price
            
            await self.process_strategy(price)
        except Exception as e:
            print(f"Error processing tick: {e}")

    async def process_strategy(self, price):
        # STAGE 1: Place initial grid if idle
        if self.iteration_state == "idle" and not self.pending_orders and not self.positions:
            await self.place_initial_grid(price)
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
                await self.execute_trade_logic(triggered_order, price)

    async def place_initial_grid(self, price):
        spread = self.config.get('spread', 100) # Points? Or Price difference? 
        # Assuming config 'spread' is in POINTS if using MT5, or Price. 
        # Let's assume Price difference for now to match previous logic.
        
        tp_dist = 160 # Points (e.g. 16 pips if 10 points/pip) - Adjust as needed
        sl_dist = 240 # Points
        
        # If the user meant "spread" as distance:
        dist = spread * 0.0001 if "FX" in self.symbol else spread # Rough heuristic
        # Actually, let's stick to the user's "points" requirement.
        # If the strategy calculates PRICE, we need to convert to POINTS for the bridge.
        # OR, we send the calculated SL/TP PRICES to the bridge?
        # The bridge expects sl_points, tp_points.
        
        # Strategy Logic:
        # Buy Stop @ Price + Spread
        # Sell Stop @ Price - Spread
        
        # We'll stick to the previous logic of calculating absolute prices for triggers,
        # but when sending to bridge, we send POINTS for SL/TP.
        
        # Previous logic:
        # buy_stop_price = price + spread
        # tp = buy_stop_price + tp_dist
        # sl = buy_stop_price - sl_dist
        
        # So TP distance = tp_dist, SL distance = sl_dist.
        # We will use these directly for the bridge.
        
        buy_stop_price = price + spread
        sell_stop_price = price - spread
        
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
        print(f"🎯 Iteration {self.iteration + 1}: Waiting for BUY@{buy_stop_price:.5f} or SELL@{sell_stop_price:.5f}")

    async def execute_trade_logic(self, order, current_price):
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
                
                await self.place_next_bracket(data.get("price"))
                
            else:
                print(f"❌ Bridge Error: {response.text}")
                self.iteration_state = "idle"
                
        except Exception as e:
            print(f"❌ Failed to contact Bridge: {e}")
            self.iteration_state = "idle"

    async def place_next_bracket(self, anchor_price):
        if len(self.positions) >= self.config.get('max_positions', 5):
            print("Max positions reached. Waiting for clear.")
            self.iteration_state = "waiting_close"
            return

        spread = self.config.get('spread', 100)
        tp_dist = 160
        sl_dist = 240
        
        buy_stop_price = anchor_price + spread
        sell_stop_price = anchor_price - spread
        
        self.pending_orders = [
            {'type': 'BUY_STOP', 'price': buy_stop_price, 'sl_points': sl_dist, 'tp_points': tp_dist},
            {'type': 'SELL_STOP', 'price': sell_stop_price, 'sl_points': sl_dist, 'tp_points': tp_dist}
        ]
        print(f"📍 New Bracket Placed around {anchor_price:.5f}")

    async def stop(self):
        self.running = False
        print("Strategy Stopped")

