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
from cachetools import TTLCache 

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Auth Cache (60 seconds)
auth_cache = TTLCache(maxsize=100, ttl=60)

app.mount("/", StaticFiles(directory="static", html=True), name="static")

bot_manager = BotManager()
trading_engine = TradingEngine(bot_manager)

@app.on_event("startup")
async def startup_event():
    print("🚀 Server Starting: Launching Monolith Engine...")
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

def verify_token_sync(token):
    if token in auth_cache: return auth_cache[token]
    user = supabase.auth.get_user(token)
    if user and user.user:
        auth_cache[token] = user
        return user
    return None

async def get_current_bot(request: Request):
    auth_header = request.headers.get('Authorization')
    if not auth_header: raise HTTPException(401, "Missing token")
    
    try:
        user = await asyncio.to_thread(verify_token_sync, auth_header.split(" ")[1])
        if not user: raise HTTPException(401, "Invalid Token")
        return await bot_manager.get_or_create_bot(user.user.id)
    except Exception:
        raise HTTPException(401, "Auth Failed")

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
    old_sym = bot.config.get('symbol')
    data = {k: v for k, v in config.model_dump().items() if v is not None}
    bot.config_manager.update_config(data)
    if config.symbol and config.symbol != old_sym:
        await bot.start_ticker()
    return True

@app.post("/control/start")
async def start_bot(bot = Depends(get_current_bot)):
    await bot.start()
    return {"status": "started"}

@app.post("/control/stop")
async def stop_bot(bot = Depends(get_current_bot)):
    await bot.stop()
    return {"status": "stopped"}

@app.get("/status")
async def get_status(bot = Depends(get_current_bot)):
    return bot.get_status()