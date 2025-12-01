from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
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

class ConfigUpdate(BaseModel):
    symbol: str | None = None
    spread: float | None = None
    step_lots: list[float] | None = None
    buy_stop_tp: float | None = None
    buy_stop_sl: float | None = None
    sell_stop_tp: float | None = None
    sell_stop_sl: float | None = None
    max_positions: int | None = None
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
        # No token/app_id needed anymore. Just create a session.
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
    new_config = {k: v for k, v in config.dict().items() if v is not None}
    updated = bot.config_manager.update_config(new_config)
    
    # If symbol changed, restart ticker
    if config.symbol and config.symbol != old_symbol:
        print(f"Symbol changed from {old_symbol} to {config.symbol}. Restarting ticker...")
        await bot.start_ticker()
        
    return updated

@app.post("/control/start")
async def start_bot(bot = Depends(get_bot_or_raise)):
    if bot.running:
        return {"message": "Bot is already running"}
    
    # Start the strategy in the background
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
        raise HTTPException(status_code=401, detail="Invalid or expired session")
        
    return bot.get_status()
