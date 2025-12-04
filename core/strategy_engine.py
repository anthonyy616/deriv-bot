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
        
        self.session = None
        self.current_price = 0.0
        
        # We no longer store complex state. We read state from the broker.
        self.level_center = None 

    @property
    def config(self):
        return self.config_manager.get_config()

    async def start_ticker(self):
        # On config change, we just let the next reconcile loop fix it
        pass 

    async def start(self):
        self.running = True
        self.session = aiohttp.ClientSession()
        self.level_center = None # Reset center on start
        
        asyncio.create_task(self.run_reconcile_loop())
        print(f"✅ Strategy Started (Snapshot Mode): {self.symbol}")

    async def stop(self):
        self.running = False
        if self.session: await self.session.close()
        print("🛑 Strategy Stopped.")

    async def on_external_tick(self, tick_data):
        """Just updates price cache."""
        self.current_price = float(tick_data['ask'])

    async def run_reconcile_loop(self):
        """The Heartbeat: Checks 'What Is' vs 'What Should Be' every 1s."""
        print("👀 Reconciler Active")
        while self.running:
            try:
                await self.reconcile_state()
            except Exception as e:
                print(f"⚠️ Reconcile Error: {e}")
            await asyncio.sleep(1.0) # 1-second heartbeat

    async def reconcile_state(self):
        if not self.session: return
        
        # 1. FETCH REALITY (Account Info)
        # We need a new endpoint in Bridge to get ALL positions/orders efficiently
        # For now, we use account_info which returns 'positions_count'. 
        # But we need DETAILS. We will rely on 'recent_deals' to infer state or add a helper.
        # Actually, let's look at recent deals to detect the "Nuclear" condition first.
        
        # --- PHASE A: CHECK FOR DEAD POSITIONS (TP/SL) ---
        async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=60") as resp:
            if resp.status == 200:
                deals = await resp.json()
                # If ANY deal shows we exited a trade with profit/loss, NUCLEAR RESET
                for d in deals:
                    if d['entry'] == 1: # Entry Out (Exit)
                        print(f"🚨 Deal {d['ticket']} closed (Profit: {d['profit']}) -> NUCLEAR RESET")
                        await self.session.post(f"{self.mt5_bridge_url}/close_all")
                        self.level_center = None # Forget the grid
                        return # Stop this loop, wait for next tick to restart

        # --- PHASE B: GET OPEN POSITIONS & ORDERS ---
        # We need to know WHAT is open. 
        # Since we can't easily query full position details via the simple bridge yet, 
        # we will infer 'Step' count from 'positions_count' in account_info (which you have).
        
        async with self.session.get(f"{self.mt5_bridge_url}/account_info") as resp:
            if resp.status != 200: return
            info = await resp.json()
            
        pos_count = info.get('positions_count', 0)
        current_price = info.get('current_price', 0)
        
        if current_price == 0: return # No data yet

        # --- PHASE C: DEFINE DESIRED STATE ---
        
        # 1. Initialize Center if needed (Start of Cycle)
        spread = float(self.config.get('spread', 6.0))
        if self.level_center is None and pos_count == 0:
            self.level_center = current_price
            print(f"🎯 New Grid Center: {self.level_center}")

        if self.level_center is None: return # Should be set by now

        # 2. Calculate Levels
        level_top = self.level_center + spread
        level_bottom = self.level_center - spread
    
        # --- PHASE D: EXECUTE LOGIC ---
        
        max_pos = int(self.config.get('max_positions', 5))
        
        if pos_count >= max_pos:
            # We are full. Ensure NO pending orders exist.
            # (We blindly cancel every loop to ensure none sneak in)
            await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")
            return

        # LOGIC FOR STEP 0 (Start)
        if pos_count == 0:
            # We need 2 pending orders.
            # We blindly send them. If they already exist, MT5 rejects duplicates? 
            # No, MT5 allows duplicates. We must check if they exist.
            # PROBLEM: We can't check *what* exists via current Bridge.
            # FIX: We use a "Pulse" variable. We only send orders ONCE per "Step".
            pass 
            # Actually, your issue is "It's not canceling".
            # So if pos_count > 0, we MUST cancel orders.
            
        if pos_count > 0:
             # We have executed trades.
             # 1. Cancel the "Leftover" initial orders immediately.
             # This solves your "20 pips apart" bug.
             # We do this blindly every second to guarantee they are gone.
             await self.session.post(f"{self.mt5_bridge_url}/cancel_orders")
             
             # 2. Place the NEXT single pending order.
             # We need to calculate the next lot size.
             next_lot = self.get_volume(pos_count) # pos_count is effectively the index for next trade
             
             # We need to know WHERE to place it.
             # This requires knowing if the last trade was BUY or SELL.
             # Since we lack that data in 'account_info', we will look at 'recent_deals' again
             # to find the LATEST deal entry.
             
             last_type = await self.get_last_trade_type()
             
             target_price = 0
             side = ""
             
             if last_type == 0: # Last was BUY
                 # Next is SELL STOP
                 side = "sell_stop"
                 pass

             if last_type == 0: # Last was BUY
                 side = "sell_stop"
                 # If price is above Center, we probably bought Top. Target = Center.
                 # If price is near Center, we bought Center. Target = Bottom.
                 # Simple snap:
                 if current_price > self.level_center: target_price = self.level_center
                 else: target_price = level_bottom
                 
             elif last_type == 1: # Last was SELL
                 side = "buy_stop"
                 # If price is below Center, we sold Bottom. Target = Center.
                 # If price is near Center, we sold Center. Target = Top.
                 if current_price < self.level_center: target_price = self.level_center
                 else: target_price = level_top
                 
             # 3. SEND THE ORDER (If it doesn't exist)
             # Since we canceled everything above, we just place it.
             # But we must avoid spamming.
             # We use a memory flag "orders_placed_for_step".
             if self.current_step_memory != pos_count:
                 print(f"➡ Placing Correction Order: {side} @ {target_price} (Lot: {next_lot})")
                 await self.send_pending(side, target_price, next_lot)
                 self.current_step_memory = pos_count # Mark as done for this step count

        elif pos_count == 0:
            # Init Logic
            if self.current_step_memory != 0:
                print("🚀 Placing Initial Straddle")
                vol = self.get_volume(0)
                await self.send_pending("buy_stop", level_top, vol)
                await self.send_pending("sell_stop", level_bottom, vol)
                self.current_step_memory = 0

    async def get_last_trade_type(self):
        """Finds the type of the most recent executed deal."""
        async with self.session.get(f"{self.mt5_bridge_url}/recent_deals?seconds=3600") as resp:
            if resp.status == 200:
                deals = await resp.json()
                if deals:
                    # Sort by ticket descending to get latest
                    deals.sort(key=lambda x: x['ticket'], reverse=True)
                    # Return the type of the newest deal (0=Buy, 1=Sell)
                    return deals[0]['type']
        return 0 # Default fallback

    # ... [Keep send_pending, get_volume, get_status unchanged] ...
    # (I will include them in the full code block below)

    # --- MEMORY ---
    current_step_memory = -1 # Tracks which step we have successfully set up orders for

    async def send_pending(self, action, price, volume):
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
            "comment": "AutoGrid"
        }
        try:
            await self.session.post(f"{self.mt5_bridge_url}/execute_signal", json=payload)
        except Exception as e:
            print(f"❌ Order Error: {e}")

    def get_volume(self, step):
        step_lots = self.config.get('step_lots', [])
        if not step_lots: return 0.01
        if step < len(step_lots): return step_lots[step]
        return step_lots[-1]

    def get_status(self):
        return {
            "running": self.running,
            "current_price": self.current_price,
            "step": self.current_step_memory, # Shows current logic step
            "top": self.level_top if hasattr(self, 'level_top') else 0, # Safety check
            "center": self.level_center
        }