import MetaTrader5 as mt5
import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager

# Load environment variables
load_dotenv()

# Configuration
LOGIN = int(os.getenv("MT5_LOGIN"))
PASSWORD = os.getenv("MT5_PASSWORD")
SERVER = os.getenv("MT5_SERVER")
PATH = os.getenv("MT5_PATH")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", 8001))
SIGNAL_SERVER_URL = os.getenv("SIGNAL_SERVER_URL", "http://localhost:8000")
SYMBOL = "FX Vol 20" 

class TradeSignal(BaseModel):
    action: str
    symbol: str
    volume: float
    sl_points: int
    tp_points: int
    magic: int = 123456
    comment: str = "MT5 Bridge Trade"

# --- Helper Functions ---

def ensure_symbol(symbol):
    """Attempts to select the symbol in Market Watch."""
    # Ensure terminal is connected first
    if not mt5.terminal_info():
        return False
        
    selected = mt5.symbol_select(symbol, True)
    if not selected:
        print(f"   [MT5] Failed to select '{symbol}' (Error: {mt5.last_error()})")
        return False
    return True

async def tick_stream_loop():
    """Continuously polls MT5 for ticks and sends them to the Signal Server."""
    print(f"🚀 Starting tick stream for {SYMBOL}...")
    
    # 1. Wait for Terminal Connection
    while not mt5.terminal_info():
        print("   [Stream] Waiting for terminal connection...")
        await asyncio.sleep(2)

    # 2. Robust Symbol Check (Retry Loop)
    retry_count = 0
    while not ensure_symbol(SYMBOL):
        retry_count += 1
        print(f"   [Stream] Retrying symbol selection ({retry_count}/5)...")
        await asyncio.sleep(2)
        if retry_count > 5:
            print(f"❌ [Stream] ABORT: Could not find symbol '{SYMBOL}'. Check name/broker.")
            return

    symbol_info = mt5.symbol_info(SYMBOL)
    point = symbol_info.point if symbol_info else 0.001
    last_time = 0
    
    print(f"✅ [Stream] Live and streaming {SYMBOL}...")

    # 3. Stream Loop
    while True:
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick and tick.time > last_time:
            last_time = tick.time
            try:
                payload = {
                    "symbol": SYMBOL,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "time": int(tick.time),
                    "point": point
                }
                # Fast timeout so bridge doesn't lag if main server is busy
                requests.post(f"{SIGNAL_SERVER_URL}/tick", json=payload, timeout=0.1)
            except Exception:
                pass 
        
        await asyncio.sleep(0.01) # 10ms poll rate

# --- Lifespan (The Critical Fix) ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    print("\n--- Bridge Startup ---")
    
    # 1. Initialize MT5
    if not mt5.initialize(path=PATH):
        print(f"❌ Error initializing: {mt5.last_error()}")
        if not mt5.initialize(): # Try default path fallback
            print(f"❌ Critical: Connection failed.")
    
    # 2. Smart Login
    current_account = mt5.account_info()
    if current_account and current_account.login == LOGIN:
        print(f"✅ Already logged in as {LOGIN}")
    else:
        print(f"⚠️ Logging in as {LOGIN}...")
        if mt5.login(LOGIN, password=PASSWORD, server=SERVER):
            print(f"✅ Login successful")
        else:
            print(f"❌ Login failed: {mt5.last_error()}")

    # 3. Start Background Stream
    task = asyncio.create_task(tick_stream_loop())
    
    print("----------------------\n")
    
    # 🚨 THIS YIELD IS REQUIRED. DO NOT REMOVE. 🚨
    yield 
    
    # --- SHUTDOWN ---
    print("\n--- Bridge Shutdown ---")
    task.cancel()
    mt5.shutdown()
    print("-----------------------")

# --- App Definition ---
app = FastAPI(title="MT5 Bridge", lifespan=lifespan)

# --- Endpoints ---

@app.post("/execute_signal")
async def execute_trade(signal: TradeSignal):
    # Quick checks
    if not mt5.terminal_info():
        raise HTTPException(status_code=500, detail="MT5 Disconnected")
    
    # Filling Mode Fix
    symbol_info = mt5.symbol_info(signal.symbol)
    if not symbol_info:
        raise HTTPException(status_code=400, detail="Symbol not found")

    filling_mode = mt5.ORDER_FILLING_FOK
    if symbol_info.filling_mode == mt5.SYMBOL_FILLING_IOC:
        filling_mode = mt5.ORDER_FILLING_IOC

    # Price & SL/TP
    tick = mt5.symbol_info_tick(signal.symbol)
    if not tick:
        raise HTTPException(status_code=500, detail="No price data")
    
    price = tick.ask if signal.action == "buy" else tick.bid
    point = symbol_info.point
    
    sl_offset = signal.sl_points * point
    tp_offset = signal.tp_points * point
    
    if signal.action == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        sl = price - sl_offset
        tp = price + tp_offset
    else:
        order_type = mt5.ORDER_TYPE_SELL
        sl = price + sl_offset
        tp = price - tp_offset

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
        "type_filling": filling_mode,
    }

    result = mt5.order_send(request)
    
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        msg = result.comment if result else "Unknown"
        print(f"❌ Exec Failed: {msg}")
        raise HTTPException(status_code=500, detail=msg)

    print(f"✅ TRADE: {signal.action.upper()} {signal.volume} @ {price}")
    return {"order_id": result.order, "price": result.price}

@app.get("/account_info")
def get_account_info():
    if not mt5.terminal_info(): return {"status": "disconnected"}
    
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