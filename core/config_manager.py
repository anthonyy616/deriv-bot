import json
import os
import logging

class ConfigManager:
    def __init__(self, user_id="default"):
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