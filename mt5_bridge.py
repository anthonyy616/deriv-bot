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
    price: float = 0.0  
    sl_points: int
    tp_points: int
    magic: int = 123456
    comment: str = "MT5 Bridge Trade"

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n--- Bridge Startup ---")
    if not mt5.initialize(path=PATH):
        if not mt5.initialize(): 
            print(f"❌ Critical: Connection failed.")
    
    if mt5.login(LOGIN, password=PASSWORD, server=SERVER):
        print(f"✅ Login successful: {LOGIN}")
    else:
        print(f"❌ Login failed: {mt5.last_error()}")

    print("----------------------\n")
    yield 
    print("\n--- Bridge Shutdown ---")
    mt5.shutdown()
    print("-----------------------")

app = FastAPI(title="MT5 Bridge", lifespan=lifespan)

@app.post("/execute_signal")
async def execute_trade(signal: TradeSignal):
    try:
        if not mt5.terminal_info(): raise HTTPException(500, "MT5 Disconnected")

        symbol_info = mt5.symbol_info(signal.symbol)
        if not symbol_info: raise HTTPException(400, "Symbol not found")

        point = symbol_info.point
        digits = symbol_info.digits
        
        # Determine Order Type
        action_map = {
            "buy": mt5.ORDER_TYPE_BUY,
            "sell": mt5.ORDER_TYPE_SELL,
            "buy_stop": mt5.ORDER_TYPE_BUY_STOP,   
            "sell_stop": mt5.ORDER_TYPE_SELL_STOP  
        }
        
        order_type = action_map.get(signal.action.lower())
        if order_type is None: raise HTTPException(400, "Invalid action")

        # Price Logic
        if "stop" in signal.action.lower():
            # For Pending Orders, use the specific requested price
            price = signal.price
            trade_action = mt5.TRADE_ACTION_PENDING
        else:
            # For Market Orders, use current Ask/Bid
            tick = mt5.symbol_info_tick(signal.symbol)
            price = tick.ask if signal.action.lower() == "buy" else tick.bid
            trade_action = mt5.TRADE_ACTION_DEAL

        sl = price - (signal.sl_points * point) if "buy" in signal.action.lower() else price + (signal.sl_points * point)
        tp = price + (signal.tp_points * point) if "buy" in signal.action.lower() else price - (signal.tp_points * point)

        if signal.sl_points == 0: sl = 0.0
        if signal.tp_points == 0: tp = 0.0

        request = {
            "action": trade_action,
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
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        result = mt5.order_send(request)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"❌ Order Failed: {result.comment if result else 'Unknown'}")
            raise HTTPException(500, f"MT5 Error: {result.comment if result else 'Unknown'}")

        print(f"✅ ORDER SENT: {signal.action} @ {price}")
        return {"order_id": result.order, "price": result.price}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cancel_orders")
def cancel_pending_orders():
    if not mt5.terminal_info(): return {"error": "Disconnected"}
    
    orders = mt5.orders_get(symbol=SYMBOL)
    count = 0
    if orders:
        for order in orders:
            req = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": order.ticket,
                "symbol": SYMBOL
            }
            res = mt5.order_send(req)
            if res.retcode == mt5.TRADE_RETCODE_DONE: count += 1
            
    print(f"🧹 Canceled {count} Pending Orders")
    return {"canceled": count}

@app.post("/close_all")
def close_all_positions():
    """ Nuclear: Closes Positions AND Cancels Orders """
    cancel_pending_orders() 
    
    positions = mt5.positions_get(symbol=SYMBOL)
    count = 0
    if positions:
        for pos in positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            type_op = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": type_op,
                "position": pos.ticket,
                "price": price,
                "deviation": 50,
                "comment": "Reset",
            }
            res = mt5.order_send(req)
            if res.retcode == mt5.TRADE_RETCODE_DONE: count += 1
            
    return {"closed": count}

@app.get("/account_info")
def get_account_info():
    if not mt5.terminal_info(): return {"status": "disconnected"}
    account = mt5.account_info()
    positions = mt5.positions_get(symbol=SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    symbol_info = mt5.symbol_info(SYMBOL) # FIXED: Get point from here
    
    return {
        "balance": account.balance,
        "equity": account.equity,
        "positions_count": len(positions) if positions else 0,
        "current_price": tick.ask if tick else 0,
        "symbol": SYMBOL,
        "point": symbol_info.point if symbol_info else 0.001 # FIXED
    }

@app.get("/recent_deals")
def get_recent_deals(seconds: int = 60):
    if not mt5.terminal_info(): return []
    from_date = datetime.now() - timedelta(seconds=seconds)
    deals = mt5.history_deals_get(from_date, datetime.now())
    if not deals: return []
    return [{"ticket": d.ticket, "type": d.type, "profit": d.profit, "symbol": d.symbol} for d in deals if d.symbol == SYMBOL]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT)