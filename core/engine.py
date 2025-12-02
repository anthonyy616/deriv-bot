import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

class TradingEngine:
    def __init__(self, bot_manager):
        self.bot_manager = bot_manager
        self.running = True
        self.bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")

    async def start(self):
        print("⚙️ Engine: Bridge Poll Loop Active.")
        await self.run_tick_loop()

    async def run_tick_loop(self):
        async with aiohttp.ClientSession() as session:
            while self.running:
                try:
                    # 1. Poll Bridge for latest market data
                    async with session.get(f"{self.bridge_url}/account_info", timeout=1) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            price = data.get('current_price', 0)
                            point = data.get('point', 0.001)
                            if point == 0: point = 0.001

                            if price > 0:
                                # Construct tick data
                                tick_data = {
                                    'symbol': data.get('symbol', 'FX Vol 20'),
                                    'ask': price, 
                                    'bid': price, 
                                    'point': point
                                }
                                
                                # 2. Push tick to all ACTIVE bots
                                active_bots = [b for b in self.bot_manager.bots.values() if b.running]
                                
                                if active_bots:
                                    await asyncio.gather(
                                        *[bot.on_external_tick(tick_data) for bot in active_bots]
                                    )
                                    
                except Exception as e:
                    # print(f"Engine Poll Error: {e}")
                    pass 
                
                # 3. Poll Frequency (100ms)
                await asyncio.sleep(0.1)

    def stop(self):
        self.running = False