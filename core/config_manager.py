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
            "symbol": "R_75",
            "spread": 8,
            "tp_dist": 16,
            "sl_dist": 24,
            "max_positions": 7,
            "lot_size": 10,  # $10 stake per trade
            "max_runtime_minutes": 0,  # 0 = Infinite
            "max_drawdown_usd": 0      # 0 = Disabled
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
            logging.info("Configuration saved.")
        except Exception as e:
            logging.error(f"Error saving config: {e}")

    def get_config(self):
        return self.config

    def update_config(self, new_config):
        # Update only provided keys
        self.config.update(new_config)
        self.save_config(self.config)
        return self.config
