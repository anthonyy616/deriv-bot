import asyncio
import os
from deriv_api import DerivAPI
from dotenv import load_dotenv

load_dotenv()

class DerivClient:
    def __init__(self, app_id, api_token):
        self.app_id = app_id
        self.api_token = api_token
        self.api = None

    async def connect(self):
        if not self.app_id or not self.api_token:
            raise ValueError("App ID and API Token must be provided.")
        
        try:
            self.api = DerivAPI(app_id=self.app_id)
            
            # Authorize
            authorize = await self.api.authorize(self.api_token)
            self.account_info = {
                "email": authorize['authorize']['email'],
                "loginid": authorize['authorize']['loginid'],
                "balance": authorize['authorize']['balance'],
                "currency": authorize['authorize']['currency'],
                "fullname": authorize['authorize']['fullname']
            }
            print(f"Authorized: {self.account_info['email']} (ID: {self.account_info['loginid']})")
            print(f"Account Balance: {self.account_info['balance']} {self.account_info['currency']}")
            
            # Start Keep-Alive Loop
            asyncio.create_task(self._keep_alive())
            
            return self.api
        except Exception as e:
            print(f"Connection Error: {e}")
            raise e

    async def _keep_alive(self):
        """Sends a ping every 30 seconds to keep the connection alive."""
        while True:
            try:
                await asyncio.sleep(30)
                if self.api:
                    await self.api.ping({'ping': 1})
            except Exception as e:
                print(f"Keep-Alive Error: {e}")
                break

    async def subscribe_ticks(self, symbol):
        source_tick = await self.api.subscribe({'ticks': symbol})
        return source_tick

    async def get_balance(self):
        response = await self.api.balance()
        return response['balance']

    async def buy_contract(self, contract_type, amount, symbol, duration, duration_unit, barrier=None):
        """
        Executes a trade by first getting a proposal and then buying it.
        contract_type: "CALL" (Rise) or "PUT" (Fall)
        """
        try:
            # 1. Get Proposal
            proposal_req = {
                "proposal": 1,
                "amount": amount,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "duration": duration,
                "duration_unit": duration_unit,
                "symbol": symbol
            }
            if barrier:
                proposal_req["barrier"] = barrier

            proposal = await self.api.proposal(proposal_req)
            
            if 'error' in proposal:
                print(f"Proposal Error: {proposal['error']['message']}")
                return None

            proposal_id = proposal['proposal']['id']
            # print(f"Proposal ID: {proposal_id}")

            # 2. Buy
            buy = await self.api.buy({"buy": proposal_id, "price": proposal['proposal']['ask_price']})
            
            if 'error' in buy:
                print(f"Buy Error: {buy['error']['message']}")
                return None
                
            print(f"Trade Executed! Contract ID: {buy['buy']['contract_id']}")
            return buy['buy']

        except Exception as e:
            print(f"Exception during buy_contract: {e}")
            return None

    async def buy_multiplier(self, contract_type, amount, symbol, multiplier, stop_loss, take_profit):
        """
        Opens a multiplier position with TP/SL.
        contract_type: "MULTUP" (buy) or "MULTDOWN" (sell)
        amount: stake amount in USD
        stop_loss: absolute price for SL
        take_profit: absolute price for TP
        multiplier: leverage multiplier (e.g., 10, 25, 50)
        """
        try:
            proposal_req = {
                "proposal": 1,
                "amount": amount,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "symbol": symbol,
                "multiplier": str(multiplier),
                "stop_loss": str(stop_loss),
                "take_profit": str(take_profit)
            }
            
            proposal = await self.api.proposal(proposal_req)
            
            if 'error' in proposal:
                print(f"Multiplier Proposal Error: {proposal['error']['message']}")
                return None

            proposal_id = proposal['proposal']['id']
            
            # Buy the contract
            buy = await self.api.buy({"buy": proposal_id, "price": proposal['proposal']['ask_price']})
            
            if 'error' in buy:
                print(f"Multiplier Buy Error: {buy['error']['message']}")
                return None
                
            print(f"Multiplier Trade Executed! Contract ID: {buy['buy']['contract_id']}")
            return buy['buy']

        except Exception as e:
            print(f"Exception during buy_multiplier: {e}")
            return None
    
    async def get_contract_status(self, contract_id):
        """Get the current status of a contract"""
        try:
            response = await self.api.proposal_open_contract({"proposal_open_contract": 1, "contract_id": contract_id})
            if 'error' in response:
                print(f"Error fetching contract: {response['error']['message']}")
                return None
            return response['proposal_open_contract']
        except Exception as e:
            print(f"Exception fetching contract status: {e}")
            return None

    async def disconnect(self):
        if self.api:
            await self.api.disconnect()
