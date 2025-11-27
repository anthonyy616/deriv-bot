import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# MT5 Credentials
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 0))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
MT5_PATH = os.getenv("MT5_PATH", "") # Path to terminal64.exe

# Trading Settings
SYMBOL = "Volatility 25 Index"  # Crypto Pair for testing
TIMEFRAME = "M1"   # Default timeframe
VOLUME = 1.00      # Default lot size
DEVIATION = 20     # Max deviation in points

# Risk Management
MAX_DRAWDOWN_PCT = 0.05  # 5% max daily drawdow
MAX_DAILY_LOSS = 100.0   # Max daily loss in account currency

# Strategy Settings
RSI_PERIOD = 2       # Very short period for testing
RSI_OVERBOUGHT = 50  # Low threshold to trigger SELL often
RSI_OVERSOLD = 50    # High threshold to trigger BUY often

# Risk Management

MAX_POSITIONS = 15    # Maximum number of open positions allowed
ATR_PERIOD = 14      # Period for ATR calculation
ATR_SL_MULTIPLIER = 2.0  # Stop Loss = 2 * ATR
ATR_TP_MULTIPLIER = 3.0  # Take Profit = 3 * ATR

# Trailing Stop Settings
TRAILING_STOP_TRIGGER = 1.0   # Move SL when profit > 1.0 * ATR
TRAILING_STOP_DISTANCE = 0.5  # New SL distance = Entry +/- 0.5 * ATR
