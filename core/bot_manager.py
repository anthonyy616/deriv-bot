import asyncio
import uuid
from typing import Dict
from core.config_manager import ConfigManager
from core.strategy_engine import GridStrategy

class BotManager:
    def __init__(self):
        self.bots: Dict[str, GridStrategy] = {}

    async def create_bot(self, token: str, app_id: str) -> str:
        """
        Creates a new bot instance for a user.
        Returns: session_id
        """
        session_id = str(uuid.uuid4())
        
        # Initialize ConfigManager with unique session ID
        config_manager = ConfigManager(user_id=session_id)
        
        # Initialize Strategy (No DerivClient needed)
        strategy = GridStrategy(config_manager)
        
        # Start Ticker (Passive)
        await strategy.start_ticker()
        
        # Store in memory
        self.bots[session_id] = strategy
        
        print(f"Bot created for session: {session_id}")
        return session_id

    def get_bot(self, session_id: str) -> GridStrategy:
        return self.bots.get(session_id)

    async def stop_bot(self, session_id: str):
        bot = self.bots.get(session_id)
        if bot:
            await bot.stop()
            print(f"Bot stopped for session: {session_id}")

    async def stop_all(self):
        for session_id in list(self.bots.keys()):
            await self.stop_bot(session_id)
