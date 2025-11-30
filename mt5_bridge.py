import MetaTrader5 as mt5
import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import requests
import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 0))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "Weltrade-Live")
MT5_PATH = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")
SIGNAL_SERVER_URL = os.getenv("SIGNAL_SERVER_URL", "http://localhost:8000")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", 8001))
SYMBOL = "Volatility 20 Index"  # Default symbol to stream

app = FastAPI(title="MT5 Bridge")

class TradeSignal(BaseModel):
    action: str  # "buy" or "sell"
    symbol: str
    volume: float
    sl_points: int
    tp_points: int
    magic: int = 123456
    comment: str = "MT5 Bridge Trade"

class TickData(BaseModel):
    symbol: str
    bid: float
    ask: float
    time: int
    point: float

# --- MT5 Management ---

def initialize_mt5():
    print(f"Attempting to connect to MT5 at: {MT5_PATH}")
    # Attempt to initialize with path
    if not mt5.initialize(path=MT5_PATH):
        print(f"❌ initialize() failed, error code = {mt5.last_error()}")
        # Try without path (if it's already running or in registry)
        print("Retrying without path...")
        if not mt5.initialize():
            print(f"❌ initialize() without path failed, error code = {mt5.last_error()}")
            return False
    
    # Login
    authorized = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if authorized:
        print(f"✅ Connected to MT5 account #{MT5_LOGIN}")
        account_info = mt5.account_info()
        if account_info:
            print(f"   Balance: {account_info.balance} USD, Equity: {account_info.equity} USD")
    else:
        print(f"❌ Failed to login to account #{MT5_LOGIN}, error code: {mt5.last_error()}")
    return authorized

def ensure_symbol(symbol):
    selected = mt5.symbol_select(symbol, True)
    if not selected:
        print(f"Failed to select {symbol}, error code = {mt5.last_error()}")
        return False
    return True

# --- Background Tick Stream ---

async def tick_stream_loop():
    """Continuously polls MT5 for ticks and sends them to the Signal Server."""
    print(f"Starting tick stream for {SYMBOL}...")
    if not ensure_symbol(SYMBOL):
        print("Symbol not available. Stream aborted.")
        return

    last_time = 0
    symbol_info = mt5.symbol_info(SYMBOL)
    if not symbol_info:
        print(f"Failed to get symbol info for {SYMBOL}")
        return
        
    point = symbol_info.point
    
    while True:
        # Check connection
        if not mt5.terminal_info():
            print("MT5 disconnected, attempting reconnect...")
            initialize_mt5()
            await asyncio.sleep(5)
            continue

        tick = mt5.symbol_info_tick(SYMBOL)
        if tick and tick.time > last_time:
            last_time = tick.time
            # Send to Signal Server
            try:
                payload = {
                    "symbol": SYMBOL,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "time": int(tick.time),
                    "point": point
                }
                # Use a short timeout to avoid blocking if server is down
                requests.post(f"{SIGNAL_SERVER_URL}/tick", json=payload, timeout=0.5)
            except Exception as e:
                # Suppress connection errors to keep log clean
                pass
        
        await asyncio.sleep(0.1) # Poll interval

# --- API Endpoints ---

@app.on_event("startup")
async def startup_event():
    if initialize_mt5():
        asyncio.create_task(tick_stream_loop())
    else:
        print("CRITICAL: Failed to initialize MT5 on startup.")

@app.on_event("shutdown")
def shutdown_event():
    mt5.shutdown()
    print("MT5 connection closed.")

@app.post("/execute_signal")
async def execute_trade(signal: TradeSignal):
    if not mt5.terminal_info():
        if not initialize_mt5():
             raise HTTPException(status_code=500, detail="MT5 not connected")

    if not ensure_symbol(signal.symbol):
        raise HTTPException(status_code=400, detail=f"Symbol {signal.symbol} not found")

    # Prepare Order
    symbol_info = mt5.symbol_info(signal.symbol)
    if not symbol_info:
        raise HTTPException(status_code=400, detail="Symbol info not found")

    point = symbol_info.point
    tick = mt5.symbol_info_tick(signal.symbol)
    if not tick:
        raise HTTPException(status_code=500, detail="Failed to get tick data")
        
    price = tick.ask if signal.action == "buy" else tick.bid
    
    # Calculate SL/TP prices
    if signal.action == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        sl = price - (signal.sl_points * point)
        tp = price + (signal.tp_points * point)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        sl = price + (signal.sl_points * point)
        tp = price - (signal.tp_points * point)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": signal.symbol,
        "volume": signal.volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": signal.magic,
        "comment": signal.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    # Execute
    result = mt5.order_send(request)
    
    if result is None:
         raise HTTPException(status_code=500, detail="Order send returned None")
         
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"❌ Order failed: {result.comment} ({result.retcode})")
        raise HTTPException(status_code=500, detail=f"MT5 Error: {result.comment}")

    print(f"✅ Trade Executed: {signal.action.upper()} {signal.volume} {signal.symbol} @ {price}")
    return {
        "status": "success",
        "order_id": result.order,
        "price": result.price,
        "comment": result.comment
    }

@app.get("/health")
def health_check():
    info = mt5.terminal_info()
    return {
        "status": "online", 
        "mt5_connected": info is not None if info else False,
        "account": MT5_LOGIN
    }

@app.get("/account_info")
def get_account_info():
    if not mt5.terminal_info():
        return {"status": "disconnected"}
    
    account = mt5.account_info()
    positions = mt5.positions_get(symbol=SYMBOL)
    orders = mt5.orders_get(symbol=SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    
    return {
        "balance": account.balance if account else 0,
        "equity": account.equity if account else 0,
        "positions_count": len(positions) if positions else 0,
        "pending_orders_count": len(orders) if orders else 0,
        "current_price": tick.ask if tick else 0,
        "symbol": SYMBOL
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT)
