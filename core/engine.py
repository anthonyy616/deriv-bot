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
        self.session = None

    async def start(self):
        print("⚙️ Engine: High-Speed Poll Loop Active.")
        # Persistent Session prevents TCP handshake overhead on every tick
        self.session = aiohttp.ClientSession()
        await self.run_tick_loop()

    async def run_tick_loop(self):
        while self.running:
            try:
                if self.session.closed:
                    self.session = aiohttp.ClientSession()

                # Non-blocking poll with fast timeout
                async with self.session.get(f"{self.bridge_url}/account_info", timeout=0.5) as res:
                    if res.status == 200:
                        data = await res.json()
                        
                        price = data.get('current_price', 0)
                        positions_count = data.get('positions_count', 0)
                        
                        if price > 0:
                            tick_data = {
                                'ask': price, 
                                'bid': price,
                                'positions_count': positions_count 
                            }
                            
                            # Push to bots immediately
                            active_bots = [b for b in self.bot_manager.bots.values() if b.running]
                            if active_bots:
                                await asyncio.gather(
                                    *[bot.on_external_tick(tick_data) for bot in active_bots]
                                )
                                    
            except Exception:
                # Silently ignore dropped frames to maintain high Hz
                pass
            
            # OVERCLOCK: Run as fast as CPU/Network allows
            await asyncio.sleep(0.001)

    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()