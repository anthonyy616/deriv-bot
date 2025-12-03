import MetaTrader5 as mt5
import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import traceback

load_dotenv()

# Configuration
LOGIN = int(os.getenv("MT5_LOGIN", 0))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER = os.getenv("MT5_SERVER", "")
PATH = os.getenv("MT5_PATH", "")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", 8001))
SYMBOL = "FX Vol 20" 

class TradeSignal(BaseModel):
    action: str
    symbol: str
    volume: float
    price: float = 0.0  
    sl_points: float 
    tp_points: float 
    magic: int = 123456
    comment: str = "MT5 Bridge"

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n--- Bridge Startup ---")
    if not mt5.initialize(path=PATH):
        if not mt5.initialize(): 
            print("❌ Critical: Init failed.")
    
    if mt5.login(LOGIN, password=PASSWORD, server=SERVER):
        print(f"✅ Login successful: {LOGIN} on {SERVER}")
    else:
        print(f"❌ Login failed: {mt5.last_error()}")
    
    mt5.symbol_select(SYMBOL, True)
    yield 
    mt5.shutdown()
    print("--- Bridge Shutdown ---")

app = FastAPI(title="MT5 Bridge", lifespan=lifespan)

# --- HELPER: Strict Normalization ---
def normalize_price(price, tick_size, digits):
    """
    Rounds price to the nearest valid tick step AND strictly rounds to 'digits'.
    """
    if tick_size == 0: return round(price, digits)
    # 1. Round to nearest tick
    rounded_tick = round(price / tick_size) * tick_size
    # 2. Hard round to digits to remove float artifacts
    return round(rounded_tick, digits)

# --- HELPER: Get Valid Filling Mode (Integer Fix) ---
def get_filling_mode(symbol_info):
    modes = symbol_info.filling_mode
    # Use Integers directly to avoid AttributeError
    # 1 = FOK, 2 = IOC
    if modes & 1: return mt5.ORDER_FILLING_FOK
    elif modes & 2: return mt5.ORDER_FILLING_IOC
    else: return mt5.ORDER_FILLING_RETURN

@app.post("/execute_signal")
async def execute_trade(signal: TradeSignal):
    try:
        if not mt5.terminal_info(): raise HTTPException(500, "MT5 Disconnected")
        
        symbol_info = mt5.symbol_info(signal.symbol)
        if not symbol_info: raise HTTPException(400, f"Symbol {signal.symbol} not found")

        tick_size = symbol_info.trade_tick_size
        digits = symbol_info.digits
        
        action_map = {
            "buy": mt5.ORDER_TYPE_BUY, "sell": mt5.ORDER_TYPE_SELL,
            "buy_stop": mt5.ORDER_TYPE_BUY_STOP, "sell_stop": mt5.ORDER_TYPE_SELL_STOP
        }
        order_type = action_map.get(signal.action.lower())
        
        # Get Live Data to validate Stop Levels
        tick = mt5.symbol_info_tick(signal.symbol)
        if not tick: raise HTTPException(500, "Tick data unavailable")

        # Price Logic
        if "stop" in signal.action.lower():
            raw_price = signal.price
            trade_action = mt5.TRADE_ACTION_PENDING
        else:
            raw_price = tick.ask if signal.action.lower() == "buy" else tick.bid
            trade_action = mt5.TRADE_ACTION_DEAL

        # --- CRITICAL FIX: Normalize EVERYTHING ---
        price = normalize_price(raw_price, tick_size, digits)

        # Calculate SL/TP
        if "buy" in signal.action.lower():
            sl = price - signal.sl_points
            tp = price + signal.tp_points
        else:
            sl = price + signal.sl_points
            tp = price - signal.tp_points

        sl = normalize_price(sl, tick_size, digits) if signal.sl_points > 0 else 0.0
        tp = normalize_price(tp, tick_size, digits) if signal.tp_points > 0 else 0.0

        filling_mode = get_filling_mode(symbol_info)

        request = {
            "action": trade_action,
            "symbol": signal.symbol,
            "volume": float(signal.volume),
            "type": order_type,
            "price": price, 
            "sl": sl,
            "tp": tp,
            "deviation": 50,
            "magic": int(signal.magic),
            "comment": str(signal.comment),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode, 
        }

        print(f"📡 Sending: {signal.action} @ {price} | SL: {sl} | TP: {tp}")
        
        result = mt5.order_send(request)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error_msg = result.comment if result else "Unknown Error"
            print(f"❌ Order Failed: {error_msg} ({result.retcode if result else '?'})")
            raise HTTPException(500, f"MT5 Error: {error_msg}")

        print(f"✅ ORDER SENT: Ticket {result.order}")
        return {"order_id": result.order, "price": result.price}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/account_info")
def account_info():
    if not mt5.terminal_info(): return {"status": "disconnected"}
    
    account = mt5.account_info()
    positions = mt5.positions_get(symbol=SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    
    return {
        "balance": account.balance,
        "equity": account.equity,
        "positions_count": len(positions) if positions else 0,
        "current_price": tick.ask if tick else 0.0,
        "symbol": SYMBOL,
        "point": 0.001 
    }

# --- Helpers ---
@app.post("/cancel_orders")
def cancel_pending_orders():
    if not mt5.terminal_info(): return {"error": "Disconnected"}
    orders = mt5.orders_get()
    count = 0
    if orders: 
        for o in orders: 
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            count += 1
    return {"canceled": count}

@app.post("/close_all")
def close_all():
    cancel_pending_orders()
    positions = mt5.positions_get()
    count = 0
    if positions:
        for p in positions:
            tick = mt5.symbol_info_tick(p.symbol)
            price = tick.bid if p.type == 0 else tick.ask
            type_op = 1 if p.type == 0 else 0
            mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL, 
                "symbol": p.symbol, 
                "position": p.ticket, 
                "volume": p.volume, 
                "type": type_op, 
                "price": price
            })
            count += 1
    return {"closed": count}

@app.get("/recent_deals")
def recent_deals(seconds: int = 60):
    if not mt5.terminal_info(): return []
    from datetime import datetime, timedelta
    d = mt5.history_deals_get(datetime.now() - timedelta(seconds=seconds), datetime.now())
    return [{"ticket": x.ticket, "type": x.type, "profit": x.profit} for x in d] if d else []

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT)