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

    async def buy_multiplier(self, contract_type, amount, symbol, multiplier, stop_loss, take_profit, login=None):
        """
        Opens a multiplier position with TP/SL.
        contract_type: "MULTUP" (buy) or "MULTDOWN" (sell)
        amount: stake amount in USD
        stop_loss: absolute price for SL
        take_profit: absolute price for TP
        multiplier: leverage multiplier (e.g., 10, 25, 50)
        login: Optional MT5 login ID to associate/force trade
        """
        try:
            # 1. Get Proposal (Clean, no TP/SL to avoid validation errors on proposal)
            proposal_req = {
                "proposal": 1,
                "amount": amount,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "symbol": symbol,
                "multiplier": str(multiplier)
            }
            
            if login:
                proposal_req["login"] = login

            
            proposal = await self.api.proposal(proposal_req)
            
            if 'error' in proposal:
                print(f"Multiplier Proposal Error: {proposal['error']['message']}")
                return None

            proposal_id = proposal['proposal']['id']
            ask_price = proposal['proposal']['ask_price']
            
            # 2. Calculate TP/SL Amounts (Required by Deriv API for Multipliers)
            # Formula: Profit = (Price_Diff / Entry_Price) * Stake * Multiplier
            
            amount = float(amount)
            multiplier = float(multiplier)
            ask_price = float(ask_price)
            stop_loss = float(stop_loss)
            take_profit = float(take_profit)

            tp_diff = abs(take_profit - ask_price)
            sl_diff = abs(stop_loss - ask_price)
            
            tp_amount = (tp_diff / ask_price) * amount * multiplier
            sl_amount = (sl_diff / ask_price) * amount * multiplier
            
            # Round to 2 decimals (Currency requirement)
            tp_amount = round(tp_amount, 2)
            sl_amount = round(sl_amount, 2)
            
            # Ensure minimum values (e.g. 0.01)
            tp_amount = max(0.01, tp_amount)
            sl_amount = max(0.01, sl_amount)
            
            print(f"Calculated TP: ${tp_amount}, SL: ${sl_amount} (Price: {ask_price})")

            # 3. Buy with limit_order
            buy_req = {
                "buy": proposal_id,
                "price": ask_price,
                "limit_order": {
                    "take_profit": tp_amount,
                    "stop_loss": sl_amount
                }
            }
            
            if login:
                buy_req["login"] = login
            
            buy = await self.api.buy(buy_req)
            
            if 'error' in buy:
                print(f"Multiplier Buy Error: {buy['error']['message']}")
                return None
                
            print(f"Multiplier Trade Executed! Contract ID: {buy['buy']['contract_id']}")
            return buy['buy']

        except Exception as e:
            print(f"Exception during buy_multiplier: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def get_contract_status(self, contract_id, login=None):
        """Get the current status of a contract"""
        try:
            req = {"proposal_open_contract": 1, "contract_id": contract_id}
            if login:
                req["login"] = login
            
            response = await self.api.proposal_open_contract(req)
            if 'error' in response:
                print(f"Error fetching contract: {response['error']['message']}")
                return None
            return response['proposal_open_contract']
        except Exception as e:
            print(f"Exception fetching contract status: {e}")
            return None

    async def get_mt5_accounts(self):
        """
        Fetches all MT5 accounts associated with the user's token.
        Returns a list of dictionaries with account details.
        """
        try:
            response = await self.api.mt5_login_list()
            
            if 'error' in response:
                print(f"Error fetching MT5 accounts: {response['error']['message']}")
                return []
                
            accounts = []
            for acc in response.get('mt5_login_list', []):
                # Filter/Format as needed
                accounts.append({
                    "login": acc.get('login'),
                    "group": acc.get('group'),
                    "market_type": acc.get('market_type'), # synthetic, financial, etc.
                    "sub_account_type": acc.get('sub_account_type'), # financial, financial_stp, etc.
                    "account_type": acc.get('account_type'), # demo, real
                    "balance": acc.get('balance'),
                    "currency": acc.get('currency'),
                    "leverage": acc.get('leverage'),
                    "display_name": f"{acc.get('market_type').capitalize()} - {acc.get('account_type').capitalize()} - {acc.get('login')}"
                })
            return accounts
            
        except Exception as e:
            print(f"Exception fetching MT5 accounts: {e}")
            return []

    async def disconnect(self):
        if self.api:
            await self.api.disconnect()
