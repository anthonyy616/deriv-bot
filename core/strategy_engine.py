import asyncio
import time
import aiohttp
import os

class GridStrategy:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.symbol = config_manager.get_config().get('symbol', 'FX Vol 20')
        self.running = False
        
        # Connection
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8001")
        
        # --- State Management ---
        self.level_top = None    
        self.level_bottom = None 
        self.level_center = None 
        self.state = "START"
        self.current_step = 0
        self.last_trigger_time = 0
        self.current_price = 0 
        self.session = None
        self.start_time = 0 
        
        # Interrupt State
        self.last_processed_ticket = 0

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        self.reset_cycle()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.start_time = time.time()
        
        # 1. Sync Sequence Number (Prevent processing old deals)
        try:
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=60", timeout=2) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    if deals:
                        self.last_processed_ticket = max(d['ticket'] for d in deals)
        except Exception:
            pass

        self.reset_cycle()
        print(f"✅ Strategy Started. Symbol: {self.symbol} | Sequence: {self.last_processed_ticket}")

    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()
            self.session = None
        print("🛑 Strategy Stopped.")

    def reset_cycle(self):
        """ Soft Reset: Wipes memory to start a fresh round """
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.state = "START"
        self.current_step = 0
        self.last_trigger_time = 0
        print("🔄 RAM Cleared: Waiting for next tick to start new round...")

    async def on_external_tick(self, tick_data):
        if not self.running: return
        self.current_price = tick_data['ask']

        # 0. INTERRUPT CHECK
        # If this returns True, we STOP processing this tick immediately.
        if await self.check_stopping_conditions():
            return

        # ... Normal Business Logic ...
        ask = tick_data['ask']
        bid = tick_data['bid']
        point = tick_data.get('point', 0.001)
        
        if self.level_center is None:
            spread_points = self.config.get('spread', 2) 
            spread_val = spread_points * point 
            self.level_center = ask 
            self.level_top = self.level_center + spread_val
            self.level_bottom = self.level_center - spread_val
            print(f"🎯 New Round: Center={self.level_center:.5f} | Top={self.level_top:.5f} | Bottom={self.level_bottom:.5f}")
            return

        await self.execute_logic(ask, bid, point)

    async def execute_logic(self, ask, bid, point):
        # ... (Same Logic as before) ...
        if time.time() - self.last_trigger_time < 0.5: return

        max_pos = self.config.get('max_positions', 5)
        if self.current_step >= max_pos: return
            
        step_lots = self.config.get('step_lots', [])
        current_vol = step_lots[self.current_step] if self.current_step < len(step_lots) else (step_lots[-1] if step_lots else 0.01)

        if self.state == "START":
            if ask >= self.level_top:
                if await self.send_trade("buy", current_vol, point):
                    self.state = "TOP_HIT" 
            elif bid <= self.level_bottom:
                if await self.send_trade("sell", current_vol, point):
                    self.state = "BOTTOM_HIT" 

        elif self.state == "TOP_HIT":
            if bid <= self.level_center:
                if await self.send_trade("sell", current_vol, point):
                    self.state = "CENTER_FROM_TOP" 

        elif self.state == "BOTTOM_HIT":
            if ask >= self.level_center:
                if await self.send_trade("buy", current_vol, point):
                    self.state = "CENTER_FROM_BOTTOM" 

        elif self.state == "CENTER_FROM_TOP":
            if ask >= self.level_top:
                if await self.send_trade("buy", current_vol, point):
                    self.state = "TOP_HIT"

        elif self.state == "CENTER_FROM_BOTTOM":
            if bid <= self.level_bottom:
                if await self.send_trade("sell", current_vol, point):
                    self.state = "BOTTOM_HIT"

    async def send_trade(self, action, volume, point):
        if not self.session: return False
        
        tp_usd = self.config.get('buy_stop_tp') if action == 'buy' else self.config.get('sell_stop_tp')
        sl_usd = self.config.get('buy_stop_sl') if action == 'buy' else self.config.get('sell_stop_sl')
        
        try:
            tp_points = int(abs(tp_usd) / point) if tp_usd > 0 else 0
            sl_points = int(abs(sl_usd) / point) if sl_usd > 0 else 0
        except:
            tp_points = 100
            sl_points = 100

        payload = {
            "action": action,
            "symbol": self.symbol,
            "volume": float(volume),
            "sl_points": sl_points,
            "tp_points": tp_points,
            "comment": f"Step {self.current_step}"
        }
        
        try:
            async with self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload, timeout=2) as resp:
                if resp.status == 200:
                    self.current_step += 1
                    self.last_trigger_time = time.time()
                    print(f"⚡ ORDER SENT: {action.upper()} | Step {self.current_step}")
                    return True
        except Exception as e:
            print(f"❌ Execution Error: {e}")
        return False

    # --- THE INTERRUPT SYSTEM ---
    
    async def check_stopping_conditions(self):
        """ The Detector: Scans for signals to trigger the interrupt """
        if not self.session: return False
        
        try:
            # 1. Soft Interrupt: TP/SL Hit
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=5", timeout=2) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    
                    # Filter for NEW events only
                    new_deals = [d for d in deals if d['ticket'] > self.last_processed_ticket]
                    
                    if new_deals:
                        # Update Sequence (Consume the event)
                        self.last_processed_ticket = max(d['ticket'] for d in new_deals)
                        
                        tp_hit = any(d['profit'] > 0 for d in new_deals)
                        sl_hit = any(d['profit'] < 0 for d in new_deals)
                        
                        if tp_hit or sl_hit:
                            msg = "🎉 TP Hit!" if tp_hit else "💀 SL Hit!"
                            print(f"⚡ INTERRUPT SIGNAL: {msg}")
                            await self.execute_soft_interrupt() # TRIGGER HANDLER
                            return True

            # 2. Hard Interrupt: Max Drawdown / Runtime
            # (If these hit, we stop the bot entirely, not just reset)
            # ... (omitted for brevity, same as before but calls self.stop()) ...
            
        except Exception:
            pass
        return False

    async def execute_soft_interrupt(self):
        """ The Handler: Performs the clean-up and reset """
        print(">>> HANDLER: Executing Close All & State Reset...")
        
        # 1. Hardware Kill (Bridge)
        try:
            await self.session.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
        except:
            print("⚠️ Bridge Unreachable during reset")

        # 2. Memory Wipe (Strategy)
        self.reset_cycle()
        
        # 3. Cooldown (Wait for dust to settle)
        await asyncio.sleep(1.0)
        print(">>> HANDLER: System Ready.")

        

    def get_status(self):
        return {
            "running": self.running,
            "symbol": self.symbol,
            "current_price": self.current_price,
            "positions_count": self.current_step,
            "config": self.config
        }