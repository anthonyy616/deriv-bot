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
        print("⚙️ Engine: Initializing Direct MT5 Connection (Monolith)...")
        
        if not mt5.initialize(path=self.path):
            print(f"❌ MT5 Init Failed: {mt5.last_error()}")
            return
            
        if not mt5.login(self.login, password=self.password, server=self.server):
            print(f"❌ MT5 Login Failed: {mt5.last_error()}")
            return
            
        print("✅ MT5 Connected. Starting High-Speed Loop.")
        await self.run_tick_loop()

    async def run_tick_loop(self):
        # We assume single-tenant or same-symbol for efficiency in this loop
        current_symbol = "FX Vol 20"
        
        while self.running:
            try:
                # Dynamic Symbol from Strategy
                bots = list(self.bot_manager.bots.values())
                if bots:
                    current_symbol = bots[0].config.get('symbol', current_symbol)
                    
                    # Ensure Symbol Selected
                    mt5.symbol_select(current_symbol, True)
                    
                    # Direct API Call - Zero Network Latency
                    tick = mt5.symbol_info_tick(current_symbol)
                    
                    if tick:
                        # Direct Position Check
                        positions = mt5.positions_get(symbol=current_symbol)
                        pos_count = len(positions) if positions else 0
                        
                        tick_data = {
                            'ask': tick.ask, 
                            'bid': tick.bid,
                            'positions_count': pos_count
                        }
                        
                        # In-Memory Function Call
                        await asyncio.gather(*[bot.on_external_tick(tick_data) for bot in bots])
                        
            except Exception as e:
                print(f"Engine Error: {e}")
                
            # Zero Sleep for max performance
            await asyncio.sleep(0)

    async def stop(self):
        self.running = False
        mt5.shutdown()
        print("🛑 MT5 Disconnected.")