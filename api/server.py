from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from core.bot_manager import BotManager
from core.engine import TradingEngine 
from supabase import create_client, Client
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Initialize Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# 2. Initialize Core Systems
bot_manager = BotManager()
# CRITICAL: Initialize the Engine that drives the bots
trading_engine = TradingEngine(bot_manager)

# 3. Start the Engine Loop on Server Startup
@app.on_event("startup")
async def startup_event():
    print("🚀 Server Starting: Launching Trading Engine Loop...")
    asyncio.create_task(trading_engine.start())

class ConfigUpdate(BaseModel):
    symbol: str | None = None
    spread: float | None = None
    buy_stop_tp: float | None = None
    buy_stop_sl: float | None = None
    sell_stop_tp: float | None = None
    sell_stop_sl: float | None = None
    step_lots: List[float] | None = None
    max_positions: int | None = None
    max_runtime_minutes: int | None = None
    max_drawdown_usd: float | None = None

# --- Dependency: Verify Supabase Token & Get Bot ---
async def get_current_bot(request: Request):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    
    token = auth_header.split(" ")[1]
    
    try:
        user_data = supabase.auth.get_user(token)
        if not user_data or not user_data.user:
             raise HTTPException(status_code=401, detail="Invalid Supabase Token")

        user_id = user_data.user.id
        return await bot_manager.get_or_create_bot(user_id)
        
    except Exception as e:
        print(f"Auth Error: {e}")
        raise HTTPException(status_code=401, detail="Session expired")

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

@app.get("/env")
async def get_env():
    return { "SUPABASE_URL": SUPABASE_URL, "SUPABASE_KEY": SUPABASE_KEY }

@app.get("/config")
async def get_config(bot = Depends(get_current_bot)):
    return bot.config

@app.post("/config")
async def update_config(config: ConfigUpdate, bot = Depends(get_current_bot)):
    old_symbol = bot.config.get('symbol')
    new_config = {k: v for k, v in config.model_dump().items() if v is not None}
    
    updated = bot.config_manager.update_config(new_config)
    
    if config.symbol and config.symbol != old_symbol:
        print(f"Symbol changed from {old_symbol} to {config.symbol}. Resetting logic...")
        await bot.start_ticker()
        
    return updated

@app.post("/control/start")
async def start_bot(bot = Depends(get_current_bot)):
    if bot.running:
        return {"message": "Bot is already running"}
    await bot.start()
    return {"message": "Bot started"}

@app.post("/control/stop")
async def stop_bot(bot = Depends(get_current_bot)):
    if not bot.running:
        return {"message": "Bot is not running"}
    await bot.stop()
    return {"message": "Bot stopped"}

@app.get("/status")
async def get_status(bot = Depends(get_current_bot)):
    return bot.get_status()