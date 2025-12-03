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
        # 1. Fixed Price Levels
        self.level_top = None    
        self.level_bottom = None 
        self.level_center = None 
        
        # 2. Logic State (The "Stack")
        self.state = "START"
        
        self.current_step = 0
        self.last_trigger_time = 0
        self.current_price = 0 
        self.session = None
        self.start_time = 0 
        
        # 3. Deal Tracking (To prevent infinite reset loops)
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
        
        # Initialize last ticket to current max so we only look forward
        try:
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=60", timeout=2) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    if deals:
                        # Store the highest ticket number we see on startup
                        self.last_processed_ticket = max(d['ticket'] for d in deals)
        except Exception:
            pass

        self.reset_cycle()
        print(f"✅ Strategy Started. Symbol: {self.symbol} | Last Ticket: {self.last_processed_ticket}")

    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()
            self.session = None
        print("🛑 Strategy Stopped.")

    def reset_cycle(self):
        """ 
        Soft Reset: Clears levels/steps to start a fresh round.
        Does NOT clear 'last_processed_ticket' or 'running' state.
        """
        self.level_top = None
        self.level_bottom = None
        self.level_center = None
        self.state = "START"
        self.current_step = 0
        self.last_trigger_time = 0
        print("🔄 Round Reset: Clean state, waiting for next tick to start new round...")

    async def on_external_tick(self, tick_data):
        if not self.running: return
        
        # UI Update
        self.current_price = tick_data['ask']

        # 0. Check Termination (TP/SL/Max)
        if await self.check_stopping_conditions():
            return

        ask = tick_data['ask']
        bid = tick_data['bid']
        point = tick_data.get('point', 0.001)
        
        # 1. Initialization (First Tick of Round)
        if self.level_center is None:
            spread_points = self.config.get('spread', 2) 
            spread_val = spread_points * point 
            
            self.level_center = ask # Anchor at current price
            self.level_top = self.level_center + spread_val
            self.level_bottom = self.level_center - spread_val
            
            print(f"🎯 New Round: Center={self.level_center:.5f} | Top={self.level_top:.5f} | Bottom={self.level_bottom:.5f}")
            return

        # 2. Execute Ping-Pong Logic
        await self.execute_logic(ask, bid, point)

    async def execute_logic(self, ask, bid, point):
        if time.time() - self.last_trigger_time < 0.5:
            return

        max_pos = self.config.get('max_positions', 5)
        if self.current_step >= max_pos:
            return
            
        step_lots = self.config.get('step_lots', [])
        current_vol = step_lots[self.current_step] if self.current_step < len(step_lots) else (step_lots[-1] if step_lots else 0.01)

        # --- THE STATE MACHINE ---
        if self.state == "START":
            if ask >= self.level_top:
                print(f"⚡ Breakout UP: Hit Top ({self.level_top:.5f}) -> BUY")
                if await self.send_trade("buy", current_vol, point):
                    self.state = "TOP_HIT" 
                    print(f"➡ Next Target: Sell at Center ({self.level_center:.5f})")
            elif bid <= self.level_bottom:
                print(f"⚡ Breakout DOWN: Hit Bottom ({self.level_bottom:.5f}) -> SELL")
                if await self.send_trade("sell", current_vol, point):
                    self.state = "BOTTOM_HIT" 
                    print(f"➡ Next Target: Buy at Center ({self.level_center:.5f})")

        elif self.state == "TOP_HIT":
            if bid <= self.level_center:
                print(f"⚡ Pullback: Hit Center ({self.level_center:.5f}) -> SELL")
                if await self.send_trade("sell", current_vol, point):
                    self.state = "CENTER_FROM_TOP" 
                    print(f"➡ Next Target: Buy at Top ({self.level_top:.5f})")

        elif self.state == "BOTTOM_HIT":
            if ask >= self.level_center:
                print(f"⚡ Pullback: Hit Center ({self.level_center:.5f}) -> BUY")
                if await self.send_trade("buy", current_vol, point):
                    self.state = "CENTER_FROM_BOTTOM" 
                    print(f"➡ Next Target: Sell at Bottom ({self.level_bottom:.5f})")

        elif self.state == "CENTER_FROM_TOP":
            if ask >= self.level_top:
                print(f"⚡ Re-Test Top: Hit Top ({self.level_top:.5f}) -> BUY")
                if await self.send_trade("buy", current_vol, point):
                    self.state = "TOP_HIT"
                    print(f"➡ Next Target: Sell at Center")

        elif self.state == "CENTER_FROM_BOTTOM":
            if bid <= self.level_bottom:
                print(f"⚡ Re-Test Bottom: Hit Bottom ({self.level_bottom:.5f}) -> SELL")
                if await self.send_trade("sell", current_vol, point):
                    self.state = "BOTTOM_HIT"
                    print(f"➡ Next Target: Buy at Center")

    async def send_trade(self, action, volume, point):
        if not self.session: return False
        
        tp_usd = self.config.get('buy_stop_tp') if action == 'buy' else self.config.get('sell_stop_tp')
        sl_usd = self.config.get('buy_stop_sl') if action == 'buy' else self.config.get('sell_stop_sl')
        
        try:
            # Pure Distance: Input 10 = 10 Price Units
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
                    return True
        except Exception as e:
            print(f"❌ Execution Error: {e}")
        return False

    async def check_stopping_conditions(self):
        if not self.session: return False
        
        try:
            # 1. SOFT STOP: TP/SL Hit -> Reset Round (Close All & Restart)
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=5", timeout=2) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    
                    # Filter for NEW deals only (ticket > last_processed)
                    new_deals = [d for d in deals if d['ticket'] > self.last_processed_ticket]
                    
                    if new_deals:
                        # Update our watermark so we don't process these again
                        self.last_processed_ticket = max(d['ticket'] for d in new_deals)
                        
                        tp_hit = any(d['profit'] > 0 for d in new_deals)
                        sl_hit = any(d['profit'] < 0 for d in new_deals)
                        
                        if tp_hit or sl_hit:
                            log_msg = "🎉 TP Hit!" if tp_hit else "💀 SL Hit!"
                            print(f"{log_msg} Closing All & Starting New Round...")
                            
                            # A. Wipe the Board (Positions + Pending)
                            await self.session.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
                            
                            # B. Reset State (Config remains same)
                            self.reset_cycle()
                            
                            # C. Cooldown to ensure MT5 processes closes
                            await asyncio.sleep(2) 
                            return True

            # 2. HARD STOP: Max Drawdown / Max Time -> Fully Stop Bot
            async with self.session.get(f"{self.mt5_bridge_url}/account_info", timeout=2) as resp:
                if resp.status == 200:
                    info = await resp.json()
                    balance = info.get('balance', 0)
                    equity = info.get('equity', 0)
                    
                    max_dd = self.config.get('max_drawdown_usd', 0)
                    if max_dd > 0 and (balance - equity) >= max_dd:
                        print(f"🛑 Max Drawdown Reached. Stopping Bot.")
                        await self.session.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
                        await self.stop()
                        return True

            max_runtime = self.config.get('max_runtime_minutes', 0)
            if max_runtime > 0 and self.start_time > 0:
                if (time.time() - self.start_time) / 60 >= max_runtime:
                    print(f"🛑 Max Runtime Reached. Stopping Bot.")
                    await self.session.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
                    await self.stop()
                    return True
                    
        except Exception:
            pass
        return False

    def get_status(self):
        return {
            "running": self.running,
            "symbol": self.symbol,
            "current_price": self.current_price,
            "positions_count": self.current_step,
            "config": self.config
        }