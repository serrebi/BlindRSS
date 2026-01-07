import unittest
import os
import json
from unittest.mock import patch
from core.config import ConfigManager

class TestConfigManager(unittest.TestCase):

    def setUp(self):
        self.test_dir = "test_config_dir"
        os.makedirs(self.test_dir, exist_ok=True)
        self.config_file = os.path.join(self.test_dir, "config.json")

        patcher = patch('core.config.CONFIG_FILE', self.config_file)
        self.addCleanup(patcher.stop)
        self.mock_config_file = patcher.start()

        # Reset the singleton instance to ensure a clean state for each test
        if hasattr(ConfigManager, '_instance'):
            ConfigManager._instance = None

        self.config_manager = ConfigManager()
        # Clear any existing config from previous singleton instances
        self.config_manager.config = {}


    def tearDown(self):
        if hasattr(ConfigManager, '_instance'):
            ConfigManager._instance = None
        if os.path.exists(self.config_file):
            os.remove(self.config_file)
        if os.path.exists(self.test_dir):
            os.rmdir(self.test_dir)

    def test_get_default_value(self):
        self.assertEqual("default", self.config_manager.get("non_existent_key", "default"))

    def test_set_and_get_value(self):
        self.config_manager.set("test_key", "test_value")
        self.assertEqual("test_value", self.config_manager.get("test_key"))

    def test_save_and_load_config(self):
        self.config_manager.set("test_key", "test_value")
        self.config_manager.save_config()

        # Reset singleton and create a new instance to force a reload from the file
        ConfigManager._instance = None
        new_config_manager = ConfigManager()
        self.assertEqual("test_value", new_config_manager.get("test_key"))

    def test_config_file_creation(self):
        self.assertFalse(os.path.exists(self.config_file))
        self.config_manager.set("test_key", "test_value")
        self.config_manager.save_config()
        self.assertTrue(os.path.exists(self.config_file))

if __name__ == '__main__':
    unittest.main()
