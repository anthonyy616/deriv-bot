import json
import os
import logging

class ConfigManager:
    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.config_file = f"config_{user_id}.json"
        self.config = self.load_config()

    def load_config(self):
        default_config = {
            "symbol": "FX Vol 20",
            "spread": 100,
            "tp_dist": 160,
            "sl_dist": 240,
            "max_positions": 5,
            "lot_size": 0.01,
            "max_runtime_minutes": 0,  # 0 = Infinite
            "max_drawdown_usd": 50.0   # Default $50 drawdown limit
        }
        
        if not os.path.exists(self.config_file):
            self.save_config(default_config)
            return default_config
        
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading config: {e}")
            return default_config

    def save_config(self, config):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=4)
            self.config = config
            logging.info(f"Configuration saved for {self.user_id}")
        except Exception as e:
            logging.error(f"Error saving config: {e}")

    def get_config(self):
        return self.config

    def update_config(self, new_config):
        self.config.update(new_config)
        self.save_config(self.config)
        return self.config