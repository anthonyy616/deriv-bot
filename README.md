# HFT Bot for Deriv (MT5)

This is a High-Frequency Trading (HFT) bot designed to connect to MetaTrader 5 (MT5) and execute trades based on a defined strategy.

## Prerequisites

1.  **MetaTrader 5 Terminal**: Download and install the MT5 terminal from your broker (e.g., Deriv).
2.  **Python 3.8+**: Ensure Python is installed on your system.
3.  **MT5 Account**: You need a Demo or Real account.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    -   Open `config.py`.
    -   Update `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER` with your credentials.
    -   Adjust trading settings (Symbol, Volume, etc.) as needed.

    *Note: It is recommended to use environment variables for credentials.*

3.  **MT5 Terminal**:
    -   Open MT5.
    -   Go to `Tools` -> `Options` -> `Expert Advisors`.
    -   Enable "Allow algorithmic trading".

## Running the Bot

Run the main script:
```bash
python main.py
```

The bot will:
1.  Launch/Connect to the MT5 Terminal.
2.  Login to your account.
3.  Start monitoring the configured symbol (default: EURUSD).
4.  Log price updates and actions to `trade_bot.log` and the console.

## Project Structure

-   `main.py`: Entry point. Orchestrates the bot's lifecycle.
-   `mt5_interface.py`: Handles all interactions with the MT5 terminal (connection, data, orders).
-   `config.py`: Configuration settings.
-   `requirements.txt`: Python dependencies.

## Disclaimer

Trading involves risk. This bot is for educational and experimental purposes. Use at your own risk.
