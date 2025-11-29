import asyncio
import logging
import uvicorn
from api.server import app, bot_manager
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trade_bot.log"),
        logging.StreamHandler()
    ]
)

class TickData(BaseModel):
    symbol: str
    bid: float
    ask: float
    time: int

@app.post("/tick")
async def receive_tick(tick: TickData):
    """Receives ticks from the MT5 Bridge and dispatches to active bots."""
    # Broadcast to all active bots (or filter by symbol)
    # Since we don't have a session mapping for the bridge, we broadcast to all.
    for session_id, bot in bot_manager.bots.items():
        if bot.running and bot.symbol == tick.symbol:
            await bot.on_external_tick(tick.dict())
    return {"status": "ok"}

if __name__ == "__main__":
    try:
        print("Starting Signal Server on port 8000...")
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    except KeyboardInterrupt:
        print("Server stopped by user.")