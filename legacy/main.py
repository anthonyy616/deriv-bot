import time
import logging
import MetaTrader5 as mt5
import config
from mt5_interface import MT5Interface
from strategy import Strategy

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("trade_bot.log"),
        logging.StreamHandler()
    ]
)

def main():
    logging.info("Starting HFT Bot...")

    # Initialize MT5 Interface
    mt5_client = MT5Interface(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER
    )

    if not mt5_client.start():
        logging.error("Failed to start MT5 Client. Exiting.")
        return

    # Initialize Strategy
    strategy = Strategy(
        rsi_period=config.RSI_PERIOD,
        rsi_overbought=config.RSI_OVERBOUGHT,
        rsi_oversold=config.RSI_OVERSOLD,
        atr_period=config.ATR_PERIOD,
        atr_sl_mult=config.ATR_SL_MULTIPLIER,
        atr_tp_mult=config.ATR_TP_MULTIPLIER
    )

    try:
        logging.info(f"Monitoring {config.SYMBOL}...")
        
        while True:
            # 1. Fetch Data
            data = mt5_client.get_market_data(config.SYMBOL, config.TIMEFRAME, num_candles=100)
            
            if data is not None:
                # 2. Calculate Indicators
                data = strategy.calculate_indicators(data)
                
                # 3. Get Signal
                signal_data = strategy.get_signal(data)
                
                # Extract current ATR for Trailing Stop
                current_atr = data.iloc[-1]['atr']
                
                # Check Trailing Stop for existing positions
                mt5_client.check_trailing_stop(
                    config.SYMBOL, 
                    current_atr, 
                    trigger_mult=config.TRAILING_STOP_TRIGGER, 
                    dist_mult=config.TRAILING_STOP_DISTANCE
                )
                
                if signal_data:
                    signal_type = signal_data['signal']
                    sl_dist = signal_data['sl_dist']
                    tp_dist = signal_data['tp_dist']
                    
                    logging.info(f"Signal Detected: {signal_type} | SL Dist: {sl_dist:.5f} | TP Dist: {tp_dist:.5f}")
                    
                    # 4. Execute Trade
                    # Check for open positions
                    open_positions = mt5_client.get_open_positions_count(config.SYMBOL)
                    logging.info(f"Open Positions: {open_positions}")

                    if open_positions >= config.MAX_POSITIONS:
                        logging.info("Max positions reached. Skipping trade.")
                        continue

                    if signal_type == 'BUY':
                        mt5_client.execute_order(
                            config.SYMBOL, 
                            mt5.ORDER_TYPE_BUY, 
                            config.VOLUME,
                            sl_dist=sl_dist,
                            tp_dist=tp_dist
                        )
                    elif signal_type == 'SELL':
                        mt5_client.execute_order(
                            config.SYMBOL, 
                            mt5.ORDER_TYPE_SELL, 
                            config.VOLUME,
                            sl_dist=sl_dist,
                            tp_dist=tp_dist
                        )
            
            time.sleep(.5) # Wait for 0.5 seconds

    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    except Exception as e:
        logging.exception(f"An error occurred: {e}")
    finally:
        mt5_client.shutdown()

if __name__ == "__main__":
    main()
