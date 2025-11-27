import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import logging
import config

class MT5Interface:
    def __init__(self, login, password, server):
        self.login = login
        self.password = password
        self.server = server
        self.connected = False

    def start(self):
        """Initializes and logs into MT5."""
        # Initialize with path if provided, otherwise default
        if config.MT5_PATH:
            init_result = mt5.initialize(config.MT5_PATH)
        else:
            init_result = mt5.initialize()

        if not init_result:
            logging.error(f"initialize() failed, error code = {mt5.last_error()}")
            return False

        authorized = mt5.login(self.login, password=self.password, server=self.server)
        if authorized:
            logging.info(f"Connected to MT5 account #{self.login}")
            self.connected = True
        else:
            logging.error(f"failed to connect at account #{self.login}, error code: {mt5.last_error()}")
            self.connected = False
            mt5.shutdown()
        
        return self.connected

    def shutdown(self):
        """Shuts down the MT5 connection."""
        mt5.shutdown()
        self.connected = False
        logging.info("MT5 connection shut down.")

    def get_symbol_info(self, symbol):
        """Retrieves symbol information."""
        if not self.connected:
            return None
        
        info = mt5.symbol_info(symbol)
        if info is None:
            logging.error(f"{symbol} not found, can not call order_check()")
            return None
        
        if not info.visible:
            logging.info(f"{symbol} is not visible, trying to switch on")
            if not mt5.symbol_select(symbol, True):
                logging.error(f"symbol_select({symbol}) failed, exit")
                return None
                
        return info

    def get_market_data(self, symbol, timeframe, num_candles=100):
        """Fetches historical data for a symbol."""
        if not self.connected:
            return None

        # Map string timeframe to MT5 constant if needed, for now assuming timeframe is passed correctly or handled elsewhere
        # Simple mapping example (can be expanded)
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "H1": mt5.TIMEFRAME_H1
        }
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M1)

        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, num_candles)
        if rates is None:
            logging.error(f"No data for {symbol} (Error code: {mt5.last_error()})")
            return None

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df

    def get_ticks(self, symbol, from_date=None, num_ticks=1000, flags=mt5.COPY_TICKS_ALL):
        """Fetches tick data for a symbol."""
        if not self.connected:
            return None
        
        if from_date is None:
            # Default to 1 minute ago if not specified
            from_date = datetime.now() - pd.Timedelta(minutes=1)

        ticks = mt5.copy_ticks_from(symbol, from_date, num_ticks, flags)
        if ticks is None:
            logging.error(f"No ticks for {symbol} (Error code: {mt5.last_error()})")
            return None
            
        return ticks

    def get_open_positions_count(self, symbol=None):
        """Returns the number of open positions for a symbol (or all if None)."""
        if not self.connected:
            return 0
        
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
            
        if positions is None:
            return 0
            
        return len(positions)

    def execute_order(self, symbol, order_type, volume, sl_dist=None, tp_dist=None, deviation=20):
        """Executes a trade order with optional SL/TP distances (in Price)."""
        if not self.connected:
            return None

        symbol_info = self.get_symbol_info(symbol)
        if symbol_info is None:
            return None

        action = mt5.TRADE_ACTION_DEAL
        price = 0.0
        
        if order_type == mt5.ORDER_TYPE_BUY:
            price = mt5.symbol_info_tick(symbol).ask
            sl = price - sl_dist if sl_dist else 0.0
            tp = price + tp_dist if tp_dist else 0.0
        elif order_type == mt5.ORDER_TYPE_SELL:
            price = mt5.symbol_info_tick(symbol).bid
            sl = price + sl_dist if sl_dist else 0.0
            tp = price - tp_dist if tp_dist else 0.0
        
        request = {
            "action": action,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": deviation,
            "magic": 234000,
            "comment": "python script open",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logging.error(f"Order failed: {result.comment}")
        else:
            logging.info(f"Order executed: {result.order} | Price: {price} | SL: {sl} | TP: {tp}")
            
        return result

    def check_trailing_stop(self, symbol, atr, trigger_mult=1.0, dist_mult=0.5):
        """Checks and updates trailing stops for open positions."""
        if not self.connected or atr is None:
            return

        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return

        for pos in positions:
            # Calculate distances in Price
            trigger_dist = atr * trigger_mult
            new_sl_dist = atr * dist_mult
            
            current_price = mt5.symbol_info_tick(symbol).bid if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).ask
            
            # BUY Position
            if pos.type == mt5.ORDER_TYPE_BUY:
                profit_dist = current_price - pos.price_open
                if profit_dist > trigger_dist:
                    new_sl = pos.price_open + new_sl_dist
                    # Only move SL up
                    if new_sl > pos.sl:
                        self._modify_position(pos.ticket, new_sl, pos.tp)
            
            # SELL Position
            elif pos.type == mt5.ORDER_TYPE_SELL:
                profit_dist = pos.price_open - current_price
                if profit_dist > trigger_dist:
                    new_sl = pos.price_open - new_sl_dist
                    # Only move SL down
                    if pos.sl == 0.0 or new_sl < pos.sl:
                        self._modify_position(pos.ticket, new_sl, pos.tp)

    def _modify_position(self, ticket, sl, tp):
        """Helper to modify SL/TP of a position."""
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": sl,
            "tp": tp,
            "magic": 234000,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logging.error(f"Failed to modify position #{ticket}: {result.comment}")
        else:
            logging.info(f"Trailing Stop Updated for #{ticket} -> SL: {sl}")
