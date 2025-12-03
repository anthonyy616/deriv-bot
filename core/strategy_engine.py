import asyncio
import time
import aiohttp
import os

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.running = False
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.current_step = 0
        self.session = None
        self.last_processed_ticket = 0
        
        # FIX: Store current price for UI
        self.current_price = 0.0

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        self.reset_cycle()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.reset_cycle()
        asyncio.create_task(self.run_watchdog())
        print(f"✅ Strategy Started: {self.symbol}")

    async def stop(self):
        self.running = False
        if self.session: await self.session.close()

    def reset_cycle(self):
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.current_step = 0
        print("🔄 Cycle Reset")

    async def on_external_tick(self, tick_data):
        if not self.running: return

        ask = tick_data['ask']
        self.current_price = ask # FIX: Update live price

        if self.level_center is None:
            # FIX: Logic uses raw spread (e.g. 6.0), NO POINT MULTIPLIER
            spread_val = float(self.config.get('spread', 6.0))
            
            self.level_center = ask
            self.level_top = self.level_center + spread_val
            self.level_bottom = self.level_center - spread_val
            
            print(f"🎯 Levels Set | Top: {self.level_top} | Center: {self.level_center} | Bottom: {self.level_bottom}")
            
            vol = self.get_volume(0)
            await self.send_pending("buy_stop", self.level_top, vol)
            await self.send_pending("sell_stop", self.level_bottom, vol)

    async def run_watchdog(self):
        while self.running:
            try:
                await self.check_deals()
            except: pass
            await asyncio.sleep(0.5)

    async def check_deals(self):
        if not self.session: return
        async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=10") as resp:
            if resp.status != 200: return
            deals = await resp.json()

        new_deals = [d for d in deals if d['ticket'] > self.last_processed_ticket]
        if not new_deals: return
        self.last_processed_ticket = max(d['ticket'] for d in new_deals)

        for deal in new_deals:
            if deal['profit'] != 0:
                print(f"🚨 TP/SL HIT -> RESET")
                await self.session.post(f"{self.mt5_bridge_url}/close_all")
                self.reset_cycle()
                return

            # Entry Logic
            self.current_step += 1
            vol = self.get_volume(self.current_step)
            deal_type = deal['type'] # 0=Buy, 1=Sell

            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")

            if deal_type == 0: # Bought
                # Place Sell Stop at Center
                await self.send_pending("sell_stop", self.level_center, vol)
            elif deal_type == 1: # Sold
                # Place Buy Stop at Top
                await self.send_pending("buy_stop", self.level_top, vol)

    async def send_pending(self, action, price, volume):
        # FIX: Send raw SL/TP distances
        if "buy" in action.lower():
            sl = float(self.config.get('buy_stop_sl', 0))
            tp = float(self.config.get('buy_stop_tp', 0))
        else:
            sl = float(self.config.get('sell_stop_sl', 0))
            tp = float(self.config.get('sell_stop_tp', 0))

        payload = {
            "action": action,
            "symbol": self.symbol,
            "volume": float(volume),
            "price": float(price),
            "sl_points": sl,
            "tp_points": tp,
            "comment": f"Step {self.current_step}"
        }
        await self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload)

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        return step_lots[step] if step < len(step_lots) else (step_lots[-1] if step_lots else 0.01)

    def get_status(self):
        # FIX: Return current_price so UI can display it
        return {
            "running": self.running,
            "current_price": self.current_price, 
            "step": self.current_step,
            "top": self.level_top,
            "center": self.level_center,
            "bottom": self.level_bottom
        }