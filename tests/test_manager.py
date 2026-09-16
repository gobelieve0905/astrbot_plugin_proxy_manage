import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def install_astrbot_stubs():
    if "astrbot.api" in sys.modules:
        return
    modules = {name: types.ModuleType(name) for name in ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star", "astrbot.api.web")}
    class Star:
        def __init__(self, context=None): pass
    class StarTools:
        @staticmethod
        def get_data_dir(name): return Path(tempfile.gettempdir()) / name
    modules["astrbot.api"].logger = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.star"].Context = object; modules["astrbot.api.star"].Star = Star; modules["astrbot.api.star"].StarTools = StarTools
    modules["astrbot.api.star"].register = lambda *a: lambda cls: cls
    modules["astrbot.api.web"].request = None
    modules["astrbot.api.web"].error_response = lambda message, status_code=400: {"message": message, "status": status_code}
    modules["astrbot.api.web"].json_response = lambda data: data
    sys.modules.update(modules)
    if "httpx" not in sys.modules:
        httpx = types.ModuleType("httpx")
        httpx.HTTPError = Exception
        httpx.AsyncClient = object
        httpx.Limits = lambda **kwargs: None
        httpx.Headers = dict
        sys.modules["httpx"] = httpx


class TestConfigurationRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_astrbot_stubs(); cls.module = __import__("main")

    def test_invalid_proxy_scheme_is_rejected(self):
        self.assertFalse(self.module.safe_url("file:///etc/passwd"))
        self.assertTrue(self.module.safe_url("socks5://mihomo:7891"))
        self.assertFalse(self.module.safe_url("http://user:password@proxy:8080"))

    def test_snapshot_redacts_node_endpoint(self):
        manager = self.module.ProxyManager.__new__(self.module.ProxyManager)
        manager.state = {"nodes": [{"id": "n1", "endpoint": "socks5://secret:7890"}], "subscriptions": [], "control": {"secret": ""}}
        manager.events = []; manager.health = {}
        self.assertEqual(manager.snapshot()["nodes"][0]["endpoint"], "socks5://[configured]")

    def test_persist_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.module.ProxyManager.__new__(self.module.ProxyManager)
            manager.path = Path(directory) / "config.json"; manager.backup = Path(directory) / "config.previous.json"; manager.state = {}
            state = {"nodes": [], "groups": [{"id": "direct", "name": "直连", "mode": "direct", "node_ids": [], "selected": "", "enabled": True}], "routes": [], "subscriptions": [], "platforms": {}, "control": {"enabled": False, "url": "", "secret": "", "timeout": 8}}
            asyncio.run(manager.persist(state))
            self.assertEqual(json.loads(manager.path.read_text())["version"], 2)
            self.assertEqual(manager.path.stat().st_mode & 0o777, 0o600)

    def test_stable_node_id_and_error_redaction(self):
        helper = self.module.ProxyManager._stable_node_id
        self.assertEqual(helper("sub-a", "http://node:80"), helper("sub-a", "http://node:80"))
        self.assertNotEqual(helper("sub-a", "http://node:80"), helper("sub-b", "http://node:80"))
        self.assertNotIn("token", self.module.safe_error("GET https://user:token@example.com/x"))


if __name__ == "__main__":
    unittest.main()
