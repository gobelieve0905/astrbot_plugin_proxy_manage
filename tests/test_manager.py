import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def install_astrbot_stubs():
    if "astrbot.api" in sys.modules:
        return
    modules = {
        name: types.ModuleType(name)
        for name in (
            "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star",
            "astrbot.api.web",
        )
    }

    class Star:
        def __init__(self, context=None):
            pass

    class StarTools:
        @staticmethod
        def get_data_dir(name):
            return Path(tempfile.gettempdir()) / name

    modules["astrbot.api"].logger = types.SimpleNamespace(
        info=lambda *args: None, warning=lambda *args: None
    )
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].StarTools = StarTools
    modules["astrbot.api.star"].register = lambda *args: lambda cls: cls
    modules["astrbot.api.web"].request = None
    modules["astrbot.api.web"].error_response = lambda message, status_code=400: {
        "message": message, "status": status_code
    }
    modules["astrbot.api.web"].json_response = lambda data: data
    for name, module in modules.items():
        sys.modules[name] = module


class TestConfigurationRules(unittest.TestCase):
    def test_redacted_endpoint_is_not_persisted_as_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            install_astrbot_stubs()
            module = __import__("main")
            class Config:
                def get(self, key, default): return default
            with patch.object(module.StarTools, "get_data_dir", return_value=Path(directory)):
                manager = module.ProxyManager.__new__(module.ProxyManager)
                manager.path = Path(directory) / "config.json"
                manager.event_path = Path(directory) / "events.jsonl"
                manager.state = {"profiles": [{"id": "tg", "endpoint": "http://secret:7890"}], "routes": [], "nodes": []}
                manager.events = []
                manager.lock = asyncio.Lock()
                payload = {"profiles": [{"id": "tg", "endpoint": "http://[configured]"}], "routes": [], "nodes": []}
                old = {p["id"]: p for p in manager.state["profiles"]}
                for profile in payload["profiles"]:
                    if profile["endpoint"].endswith("://[configured]"):
                        profile["endpoint"] = old[profile["id"]]["endpoint"]
                asyncio.run(manager._persist(payload))
                profiles = json.loads(manager.path.read_text())["profiles"]
                self.assertEqual(next(p for p in profiles if p["id"] == "tg")["endpoint"], "http://secret:7890")

    def test_invalid_proxy_scheme_is_rejected(self):
        install_astrbot_stubs()
        module = __import__("main")
        self.assertFalse(module._safe_url("file:///etc/passwd"))
        self.assertTrue(module._safe_url("socks5://mihomo:7891"))
        self.assertFalse(module._safe_url("http://user:password@proxy:8080"))

    def test_snapshot_redacts_node_endpoint(self):
        install_astrbot_stubs()
        module = __import__("main")
        manager = module.ProxyManager.__new__(module.ProxyManager)
        manager.state = {
            "profiles": [{"id": "direct", "name": "直连", "kind": "direct"}],
            "routes": [],
            "nodes": [{
                "id": "n1", "name": "节点", "endpoint": "socks5://secret:7890", "kind": "socks5"
            }],
        }
        manager.events = []
        snapshot = manager._snapshot()
        self.assertEqual(snapshot["nodes"][0]["endpoint"], "socks5://[configured]")


if __name__ == "__main__":
    unittest.main()
