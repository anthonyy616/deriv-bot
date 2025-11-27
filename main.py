import asyncio
import logging
import uvicorn
from api.server import app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trade_bot.log"),
        logging.StreamHandler()
    ]
)

if __name__ == "__main__":
    try:
        print("Starting API Server on port 8000...")
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    except KeyboardInterrupt:
        print("Server stopped by user.")