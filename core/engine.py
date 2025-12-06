import asyncio
import aiohttp # CHANGED: Use async library
import os
from dotenv import load_dotenv

load_dotenv()

class TradingEngine:
    def __init__(self, bot_manager):
        self.bot_manager = bot_manager
        self.running = True
        self.bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        # Reuse session for performance
        self.session = None

    async def start(self):
        print("⚙️ Engine: Bridge Poll Loop Active (Async Mode).")
        # Create persistent session
        self.session = aiohttp.ClientSession()
        await self.run_tick_loop()

    async def run_tick_loop(self):
        while self.running:
            try:
                if not self.session:
                    self.session = aiohttp.ClientSession()

                # 1. Non-blocking Poll to Bridge
                async with self.session.get(f"{self.bridge_url}/account_info", timeout=2) as res:
                    if res.status == 200:
                        data = await res.json()
                        price = data.get('current_price', 0)
                        point = data.get('point', 0.001)
                        if point == 0: point = 0.001
                        
                        # Capture positions for UI/Strategy
                        positions_count = data.get('positions_count', 0)

                        if price > 0:
                            # Construct tick data
                            tick_data = {
                                'symbol': data.get('symbol', 'FX Vol 20'),
                                'ask': price, 
                                'bid': price, 
                                'point': point,
                                'positions_count': positions_count 
                            }
                            
                            # 2. Push tick to all ACTIVE bots (Async)
                            active_bots = [b for b in self.bot_manager.bots.values() if b.running]
                            
                            if active_bots:
                                await asyncio.gather(
                                    *[bot.on_external_tick(tick_data) for bot in active_bots]
                                )
                                    
            except Exception as e:
                # Log error but don't crash
                # print(f"Engine Polling Error: {e}") 
                pass
            
            # 3. Poll Frequency (100ms)
            await asyncio.sleep(0.1)

    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()
            self.session = None