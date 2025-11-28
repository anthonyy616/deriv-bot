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
        self.pending_orders = []  # Orders waiting to trigger
        self.positions = []  # Active positions (open contracts)
        self.running = False
        self.iteration = 0
        self.iteration_state = "idle"  # idle, building, waiting_close

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start(self):
        self.running = True
        self.start_time = time.time()
        self.iteration = 0
        
        try:
            self.initial_balance = await self.client.get_balance()
            print(f"Starting grid strategy on {self.symbol}. Initial Balance: {self.initial_balance}")
        except Exception as e:
            print(f"Error getting balance: {e}")
            self.initial_balance = 0

        # Subscribe to ticks
        source_tick = await self.client.subscribe_ticks(self.symbol)
        source_tick.subscribe(lambda tick: asyncio.create_task(self.on_tick_callback(tick)))
        
        # Start main loop
        while self.running:
            await asyncio.sleep(2)
            
            # 1. Check Scheduler
            max_runtime = self.config.get('max_runtime_minutes', 0)
            if max_runtime > 0:
                elapsed = (time.time() - self.start_time) / 60
                if elapsed >= max_runtime:
                    print(f"⏰ Scheduler: Max runtime of {max_runtime}m reached. Stopping bot.")
                    await self.stop()
                    break

            # 2. Check Drawdown
            max_dd = self.config.get('max_drawdown_usd', 0)
            if max_dd > 0 and self.initial_balance > 0:
                try:
                    current_balance = await self.client.get_balance()
                    drawdown = self.initial_balance - current_balance
                    if drawdown >= max_dd:
                        print(f"⚠️ Risk: Max drawdown of ${max_dd} hit (Current DD: ${drawdown:.2f}). Stopping bot.")
                        await self.stop()
                        break
                except Exception as e:
                    print(f"Error checking balance: {e}")

            # 3. Update open positions status
            await self.update_positions()

            # 4. Check if iteration is complete
            if self.iteration_state == "waiting_close" and len(self.positions) == 0:
                print(f"✅ Iteration {self.iteration} complete. All positions closed.")
                self.iteration += 1
                self.iteration_state = "idle"
                # Start new iteration
                if self.cmp:
                    await self.place_initial_grid(self.cmp)

    async def on_tick_callback(self, tick):
        if 'tick' in tick:
            price = tick['tick']['quote']
            await self.on_tick(price)

    async def on_tick(self, price):
        self.cmp = price
        
        # STAGE 1: Place initial grid if idle
        if self.iteration_state == "idle" and not self.pending_orders and not self.positions:
            await self.place_initial_grid(price)
            self.iteration_state = "building"
            return

        # STAGE 2: Check pending orders for triggers
        if self.iteration_state == "building":
            for order in self.pending_orders[:]:
                triggered = False
                
                if order['type'] == 'BUY_STOP' and price >= order['price']:
                    triggered = True
                elif order['type'] == 'SELL_STOP' and price <= order['price']:
                    triggered = True
                
                if triggered:
                    await self.execute_order(order, price)
                    
                    # Check if max positions reached
                    if len(self.positions) >= self.config['max_positions']:
                        print(f"📊 Max positions ({self.config['max_positions']}) reached. Waiting for closes...")
                        self.pending_orders.clear()  # Cancel all pending
                        self.iteration_state = "waiting_close"
                        break

    async def place_initial_grid(self, price):
        """PHASE 1: Place BUY_STOP and SELL_STOP at current_price ± spread"""
        spread = self.config['spread']
        tp_dist = 16  # TP distance from entry
        sl_dist = 24  # SL distance from entry
        
        buy_stop_price = price + spread
        sell_stop_price = price - spread
        
        buy_stop = {
            'type': 'BUY_STOP',
            'price': buy_stop_price,
            'tp': buy_stop_price + tp_dist,
            'sl': buy_stop_price - sl_dist
        }
        
        sell_stop = {
            'type': 'SELL_STOP',
            'price': sell_stop_price,
            'tp': sell_stop_price - tp_dist,
            'sl': sell_stop_price + sl_dist
        }
        
        self.pending_orders = [buy_stop, sell_stop]
        print(f"🎯 Iteration {self.iteration + 1} STARTED: Placed BUY_STOP at {buy_stop_price:.2f} | SELL_STOP at {sell_stop_price:.2f}")

    async def execute_order(self, order, current_price):
        """Execute the triggered order and place opposite pending"""
        print(f"⚡ Executing {order['type']} at {current_price:.2f}")
        
        # Remove from pending
        self.pending_orders.remove(order)
        
        # Cancel opposite pending order
        opposite_type = 'SELL_STOP' if order['type'] == 'BUY_STOP' else 'BUY_STOP'
        self.pending_orders = [o for o in self.pending_orders if o['type'] != opposite_type]
        
        # Execute via Deriv Multipliers API
        contract_type = "MULTUP" if order['type'] == 'BUY_STOP' else "MULTDOWN"
        amount = self.config.get('lot_size', 10)  # Use lot_size from config
        multiplier = 100  # 100x leverage for max sensitivity
        
        trade_result = await self.client.buy_multiplier(
            contract_type=contract_type,
            amount=amount,
            symbol=self.symbol,
            multiplier=multiplier,
            stop_loss=order['sl'],
            take_profit=order['tp']
        )
        
        if trade_result:
            # Add to active positions
            position = {
                'contract_id': trade_result['contract_id'],
                'type': order['type'],
                'entry_price': current_price,
                'tp': order['tp'],
                'sl': order['sl'],
                'buy_price': trade_result.get('buy_price', amount)
            }
            self.positions.append(position)
            print(f"✅ Position {len(self.positions)}/{self.config['max_positions']}: {order['type']} | TP: {order['tp']:.2f} | SL: {order['sl']:.2f}")
        else:
            print("❌ Trade failed to execute.")
            return
        
        # PHASE 2: Place NEW opposite pending order
        if len(self.positions) < self.config['max_positions']:
            spread = self.config['spread']
            tp_dist = 16
            sl_dist = 24
            
            new_opposite_price = current_price - spread if order['type'] == 'BUY_STOP' else current_price + spread
            
            new_opposite = {
                'type': opposite_type,
                'price': new_opposite_price,
                'tp': new_opposite_price - tp_dist if opposite_type == 'SELL_STOP' else new_opposite_price + tp_dist,
                'sl': new_opposite_price + sl_dist if opposite_type == 'SELL_STOP' else new_opposite_price - sl_dist
            }
            
            self.pending_orders.append(new_opposite)
            print(f"📍 Placed new {opposite_type} at {new_opposite_price:.2f}")

    async def update_positions(self):
        """Check status of open positions and remove closed ones"""
        for position in self.positions[:]:
            try:
                status = await self.client.get_contract_status(position['contract_id'])
                if status and status.get('is_sold') == 1:
                    # Position closed
                    profit = status.get('profit', 0)
                    exit_reason = "TP" if profit > 0 else "SL"
                    print(f"🔴 Position CLOSED: {position['type']} | {exit_reason} | P/L: ${profit:.2f}")
                    self.positions.remove(position)
            except Exception as e:
                print(f"Error updating position {position['contract_id']}: {e}")

    async def stop(self):
        self.running = False
        print("🛑 Strategy stopped.")

    def get_status(self):
        return {
            "running": self.running,
            "symbol": self.symbol,
            "current_price": self.cmp,
            "positions_count": len(self.positions),
            "pending_orders_count": len(self.pending_orders),
            "iteration": self.iteration,
            "iteration_state": self.iteration_state,
            "config": self.config,
            "account": getattr(self.client, 'account_info', None)
        }
