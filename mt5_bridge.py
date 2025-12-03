import MetaTrader5 as mt5
import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import traceback
from datetime import datetime, timedelta

# Load environment variables
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
    sl_points: int
    tp_points: int
    magic: int = 123456
    comment: str = "MT5 Bridge Trade"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    print("\n--- Bridge Startup ---")
    if not mt5.initialize(path=PATH):
        print(f"❌ Error initializing: {mt5.last_error()}")
        if not mt5.initialize(): 
            print(f"❌ Critical: Connection failed.")
    
    current_account = mt5.account_info()
    if current_account and current_account.login == LOGIN:
        print(f"✅ Already logged in as {LOGIN}")
    else:
        print(f"⚠️ Logging in as {LOGIN}...")
        if mt5.login(LOGIN, password=PASSWORD, server=SERVER):
            print(f"✅ Login successful")
        else:
            print(f"❌ Login failed: {mt5.last_error()}")

    print("----------------------\n")
    yield 
    # --- SHUTDOWN ---
    print("\n--- Bridge Shutdown ---")
    mt5.shutdown()
    print("-----------------------")

app = FastAPI(title="MT5 Bridge", lifespan=lifespan)

@app.post("/execute_signal")
async def execute_trade(signal: TradeSignal):
    try:
        if not mt5.terminal_info():
            raise HTTPException(status_code=500, detail="MT5 Disconnected")

        symbol_info = mt5.symbol_info(signal.symbol)
        if not symbol_info:
            raise HTTPException(status_code=400, detail=f"Symbol '{signal.symbol}' not found")

        filling = mt5.ORDER_FILLING_FOK 
        if (symbol_info.filling_mode & 2) != 0: filling = mt5.ORDER_FILLING_IOC
        elif (symbol_info.filling_mode & 1) != 0: filling = mt5.ORDER_FILLING_FOK

        tick = mt5.symbol_info_tick(signal.symbol)
        if not tick:
            raise HTTPException(status_code=500, detail="No price data available")

        price = tick.ask if signal.action.lower() == "buy" else tick.bid
        point = symbol_info.point
        
        sl_offset = float(signal.sl_points * point)
        tp_offset = float(signal.tp_points * point)
        
        if signal.action.lower() == "buy":
            order_type = mt5.ORDER_TYPE_BUY
            sl = price - sl_offset
            tp = price + tp_offset
        else:
            order_type = mt5.ORDER_TYPE_SELL
            sl = price + sl_offset
            tp = price - tp_offset

        digits = symbol_info.digits
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": float(signal.volume),
            "type": order_type,
            "price": float(price),
            "sl": round(sl, digits), 
            "tp": round(tp, digits), 
            "deviation": 20,
            "magic": int(signal.magic),
            "comment": str(signal.comment),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)
        
        if result is None:
            raise HTTPException(status_code=500, detail="MT5 Order Send returned None")
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            error_details = f"MT5 Error: {result.comment} (Code: {result.retcode})"
            print(f"❌ {error_details}")
            raise HTTPException(status_code=500, detail=error_details)

        print(f"✅ TRADE EXECUTED: {signal.action} {signal.volume} @ {price}")
        return {"order_id": result.order, "price": result.price}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
        
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

@app.post("/close_all")
def close_all_positions():
    if not mt5.terminal_info(): 
        raise HTTPException(500, "Disconnected")
    
    status_log = []
    
    # 1. Close Active Positions
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions:
        for pos in positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            type_op = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": type_op,
                "position": pos.ticket,
                "price": price,
                "deviation": 50, # INCREASED SLIPPAGE TOLERANCE
                "magic": pos.magic,
                "comment": "Strategy Reset",
            }
            res = mt5.order_send(request)
            status_log.append(f"Position {pos.ticket}: {res.comment if res else 'Failed'}")

    # 2. Cancel Pending Orders
    orders = mt5.orders_get(symbol=SYMBOL)
    if orders:
        for order in orders:
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": order.ticket,
                "symbol": SYMBOL,
            }
            res = mt5.order_send(request)
            status_log.append(f"Order {order.ticket}: {res.comment if res else 'Failed'}")
            
    print(f"☢️ CLOSE ALL EXECUTED: {status_log}")
    return {"log": status_log}

@app.get("/recent_deals")
def get_recent_deals(seconds: int = 60):
    if not mt5.terminal_info(): return []
    from_date = datetime.now() - timedelta(seconds=seconds)
    deals = mt5.history_deals_get(from_date, datetime.now())
    if not deals: return []
    return [{"ticket": d.ticket, "profit": d.profit, "symbol": d.symbol, "comment": d.comment} for d in deals if d.symbol == SYMBOL]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT)