import asyncio
import time
import json
import os
import MetaTrader5 as mt5
import aiohttp

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.running = False
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        
        # --- IMMUTABLE GRID ANCHORS ---
        self.anchor_center_bid = None 
        self.anchor_center_ask = None
        self.anchor_top_ask = None
        self.anchor_bottom_bid = None
        
        # --- State Memory ---
        self.buy_trigger_name = None   
        self.sell_trigger_name = None
        self.active_upper_level = None
        self.active_lower_level = None
        
        # --- General State ---
        self.current_step = 0
        self.iteration = 1
        self.is_resetting = False 
        self.reset_timestamp = 0
<<<<<<< HEAD
        
        # --- Race Condition Lock ---
=======
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
        self.is_busy = False 
        
        # --- UI Data ---
        self.current_price = 0.0
        self.open_positions = 0 
        self.start_time = 0
        self.last_pos_count = 0
        
        self.max_slippage = 2.0 
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
        self.session = aiohttp.ClientSession()
        self.start_time = time.time()
        
        # Ensure symbol is selected
        self.symbol = self.config.get('symbol', 'FX Vol 20')
<<<<<<< HEAD
        if not mt5.symbol_select(self.symbol, True):
             print(f"❌ Failed to select {self.symbol}")

        self.cancel_all_orders_direct()
        
        real_positions = self.get_real_positions_count()
        if real_positions == 0:
            self.reset_cycle()
        else:
            print(f"⚠️ Resuming existing cycle ({real_positions} positions)...")
            self.last_pos_count = real_positions
=======
        mt5.symbol_select(self.symbol, True)
        
        self.cancel_all_orders_direct()
        
        if self.get_real_positions_count() == 0:
            self.reset_cycle()
        else:
            print("⚠️ Resuming existing cycle from state...")
            self.last_pos_count = self.get_real_positions_count()
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95

        print(f"✅ Strategy Started: {self.symbol}")

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
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})

    def close_all_direct(self):
        self.cancel_all_orders_direct()
        positions = mt5.positions_get(symbol=self.symbol)
        if positions:
            for pos in positions:
                type_op = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(self.symbol)
                price = tick.bid if type_op == mt5.ORDER_TYPE_SELL else tick.ask
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "position": pos.ticket,
                    "volume": pos.volume,
                    "type": type_op,
                    "price": price,
                    "deviation": 50
                })

    def reset_cycle(self):
        self.anchor_center_bid = None
        self.anchor_top_ask = None
        self.anchor_bottom_bid = None
        self.buy_trigger_name = None
        self.sell_trigger_name = None
        self.active_upper_level = None
        self.active_lower_level = None
        self.current_step = 0
        self.is_resetting = False
<<<<<<< HEAD
        self.is_busy = False 
=======
        self.is_busy = False
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
        self.save_state()
        print(f"🔄 Cycle Reset: Waiting for new Anchor (Iteration {self.iteration})...")

    async def on_external_tick(self, tick_data):
        if not self.running: return

        # SYMBOL CHECK
        cfg_symbol = self.config.get('symbol')
        if cfg_symbol and cfg_symbol != self.symbol:
            print(f"🔀 Switching Symbol: {self.symbol} -> {cfg_symbol}")
            self.close_all_direct()
            self.symbol = cfg_symbol
            mt5.symbol_select(self.symbol, True)
            self.is_resetting = True
            return

        ask = float(tick_data['ask'])
        bid = float(tick_data['bid'])
        self.current_price = ask 
        self.open_positions = tick_data.get('positions_count', 0)
        
        # 1. NUCLEAR RESET
        if self.open_positions < self.last_pos_count and not self.is_resetting and self.current_step > 0:
            print(f"🚨 POSITION DROP DETECTED. NUCLEAR RESET.")
            self.close_all_direct()
            self.is_resetting = True
            self.reset_timestamp = time.time()
            self.last_pos_count = self.open_positions
            return

        self.last_pos_count = self.open_positions

        # 2. Reset Handler
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

<<<<<<< HEAD
        # 3. Initialize Grid
        if self.anchor_center_bid is None:
=======
        # 3. Initialize Grid (Anchor)
        if (self.anchor_center_ask is None or 
            self.anchor_center_bid is None or 
            self.anchor_top_ask is None or 
            self.anchor_bottom_bid is None):
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
            self.init_immutable_grid(ask, bid)
            return

        # 4. Check Limits
        max_pos = int(self.config.get('max_positions', 5))
        if self.current_step >= max_pos: return 
        if self.is_time_up(): return
        if self.is_busy: return 

<<<<<<< HEAD
        # 5. SNIPER LOGIC (REVERTED: No Slippage Checks)
        # Fires immediately if price crosses the trigger level.
        
        if self.buy_trigger_name == "top":
            if ask >= self.anchor_top_ask:
                print(f"⚡ SNIPER: Hit Top (Ask {ask})")
                self.execute_market_order("buy", ask)
        elif self.buy_trigger_name == "center":
            if ask >= self.anchor_center_ask:
=======
        # --- 4.5 RE-ANCHOR LOGIC (Runaway Price Catch) ---
        # If we are stuck at Step 0 (First Trade) and price moved 3x spread away.
        if self.current_step == 0:
            user_spread = float(self.config.get('spread', 6.0))
            dist_3x = user_spread * 3.0
            
            # Check Upper Runaway
            if ask >= (self.anchor_center_ask + dist_3x):
                print(f"🚀 Price Runaway Detected (UP). Re-Anchoring at {ask}")
                self.init_immutable_grid(ask, bid)
                return 

            # Check Lower Runaway
            elif bid <= (self.anchor_center_bid - dist_3x):
                print(f"📉 Price Runaway Detected (DOWN). Re-Anchoring at {bid}")
                self.init_immutable_grid(ask, bid)
                return

        # 5. SNIPER LOGIC (Price Banding for Slippage)
        
        if self.buy_trigger_name == "top":
            if self.anchor_top_ask <= ask <= (self.anchor_top_ask + self.max_slippage):
                print(f"⚡ SNIPER: Hit Top (Ask {ask})")
                self.execute_market_order("buy", ask)
        elif self.buy_trigger_name == "center":
            if self.anchor_center_ask <= ask <= (self.anchor_center_ask + self.max_slippage):
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
                print(f"⚡ SNIPER: Hit Center (Ask {ask})")
                self.execute_market_order("buy", ask)

        if self.sell_trigger_name == "bottom":
<<<<<<< HEAD
            if bid <= self.anchor_bottom_bid:
                print(f"⚡ SNIPER: Hit Bottom (Bid {bid})")
                self.execute_market_order("sell", bid)
        elif self.sell_trigger_name == "center":
            if bid <= self.anchor_center_bid:
=======
            if (self.anchor_bottom_bid - self.max_slippage) <= bid <= self.anchor_bottom_bid:
                print(f"⚡ SNIPER: Hit Bottom (Bid {bid})")
                self.execute_market_order("sell", bid)
        elif self.sell_trigger_name == "center":
            if (self.anchor_center_bid - self.max_slippage) <= bid <= self.anchor_center_bid:
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
                print(f"⚡ SNIPER: Hit Center (Bid {bid})")
                self.execute_market_order("sell", bid)

    def is_time_up(self):
        max_mins = int(self.config.get('max_runtime_minutes', 0))
        if max_mins == 0: return False
        return (time.time() - self.start_time) / 60 > max_mins

    def init_immutable_grid(self, ask, bid):
        # GOLDEN FORMULA: Offset = UserInput - BrokerSpread
        user_spread = float(self.config.get('spread', 6.0))
        broker_spread = ask - bid
        
<<<<<<< HEAD
        # Ensure offset is positive
=======
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
        offset = max(user_spread - broker_spread, 0.1)
        
        self.anchor_center_ask = ask
        self.anchor_center_bid = bid
        
        self.anchor_top_ask = ask + offset
        self.anchor_bottom_bid = bid - offset
        
        self.buy_trigger_name = "top"
        self.sell_trigger_name = "bottom"
        
<<<<<<< HEAD
        print(f"⚓ ANCHOR ({self.symbol}) Set. Offset: {offset:.3f}")
        print(f"   Top (Ask): {self.anchor_top_ask:.5f}")
        print(f"   Bottom (Bid): {self.anchor_bottom_bid:.5f}")
=======
        print(f"⚓ ANCHOR SET ({self.symbol})")
        print(f"   Center Ask: {self.anchor_center_ask:.5f}")
        print(f"   Top Trigger: {self.anchor_top_ask:.5f}")
        print(f"   Bottom Trigger: {self.anchor_bottom_bid:.5f}")
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
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
        
<<<<<<< HEAD
        # --- TWO-LINE SL/TP LOGIC ---
        # 1. Determine Levels (Lock if first, Reuse if exists)
        if self.active_upper_level is not None and self.active_lower_level is not None:
            # Reuse existing levels
            upper = self.active_upper_level
            lower = self.active_lower_level
        else:
            # First Order: Calculate and Lock
            sl_cfg = float(self.config.get(f'{direction}_stop_sl', 0))
            tp_cfg = float(self.config.get(f'{direction}_stop_tp', 0))
=======
        sl_cfg = float(self.config.get(f'{direction}_stop_sl', 0))
        tp_cfg = float(self.config.get(f'{direction}_stop_tp', 0))
        
        sl = 0.0
        if sl_cfg > 0:
            dist = max(sl_cfg, min_dist)
            sl = price - dist if direction == "buy" else price + dist
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
            
            # Ensure valid distances
            dist_sl = max(sl_cfg, min_dist) if sl_cfg > 0 else min_dist
            dist_tp = max(tp_cfg, min_dist) if tp_cfg > 0 else min_dist
            
            if direction == "buy":
                # Buy: TP is Upper, SL is Lower
                upper = price + dist_tp
                lower = price - dist_sl
            else:
                # Sell: SL is Upper, TP is Lower
                upper = price + dist_sl
                lower = price - dist_tp
                
            self.active_upper_level = upper
            self.active_lower_level = lower
            self.save_state()
            print(f"🔒 LOCKED LEVELS: Upper={upper:.5f}, Lower={lower:.5f}")

        # 2. Assign based on direction
        if direction == "buy":
            tp = upper
            sl = lower
        else:
            sl = upper
            tp = lower

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
            "type_filling": mt5.ORDER_FILLING_FOK,
<<<<<<< HEAD
            "deviation": 50
=======
            "deviation": 5
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
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
            "active_upper_level": self.active_upper_level,
            "active_lower_level": self.active_lower_level,
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
                    if state.get("symbol") == self.symbol:
                        self.anchor_center_ask = state.get("anchor_center_ask")
                        self.anchor_center_bid = state.get("anchor_center_bid")
                        self.anchor_top_ask = state.get("anchor_top_ask")
                        self.anchor_bottom_bid = state.get("anchor_bottom_bid")
                        self.buy_trigger_name = state.get("buy_trigger_name")
                        self.sell_trigger_name = state.get("sell_trigger_name")
<<<<<<< HEAD
                        self.active_upper_level = state.get("active_upper_level")
                        self.active_lower_level = state.get("active_lower_level")
=======
>>>>>>> 09e715ff5b7021e7ef175daaff8f4986af86ac95
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
            "anchor": self.anchor_center_ask, 
            "next_buy": self.buy_trigger_name,
            "next_sell": self.sell_trigger_name
        }