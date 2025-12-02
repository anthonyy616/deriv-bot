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
        
        # State - Fixed Levels
        self.fixed_upper = None
        self.fixed_lower = None
        self.prev_ask = None
        self.prev_bid = None
        
        self.current_step = 0
        self.last_trigger_time = 0
        self.current_price = 0 # For UI display
        self.session = None

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        # Reset state when config changes
        self.reset_cycle()

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.reset_cycle()
        print(f"✅ Strategy Started. Symbol: {self.symbol}")

    async def stop(self):
        self.running = False
        if self.session:
            await self.session.close()
            self.session = None
        print("🛑 Strategy Stopped.")

    def reset_cycle(self):
        """ Clears fixed levels to allow a fresh calculation on next tick """
        self.fixed_upper = None
        self.fixed_lower = None
        self.prev_ask = None
        self.prev_bid = None
        self.current_step = 0
        self.last_trigger_time = 0

    async def on_external_tick(self, tick_data):
        if not self.running: return
        
        # UI Update Helper
        self.current_price = tick_data['ask']

        # 0. Check Stops (TP/SL)
        if await self.check_stopping_conditions():
            return

        ask = tick_data['ask']
        bid = tick_data['bid']
        point = tick_data.get('point', 0.001)
        
        # 1. Initialize Levels ONCE
        if self.fixed_upper is None:
            spread_points = self.config.get('spread', 8)
            spread_val = spread_points * point # Convert points to price value
            
            # Set exact levels
            self.fixed_upper = ask + spread_val
            self.fixed_lower = bid - spread_val
            
            # Initialize Previous Prices to current to avoid immediate trigger
            self.prev_ask = ask
            self.prev_bid = bid
            
            print(f"🎯 Levels Locked: Upper={self.fixed_upper:.5f} | Lower={self.fixed_lower:.5f}")
            return

        # 2. Check Triggers against LOCKED levels (CROSSING LOGIC)
        await self.execute_logic(ask, bid, point)
        
        # Update previous prices for next tick
        self.prev_ask = ask
        self.prev_bid = bid

    async def execute_logic(self, ask, bid, point):
        # Cooldown check (0.5 seconds to prevent double-fire on same tick burst)
        if time.time() - self.last_trigger_time < 0.5:
            return

        step_lots = self.config.get('step_lots', [])
        # Use last lot if step exceeded
        if self.current_step < len(step_lots):
            current_vol = step_lots[self.current_step]
        else:
            current_vol = step_lots[-1] if step_lots else 0.01

        max_pos = self.config.get('max_positions', 5)
        if self.current_step >= max_pos:
            return
        
        # A. Check Upper Crossing (Buy)
        # Trigger ONLY if we were below/at upper before, and now we are above/at
        if self.prev_ask < self.fixed_upper and ask >= self.fixed_upper:
            print(f"⚡ CROSS UP: Price ({ask}) crossed Upper ({self.fixed_upper:.5f}) -> BUY Signal")
            success = await self.send_trade("buy", current_vol, self.config.get('buy_stop_tp'), self.config.get('buy_stop_sl'), point)
            if success:
                self.last_trigger_time = time.time()
                self.current_step += 1

        # B. Check Lower Crossing (Sell)
        # Trigger ONLY if we were above/at lower before, and now we are below/at
        elif self.prev_bid > self.fixed_lower and bid <= self.fixed_lower:
            print(f"⚡ CROSS DOWN: Price ({bid}) crossed Lower ({self.fixed_lower:.5f}) -> SELL Signal")
            success = await self.send_trade("sell", current_vol, self.config.get('sell_stop_tp'), self.config.get('sell_stop_sl'), point)
            if success:
                self.last_trigger_time = time.time()
                self.current_step += 1

    async def send_trade(self, action, volume, tp_usd, sl_usd, point):
        if not self.session: return False
        
        # Safety checks
        if volume <= 0: volume = 0.01
        if point <= 0: point = 0.001

        try:
            # --- THE FIX ---
            # Old Logic: int((abs(tp_usd) / volume) / point)  <-- This adjusted for lot size (Money)
            # New Logic: int(abs(tp_usd) / point)             <-- This is pure distance
            
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
                return resp.status == 200
        except Exception as e:
            print(f"❌ Execution Error: {e}")
            return False

    async def check_stopping_conditions(self):
        if not self.session: return False
        try:
            # Get deals from last 5 seconds to catch TP/SL hits
            async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=5", timeout=2) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    tp_hit = any(d['profit'] > 0 for d in deals)
                    sl_hit = any(d['profit'] < 0 for d in deals)
                    
                    if tp_hit or sl_hit:
                        log_msg = "🎉 TP Hit!" if tp_hit else "💀 SL Hit!"
                        print(f"{log_msg} Resetting Cycle...")
                        
                        # 1. Close/Delete EVERYTHING
                        await self.session.post(f"{self.mt5_bridge_url}/close_all", timeout=2)
                        
                        # 2. Reset Logic (Calculates new levels on next tick)
                        self.reset_cycle()
                        await asyncio.sleep(2) 
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