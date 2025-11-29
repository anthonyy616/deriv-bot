from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from core.bot_manager import BotManager
import asyncio
import os
from dotenv import load_dotenv
import aiohttp
import json

load_dotenv()

# Simple Supabase Client using REST API to avoid websocket conflicts
class SupabaseClient:
    def __init__(self, url, key):
        self.url = url
        self.key = key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

    async def get_user_id_by_token(self, token):
        async with aiohttp.ClientSession() as session:
            # Query Profiles_API table
            endpoint = f"{self.url}/rest/v1/Profiles_API"
            params = {"API": f"eq.{token}", "select": "user_id"}
            async with session.get(endpoint, headers=self.headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        return data[0]['user_id']
                return None

    async def get_profile(self, user_id):
        async with aiohttp.ClientSession() as session:
            endpoint = f"{self.url}/rest/v1/Profiles"
            params = {"id": f"eq.{user_id}", "select": "selected_mt5_login"}
            async with session.get(endpoint, headers=self.headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        return data[0]
                return None

    async def update_profile(self, user_id, data):
        async with aiohttp.ClientSession() as session:
            endpoint = f"{self.url}/rest/v1/Profiles"
            params = {"id": f"eq.{user_id}"}
            async with session.patch(endpoint, headers=self.headers, params=params, json=data) as resp:
                return resp.status in [200, 204]

supabase_url: str = os.environ.get("SUPABASE_URL")
supabase_key: str = os.environ.get("SUPABASE_KEY")
supabase = SupabaseClient(supabase_url, supabase_key)

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
    token: str
    app_id: str

class ConfigUpdate(BaseModel):
    symbol: str | None = None
    spread: float | None = None
    tp_dist: float | None = None
    sl_dist: float | None = None
    max_positions: int | None = None
    lot_size: float | None = None
    max_runtime_minutes: int | None = None
    max_drawdown_usd: float | None = None

class SelectMT5Request(BaseModel):
    login: str

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
        session_id = await bot_manager.create_bot(request.token, request.app_id)
        
        # Check for saved MT5 login in Supabase
        try:
            user_id = await supabase.get_user_id_by_token(request.token)
            if user_id:
                profile = await supabase.get_profile(user_id)
                if profile and profile.get('selected_mt5_login'):
                    saved_login = profile['selected_mt5_login']
                    print(f"Found saved MT5 login: {saved_login}")
                    
                    # Update the bot
                    bot = bot_manager.get_bot(session_id)
                    if bot:
                        bot.set_mt5_login(saved_login)
        except Exception as e:
            print(f"Error fetching saved MT5 login: {e}")

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
    # Allow status check to return "not initialized" if no session, 
    # but for the dashboard logic, it's better to enforce session.
    # However, the frontend polls status immediately. 
    # If no session, frontend should handle 401.
    
    if not x_session_id:
        return {"status": "Not initialized", "running": False}

    bot = bot_manager.get_bot(x_session_id)
    if not bot:
        return {"status": "Invalid Session", "running": False}
        
    return bot.get_status()

@app.get("/supabase")
async def get_supabase():
    return {"supabase_url": os.getenv("SUPABASE_URL"), "supabase_key": os.getenv("SUPABASE_KEY")}

@app.post("/api/user/mt5-accounts")
async def get_mt5_accounts(bot = Depends(get_bot_or_raise)):
    accounts = await bot.client.get_mt5_accounts()
    return {"accounts": accounts}

@app.post("/api/user/select-mt5")
async def select_mt5_account(request: SelectMT5Request, x_session_id: str = Header(None), bot = Depends(get_bot_or_raise)):
    # 1. Update Bot
    bot.set_mt5_login(request.login)
    
    # 2. Update Database
    token = bot.client.api_token
    
    try:
        user_id = await supabase.get_user_id_by_token(token)
        if user_id:
            success = await supabase.update_profile(user_id, {"selected_mt5_login": request.login})
            if success:
                return {"message": f"Selected account {request.login}", "saved": True}
        
        return {"message": f"Selected account {request.login} (Session only)", "saved": False}
            
    except Exception as e:
        print(f"Error saving selection to DB: {e}")
        return {"message": f"Selected account {request.login} (Session only)", "error": str(e)}
