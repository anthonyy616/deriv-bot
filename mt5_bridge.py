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
    comment: str = "MT5 Bridge Trade"

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n--- Bridge Startup ---")
    if not mt5.initialize(path=PATH):
        if not mt5.initialize(): 
            print("❌ Critical: Connection failed.")
    
    if mt5.login(LOGIN, password=PASSWORD, server=SERVER):
        print(f"✅ Login successful: {LOGIN} on {SERVER}")
    else:
        print(f"❌ Login failed: {mt5.last_error()}")
    
    if not mt5.symbol_select(SYMBOL, True):
        print(f"⚠️ Warning: Failed to select {SYMBOL}")
        
    print("----------------------\n")
    yield 
    print("\n--- Bridge Shutdown ---")
    mt5.shutdown()
    print("-----------------------")

app = FastAPI(title="MT5 Bridge", lifespan=lifespan)

# --- HELPER: Strict Precision Enforcement ---
def normalize_price(price, tick_size):
    """
    Rounds price to the nearest tick and converts to FLOAT via STRING.
    This strips 'invisible' floating point artifacts (e.g. 100.0000001).
    """
    if tick_size == 0: return price
    
    # 1. Math Rounding
    rounded_price = round(price / tick_size) * tick_size
    
    # 2. Precision Calculation (e.g., 0.01 -> 2 decimals)
    # This prevents 100.20 becoming 100.1999999999
    decimal_places = 0
    if "." in str(tick_size):
        decimal_places = len(str(tick_size).split(".")[1].rstrip("0"))
        
    # 3. String formatting is the only way to guarantee exact precision in Python
    formatted_price = f"{rounded_price:.{decimal_places}f}"
    return float(formatted_price)

@app.post("/execute_signal")
async def execute_trade(signal: TradeSignal):
    try:
        if not mt5.terminal_info(): raise HTTPException(500, "MT5 Disconnected")

        symbol_info = mt5.symbol_info(signal.symbol)
        if not symbol_info: raise HTTPException(400, "Symbol not found")

        # Metadata
        tick_size = symbol_info.trade_tick_size
        point = symbol_info.point
        stops_level = symbol_info.trade_stops_level * point # Convert points to price
        
        # Order Type
        action_map = {
            "buy": mt5.ORDER_TYPE_BUY,
            "sell": mt5.ORDER_TYPE_SELL,
            "buy_stop": mt5.ORDER_TYPE_BUY_STOP,   
            "sell_stop": mt5.ORDER_TYPE_SELL_STOP  
        }
        order_type = action_map.get(signal.action.lower())
        if order_type is None: raise HTTPException(400, "Invalid action")

        # --- LIVE MARKET DATA CHECK ---
        tick = mt5.symbol_info_tick(signal.symbol)
        if not tick: raise HTTPException(500, "No tick data")

        # 1. Determine Raw Price
        if "stop" in signal.action.lower():
            raw_price = signal.price
            trade_action = mt5.TRADE_ACTION_PENDING
        else:
            raw_price = tick.ask if signal.action.lower() == "buy" else tick.bid
            trade_action = mt5.TRADE_ACTION_DEAL

        # 2. STRICT VALIDATION: Stops Level & Direction
        # If pending order is too close or on wrong side, MT5 throws 10015
        if "buy_stop" in signal.action.lower():
            min_price = tick.ask + stops_level
            if raw_price < min_price:
                print(f"⚠️ Adjustment: Buy Stop {raw_price} too close/low (Ask: {tick.ask}). Moving to {min_price}")
                raw_price = min_price
                
        elif "sell_stop" in signal.action.lower():
            max_price = tick.bid - stops_level
            if raw_price > max_price:
                print(f"⚠️ Adjustment: Sell Stop {raw_price} too close/high (Bid: {tick.bid}). Moving to {max_price}")
                raw_price = max_price

        # 3. Normalize (The Fix for 10015)
        price = normalize_price(raw_price, tick_size)

        # 4. SL/TP Calculation
        if "buy" in signal.action.lower():
            sl = price - signal.sl_points
            tp = price + signal.tp_points
        else:
            sl = price + signal.sl_points
            tp = price - signal.tp_points

        # Normalize SL/TP
        sl = normalize_price(sl, tick_size) if signal.sl_points > 0 else 0.0
        tp = normalize_price(tp, tick_size) if signal.tp_points > 0 else 0.0

        # 5. Build Request (REMOVED type_filling to use Auto/Default)
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
            # "type_filling": REMOVED to restore "old commit" behavior
        }

        print(f"📡 Sending: {signal.action} @ {price} | SL: {sl} | TP: {tp}")
        result = mt5.order_send(request)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error_msg = result.comment if result else "Unknown"
            print(f"❌ Order Failed: {error_msg} ({result.retcode if result else '?'})")
            
            # Expanded Debugging
            if result and result.retcode == 10015:
                 print(f"⚠️ DEBUG: Sent Price: {price} | Tick: {tick_size}")
                 print(f"⚠️ DEBUG: Market Ask: {tick.ask} | Bid: {tick.bid}")
                 print(f"⚠️ DEBUG: Stops Level: {stops_level}")

            raise HTTPException(500, f"MT5 Error: {error_msg}")

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
    return {"canceled": count}

@app.post("/close_all")
def close_all_positions():
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
    symbol_info = mt5.symbol_info(SYMBOL)
    
    return {
        "balance": account.balance,
        "equity": account.equity,
        "positions_count": len(positions) if positions else 0,
        "current_price": tick.ask if tick else 0,
        "symbol": SYMBOL,
        "point": symbol_info.point if symbol_info else 0.001 
    }

@app.get("/recent_deals")
def get_recent_deals(seconds: int = 60):
    if not mt5.terminal_info(): return []
    from datetime import datetime, timedelta
    d = mt5.history_deals_get(datetime.now() - timedelta(seconds=seconds), datetime.now())
    if not d: return []
    return [{"ticket": x.ticket, "type": x.type, "profit": x.profit, "symbol": x.symbol} for x in d if x.symbol == SYMBOL]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=BRIDGE_PORT)