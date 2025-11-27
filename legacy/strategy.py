import talib
import pandas as pd
import logging

class Strategy:
    def __init__(self, rsi_period=14, rsi_overbought=70, rsi_oversold=30, atr_period=14, atr_sl_mult=2.0, atr_tp_mult=3.0):
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.atr_period = atr_period
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult

    def calculate_indicators(self, df):
        """Calculates technical indicators."""
        if df is None or len(df) < max(self.rsi_period, self.atr_period):
            return df
        
        # Calculate RSI
        df['rsi'] = talib.RSI(df['close'], timeperiod=self.rsi_period)
        
        # Calculate ATR
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], timeperiod=self.atr_period)
        return df

    def get_signal(self, df):
        """
        Analyzes the dataframe and returns a signal with risk parameters.
        Returns: {'signal': 'BUY'/'SELL', 'sl_dist': float, 'tp_dist': float} or None
        """
        if df is None or len(df) < max(self.rsi_period, self.atr_period):
            return None

        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        # RSI Logic
        current_rsi = last_row['rsi']
        prev_rsi = prev_row['rsi']
        current_atr = last_row['atr']
        
        logging.info(f"RSI: {current_rsi:.2f} | ATR: {current_atr:.5f}")

        signal = None
        # Buy Signal: RSI crosses above Oversold
        if prev_rsi < self.rsi_oversold and current_rsi >= self.rsi_oversold:
            signal = 'BUY'
        
        # Sell Signal: RSI crosses below Overbought
        if prev_rsi > self.rsi_overbought and current_rsi <= self.rsi_overbought:
            signal = 'SELL'
            
        if signal:
            # Calculate Dynamic SL/TP Distances (in Price, not points)
            sl_dist = current_atr * self.atr_sl_mult
            tp_dist = current_atr * self.atr_tp_mult
            
            return {
                'signal': signal,
                'sl_dist': sl_dist,
                'tp_dist': tp_dist
            }
            
        return None
