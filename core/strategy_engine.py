import asyncio
import time
import json
import os
import MetaTrader5 as mt5

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        # Initial symbol, will be updated dynamically
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.running = False
        
        # --- IMMUTABLE GRID ANCHORS ---
        self.anchor_center_bid = None 
        self.anchor_center_ask = None
        self.anchor_top_ask = None
        self.anchor_bottom_bid = None
        
        # --- State Memory ---
        self.buy_trigger_name = None   
        self.sell_trigger_name = None  
        
        # --- General State ---
        self.current_step = 0
        self.iteration = 1
        self.is_resetting = False 
        self.reset_timestamp = 0
        
        # --- Race Condition Lock ---
        self.is_busy = False 
        
        # --- UI Data ---
        self.current_price = 0.0
        self.open_positions = 0 
        self.start_time = 0
        self.last_pos_count = 0
        
        self.load_state()

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
        self.start_time = time.time()
        
        # Ensure correct symbol is selected at startup
        self.symbol = self.config.get('symbol', 'FX Vol 20')
        if not mt5.symbol_select(self.symbol, True):
             print(f"❌ Failed to select {self.symbol}")

        self.cancel_all_orders_direct()
        
        if self.get_real_positions_count() == 0:
            self.reset_cycle()
        else:
            print("⚠️ Resuming existing cycle from state...")

        print(f"✅ Monolith Strategy Started: {self.symbol}")

    async def stop(self):
        self.running = False
        self.save_state()

    def get_real_positions_count(self):
        positions = mt5.positions_get(symbol=self.symbol)
        return len(positions) if positions else 0

    def cancel_all_orders_direct(self):
        orders = mt5.orders_get(symbol=self.symbol)
        if orders:
            for order in orders:
                req = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
                mt5.order_send(req)

    def close_all_direct(self):
        self.cancel_all_orders_direct()
        positions = mt5.positions_get(symbol=self.symbol)
        if positions:
            for pos in positions:
                type_op = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(self.symbol)
                price = tick.bid if type_op == mt5.ORDER_TYPE_SELL else tick.ask
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "position": pos.ticket,
                    "volume": pos.volume,
                    "type": type_op,
                    "price": price,
                    "deviation": 50
                }
                mt5.order_send(req)

    def reset_cycle(self):
        self.anchor_center_bid = None
        self.anchor_top_ask = None
        self.anchor_bottom_bid = None
        self.buy_trigger_name = None
        self.sell_trigger_name = None
        self.current_step = 0
        self.is_resetting = False
        self.is_busy = False 
        self.save_state()
        print(f"🔄 Cycle Reset: Waiting for new Anchor (Iteration {self.iteration})...")

    async def on_external_tick(self, tick_data):
        if not self.running: return
        
        # 1. Check for Symbol Switch
        new_symbol = self.config.get('symbol')
        if new_symbol and new_symbol != self.symbol:
            print(f"🔀 Symbol Change Detected: {self.symbol} -> {new_symbol}")
            # Nuclear Reset Old Symbol
            self.close_all_direct()
            # Switch
            self.symbol = new_symbol
            mt5.symbol_select(self.symbol, True)
            # Soft Reset State
            self.last_pos_count = 0 
            self.is_resetting = True # Force clean start
            return

        ask = float(tick_data['ask'])
        bid = float(tick_data['bid'])
        self.current_price = ask 
        self.open_positions = tick_data.get('positions_count', 0)
        
        # 2. NUCLEAR RESET (Fast Path)
        if self.open_positions < self.last_pos_count and not self.is_resetting and self.current_step > 0:
            print(f"🚨 POSITION DROP ({self.last_pos_count} -> {self.open_positions}). NUCLEAR RESET.")
            self.close_all_direct()
            self.is_resetting = True
            self.reset_timestamp = time.time()
            self.last_pos_count = self.open_positions
            return

        self.last_pos_count = self.open_positions

        # 3. Reset State Handler
        if self.is_resetting:
            if self.open_positions == 0:
                if self.is_time_up():
                    print("🛑 Max Runtime Reached. Stopping.")
                    await self.stop()
                    return
                print("✅ Account Cleaned. Starting New Iteration.")
                self.iteration += 1
                self.reset_cycle()
            else:
                if time.time() - self.reset_timestamp > 2:
                    self.close_all_direct()
                    self.reset_timestamp = time.time()
            return

        # 4. Initialize Grid
        if self.anchor_center_bid is None:
            self.init_immutable_grid(ask, bid)
            return

        # 5. Check Limits
        max_pos = int(self.config.get('max_positions', 5))
        if self.current_step >= max_pos: return 
        if self.is_time_up(): return
        if self.is_busy: return 

        # 6. SNIPER LOGIC (Golden Formula)
        
        # Buy Logic
        if self.buy_trigger_name == "top":
            # If Ask price hits the calculated Top Ask Trigger
            if ask >= self.anchor_top_ask:
                print(f"⚡ SNIPER: Hit Top (Ask {ask} >= {self.anchor_top_ask})")
                self.execute_market_order("buy", ask)
        elif self.buy_trigger_name == "center":
            if ask >= self.anchor_center_ask:
                print(f"⚡ SNIPER: Hit Center (Ask {ask} >= {self.anchor_center_ask})")
                self.execute_market_order("buy", ask)

        # Sell Logic
        if self.sell_trigger_name == "bottom":
            # If Bid price hits the calculated Bottom Bid Trigger
            if bid <= self.anchor_bottom_bid:
                print(f"⚡ SNIPER: Hit Bottom (Bid {bid} <= {self.anchor_bottom_bid})")
                self.execute_market_order("sell", bid)
        elif self.sell_trigger_name == "center":
            if bid <= self.anchor_center_bid:
                print(f"⚡ SNIPER: Hit Center (Bid {bid} <= {self.anchor_center_bid})")
                self.execute_market_order("sell", bid)

    def is_time_up(self):
        max_mins = int(self.config.get('max_runtime_minutes', 0))
        if max_mins == 0: return False
        return (time.time() - self.start_time) / 60 > max_mins

    def init_immutable_grid(self, ask, bid):
        # GOLDEN FORMULA: Net Offset = UserInput - BrokerSpread
        user_spread = float(self.config.get('spread', 6.0))
        broker_spread = ask - bid
        
        # Safety: Ensure offset is at least 1 pip to prevent negative/zero grid
        offset = max(user_spread - broker_spread, 0.001)
        
        # Set Anchors based on Bid/Ask geometry
        # Center is the snapshot of current market
        self.anchor_center_ask = ask
        self.anchor_center_bid = bid
        
        # Top (Buy Trigger) is strictly OFFSET away from Center Ask
        self.anchor_top_ask = ask + offset
        
        # Bottom (Sell Trigger) is strictly OFFSET away from Center Bid
        self.anchor_bottom_bid = bid - offset
        
        # Initial State
        self.buy_trigger_name = "top"
        self.sell_trigger_name = "bottom"
        
        print(f"⚓ ANCHOR SET ({self.symbol})")
        print(f"   User Spread: {user_spread} | Broker Spread: {broker_spread:.5f}")
        print(f"   Calculated Offset: {offset:.5f}")
        print(f"   Top Ask: {self.anchor_top_ask:.5f} | Center Ask: {self.anchor_center_ask:.5f}")
        print(f"   Center Bid: {self.anchor_center_bid:.5f} | Bottom Bid: {self.anchor_bottom_bid:.5f}")
        self.save_state()

    def execute_market_order(self, direction, price):
        self.is_busy = True 
        vol = self.get_volume(self.current_step)
        print(f"🚀 FIRING {direction.upper()} | Step {self.current_step} | Lot: {vol}")
        
        self.current_step += 1 
        
        if self.send_market_request_direct(direction, vol):
            # State Transition
            if direction == "buy":
                if self.buy_trigger_name == "top":
                    self.sell_trigger_name = "center"
                    self.buy_trigger_name = None 
                elif self.buy_trigger_name == "center":
                    self.sell_trigger_name = "bottom"
                    self.buy_trigger_name = None
            elif direction == "sell":
                if self.sell_trigger_name == "bottom":
                    self.buy_trigger_name = "center"
                    self.sell_trigger_name = None
                elif self.sell_trigger_name == "center":
                    self.buy_trigger_name = "top"
                    self.sell_trigger_name = None
            self.save_state()
        else:
            print("❌ Order Failed. Rolling back.")
            self.current_step -= 1
            
        self.is_busy = False

    def send_market_request_direct(self, direction, volume):
        symbol_info = mt5.symbol_info(self.symbol)
        if not symbol_info: return False
        
        point = symbol_info.point
        min_dist = (symbol_info.trade_stops_level * point) + (5 * point)
        
        tick = mt5.symbol_info_tick(self.symbol)
        price = tick.ask if direction == "buy" else tick.bid
        type_op = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        
        sl_cfg = float(self.config.get(f'{direction}_stop_sl', 0))
        tp_cfg = float(self.config.get(f'{direction}_stop_tp', 0))
        
        sl = 0.0
        if sl_cfg > 0:
            dist = max(sl_cfg, min_dist)
            sl = price - dist if direction == "buy" else price + dist
            
        tp = 0.0
        if tp_cfg > 0:
            dist = max(tp_cfg, min_dist)
            tp = price + dist if direction == "buy" else price - dist

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": float(volume),
            "type": type_op,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": self.iteration,
            "comment": f"S{self.current_step}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK
        }
        
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"❌ Order Fail: {res.comment}")
            return False
        return True

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        if not step_lots: return 0.01
        if step < len(step_lots): return step_lots[step]
        return step_lots[-1]

    def save_state(self):
        state = {
            "symbol": self.symbol,
            "anchor_center_ask": self.anchor_center_ask,
            "anchor_center_bid": self.anchor_center_bid,
            "anchor_top_ask": self.anchor_top_ask,
            "anchor_bottom_bid": self.anchor_bottom_bid,
            "buy_trigger_name": self.buy_trigger_name,
            "sell_trigger_name": self.sell_trigger_name,
            "current_step": self.current_step,
            "iteration": self.iteration
        }
        with open("bot_state.json", "w") as f:
            json.dump(state, f)

    def load_state(self):
        if os.path.exists("bot_state.json"):
            try:
                with open("bot_state.json", "r") as f:
                    state = json.load(f)
                    # Only restore if symbol matches
                    if state.get("symbol") == self.symbol:
                        self.anchor_center_ask = state.get("anchor_center_ask")
                        self.anchor_center_bid = state.get("anchor_center_bid")
                        self.anchor_top_ask = state.get("anchor_top_ask")
                        self.anchor_bottom_bid = state.get("anchor_bottom_bid")
                        self.buy_trigger_name = state.get("buy_trigger_name")
                        self.sell_trigger_name = state.get("sell_trigger_name")
                        self.current_step = state.get("current_step", 0)
                        self.iteration = state.get("iteration", 1)
            except: pass

    def get_status(self):
        return {
            "running": self.running,
            "current_price": self.current_price,
            "open_positions": self.open_positions,
            "step": self.current_step,
            "iteration": self.iteration,
            "is_resetting": self.is_resetting,
            "anchor": self.anchor_center_ask, # Just for reference
            "next_buy": self.buy_trigger_name,
            "next_sell": self.sell_trigger_name
        }