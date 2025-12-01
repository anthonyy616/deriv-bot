from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional  # <--- Make sure to import List
from core.bot_manager import BotManager
import asyncio
import os
from dotenv import load_dotenv
import json

load_dotenv()

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Global Bot Manager
bot_manager = BotManager()

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

@app.get("/env")
async def get_env():
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_KEY": os.getenv("SUPABASE_KEY")
    }

class ConnectRequest(BaseModel):
    name: str = "Trader"

# --- UPDATED CONFIG MODEL (Fixes 400 Error) ---
class ConfigUpdate(BaseModel):
    symbol: str | None = None
    spread: float | None = None
    # New Fields for Split TP/SL
    buy_stop_tp: float | None = None
    buy_stop_sl: float | None = None
    sell_stop_tp: float | None = None
    sell_stop_sl: float | None = None
    # New Fields for Lots
    step_lots: List[float] | None = None
    # Existing
    max_positions: int | None = None
    lot_size: float | None = None
    max_runtime_minutes: int | None = None
    max_drawdown_usd: float | None = None

async def get_bot_or_raise(x_session_id: str = Header(None)):
    if not x_session_id:
        raise HTTPException(status_code=401, detail="Missing X-Session-ID header")
    
    bot = bot_manager.get_bot(x_session_id)
    if not bot:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return bot

@app.post("/connect")
async def connect(request: ConnectRequest):
    try:
        session_id = await bot_manager.create_bot(token="dummy", app_id="dummy")
        return {"session_id": session_id, "message": "Connected successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/config")
async def get_config(bot = Depends(get_bot_or_raise)):
    return bot.config

@app.post("/config")
async def update_config(config: ConfigUpdate, bot = Depends(get_bot_or_raise)):
    old_symbol = bot.config.get('symbol')
    # Filter out None values so we don't overwrite existing settings with Null
    new_config = {k: v for k, v in config.model_dump().items() if v is not None}
    
    updated = bot.config_manager.update_config(new_config)
    
    if config.symbol and config.symbol != old_symbol:
        print(f"Symbol changed from {old_symbol} to {config.symbol}. Restarting ticker...")
        await bot.start_ticker()
        
    return updated

@app.post("/control/start")
async def start_bot(bot = Depends(get_bot_or_raise)):
    if bot.running:
        return {"message": "Bot is already running"}
    asyncio.create_task(bot.start())
    return {"message": "Bot started"}

@app.post("/control/stop")
async def stop_bot(bot = Depends(get_bot_or_raise)):
    if not bot.running:
        return {"message": "Bot is not running"}
    await bot.stop()
    return {"message": "Bot stopped"}

@app.get("/status")
async def get_status(x_session_id: str = Header(None)):
    if not x_session_id:
        return {"status": "Not initialized", "running": False}

    bot = bot_manager.get_bot(x_session_id)
    if not bot:
        # This returns 401 if bot is not found (e.g. after server restart)
        return {"status": "Invalid Session", "running": False} 
        
    return bot.get_status()