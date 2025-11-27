import asyncio
import uuid
from typing import Dict
from core.deriv_client import DerivClient
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
        
        # Initialize components with specific user credentials
        client = DerivClient(app_id=app_id, api_token=token)
        
        # Connect to verify credentials
        try:
            await client.connect()
        except Exception as e:
            print(f"Failed to connect for session {session_id}: {e}")
            raise e

        # Initialize ConfigManager with unique session ID
        config_manager = ConfigManager(user_id=session_id)
        
        # Initialize Strategy
        strategy = GridStrategy(client, config_manager)
        
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
            # Optionally remove from memory or keep for logs?
            # For now, keep it but mark as stopped.
            # If we want to fully cleanup:
            # await bot.client.disconnect()
            # del self.bots[session_id]
            print(f"Bot stopped for session: {session_id}")

    async def stop_all(self):
        for session_id in list(self.bots.keys()):
            await self.stop_bot(session_id)
