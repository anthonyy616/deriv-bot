import asyncio
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

load_dotenv()

class TradingEngine:
    def __init__(self, bot_manager):
        self.bot_manager = bot_manager
        self.running = True
        
        # MT5 Configuration
        self.login = int(os.getenv("MT5_LOGIN", 0))
        self.password = os.getenv("MT5_PASSWORD", "")
        self.server = os.getenv("MT5_SERVER", "")
        self.path = os.getenv("MT5_PATH", "")

    async def start(self):
        print("⚙️ Engine: Initializing Direct MT5 Connection...")
        
        # 1. Initialize MT5 (Blocking call, run once)
        if not mt5.initialize(path=self.path):
            print(f"❌ MT5 Init Failed: {mt5.last_error()}")
            return
            
        if not mt5.login(self.login, password=self.password, server=self.server):
            print(f"❌ MT5 Login Failed: {mt5.last_error()}")
            return
            
        print("✅ MT5 Connected. Starting High-Speed Loop.")
        await self.run_tick_loop()

    async def run_tick_loop(self):
        # Cache symbol info to avoid API spam
        symbol = "FX Vol 20" # Default, will be updated by strategy
        
        while self.running:
            try:
                # 2. Direct API Call (Microsecond latency)
                # We assume the first bot determines the symbol for now
                bots = list(self.bot_manager.bots.values())
                if bots:
                    symbol = bots[0].config.get('symbol', symbol)
                    
                    # Ensure symbol is selected
                    mt5.symbol_select(symbol, True)
                    
                    # Get Tick
                    tick = mt5.symbol_info_tick(symbol)
                    
                    if tick:
                        # Get Positions Count (Direct)
                        positions = mt5.positions_get(symbol=symbol)
                        pos_count = len(positions) if positions else 0
                        
                        tick_data = {
                            'ask': tick.ask, 
                            'bid': tick.bid,
                            'positions_count': pos_count,
                            'point': mt5.symbol_info(symbol).point
                        }
                        
                        # 3. Fire Strategy Logic (Async)
                        await asyncio.gather(
                            *[bot.on_external_tick(tick_data) for bot in bots]
                        )
                        
            except Exception as e:
                print(f"Engine Loop Error: {e}")
                
            # 4. Yield control (Zero Sleep for Max Speed)
            await asyncio.sleep(0)

    async def stop(self):
        self.running = False
        mt5.shutdown()
        print("🛑 MT5 Disconnected.")