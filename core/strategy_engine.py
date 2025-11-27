import asyncio
import time
from typing import Dict, List, Optional
from core.deriv_client import DerivClient

class GridStrategy:
    def __init__(self, client: DerivClient, config_manager, symbol="R_75"):
        self.client = client
        self.config_manager = config_manager
        self.symbol = symbol
        self.cmp = None
        self.pending_orders = [] 
        self.positions = [] 
        self.running = False

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start(self):
        self.running = True
        self.start_time = time.time()
        try:
            self.initial_balance = await self.client.get_balance()
            print(f"Starting strategy on {self.symbol}. Initial Balance: {self.initial_balance}")
        except Exception as e:
            print(f"Error getting balance: {e}")
            self.initial_balance = 0

        # Subscribe to ticks
        source_tick = await self.client.subscribe_ticks(self.symbol)
        
        source_tick.subscribe(lambda tick: asyncio.create_task(self.on_tick_callback(tick)))
        
        # Keep the loop alive and monitor risk/scheduler
        while self.running:
            await asyncio.sleep(5) # Check every 5 seconds
            
            # 1. Scheduler Check
            max_runtime = self.config.get('max_runtime_minutes', 0)
            if max_runtime > 0:
                elapsed = (time.time() - self.start_time) / 60
                if elapsed >= max_runtime:
                    print(f"Scheduler: Max runtime of {max_runtime}m reached. Stopping bot.")
                    await self.stop()
                    break

            # 2. Drawdown Kill-Switch Check
            max_dd = self.config.get('max_drawdown_usd', 0)
            if max_dd > 0 and self.initial_balance > 0:
                try:
                    current_balance = await self.client.get_balance()
                    drawdown = self.initial_balance - current_balance
                    if drawdown >= max_dd:
                        print(f"Risk: Max drawdown of ${max_dd} hit (Current DD: ${drawdown:.2f}). Stopping bot.")
                        await self.stop()
                        break
                except Exception as e:
                    print(f"Error checking balance for DD: {e}")


    async def on_tick_callback(self, tick):
        # The tick data structure might be nested
        if 'tick' in tick:
            price = tick['tick']['quote']
            await self.on_tick(price)

    async def on_tick(self, price):
        self.cmp = price
        # print(f"Tick: {price}")
        
        # 1. Check Pending Orders (Simulated)
        for order in self.pending_orders[:]:
            if order['type'] == 'BUY_STOP' and price >= order['price']:
                await self.execute_order(order, price)
            elif order['type'] == 'SELL_STOP' and price <= order['price']:
                await self.execute_order(order, price)

        # 2. Place Initial Orders if Empty
        if not self.positions and not self.pending_orders:
            await self.place_initial_grid(price)

    async def place_initial_grid(self, price):
        print(f"Placing initial grid at {price}")
        spread = self.config['spread']
        tp_dist = 16 # Fixed TP distance
        sl_dist = 24 # Fixed SL distance
        
        buy_stop = {
            'type': 'BUY_STOP',
            'price': price + spread,
            'tp': price + spread + tp_dist,
            'sl': price + spread - sl_dist
        }
        sell_stop = {
            'type': 'SELL_STOP',
            'price': price - spread,
            'tp': price - spread - tp_dist,
            'sl': price - spread + sl_dist
        }
        self.pending_orders.extend([buy_stop, sell_stop])
        print(f"Pending Orders: {self.pending_orders}")

    async def execute_order(self, order, price):
        print(f"Executing {order['type']} at {price}")
        # Remove from pending
        self.pending_orders.remove(order)
        
        # Cancel opposite pending
        opposite_type = 'SELL_STOP' if order['type'] == 'BUY_STOP' else 'BUY_STOP'
        self.pending_orders = [o for o in self.pending_orders if o['type'] != opposite_type]
        
        # Execute Trade (Market Order via API)
        # TODO: Implement actual API call to buy/sell
        # trade_result = await self.client.buy_contract(...) 
        
        # Add to positions (Simulated for now)
        self.positions.append(order)
        
        # Place NEW opposite pending
        spread = self.config['spread']
        tp_dist = 16 # Fixed TP distance
        sl_dist = 24 # Fixed SL distance
        max_positions = self.config['max_positions']
        
        new_opposite_price = price - spread if order['type'] == 'BUY_STOP' else price + spread
        new_opposite = {
            'type': opposite_type,
            'price': new_opposite_price,
            'tp': new_opposite_price - tp_dist if opposite_type == 'SELL_STOP' else new_opposite_price + tp_dist,
            'sl': new_opposite_price + sl_dist if opposite_type == 'SELL_STOP' else new_opposite_price - sl_dist
        }
        
        if len(self.positions) < max_positions:
             self.pending_orders.append(new_opposite)
             print(f"Placed new opposite {opposite_type} at {new_opposite_price}")

    async def stop(self):
        self.running = False

    def get_status(self):
        return {
            "running": self.running,
            "symbol": self.symbol,
            "current_price": self.cmp,
            "positions_count": len(self.positions),
            "pending_orders_count": len(self.pending_orders),
            "config": self.config
        }
