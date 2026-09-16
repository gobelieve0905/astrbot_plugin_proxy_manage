import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


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

    def _manager_for_runtime(self):
        manager = self.module.ProxyManager.__new__(self.module.ProxyManager)
        manager.state = {
            "nodes": [
                {"id": "hk-1", "name": "HK 1", "kind": "mihomo", "endpoint": "anytls://secret", "subscription_id": "sub-hk", "enabled": True},
                {"id": "sg-1", "name": "SG 1", "kind": "mihomo", "endpoint": "anytls://secret2", "subscription_id": "sub-sg", "enabled": True},
            ],
            "groups": [
                {"id": "direct", "name": "直连", "mode": "direct", "node_ids": [], "selected": "", "enabled": True},
                {"id": "hk", "name": "香港自动", "mode": "url-test", "node_ids": ["hk-1"], "selected": "hk-1", "enabled": True},
                {"id": "sg", "name": "新加坡自动", "mode": "url-test", "node_ids": ["sg-1"], "selected": "sg-1", "enabled": True},
            ],
            "routes": [{"id": "meta", "host": "meta.example", "match": "suffix", "target": "hk", "priority": 10, "enabled": True}],
            "subscriptions": [
                {"id": "sub-hk", "name": "HK", "url": "https://sub.example/hk", "enabled": True, "interval": 60},
                {"id": "sub-sg", "name": "SG", "url": "https://sub.example/sg", "enabled": True, "interval": 60},
            ],
            "platforms": {}, "control": {"enabled": True, "url": "http://mihomo:9090", "secret": "secret", "timeout": 8},
        }
        manager.events = []
        manager._test_dir = tempfile.TemporaryDirectory()
        manager.events_path = Path(manager._test_dir.name) / "events.jsonl"
        manager.health = {}
        manager.health_path = Path(manager._test_dir.name) / "health.json"
        return manager

    def test_kernel_not_configured_is_explicit(self):
        manager = self._manager_for_runtime()
        manager.state["control"] = {"enabled": False, "url": "", "secret": "", "timeout": 8}
        status = asyncio.run(manager._kernel_status())
        self.assertEqual(status["state"], "not_configured")
        result = asyncio.run(manager._probe_node({"node_id": "hk-1"}))
        self.assertTrue(result["skipped"])
        self.assertEqual(manager.health["hk-1"]["status"], "pending")

    def test_kernel_status_distinguishes_version_and_runtime_states(self):
        manager = self._manager_for_runtime()
        request = self.module.httpx.Request("GET", "http://mihomo:9090/version")
        def response(payload, status=200):
            return self.module.httpx.Response(status, json=payload, request=request)
        client = AsyncMock(); client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=response({"meta": False, "version": "1.18.0"}))
        with patch.object(self.module.httpx, "AsyncClient", return_value=client):
            self.assertEqual(asyncio.run(manager._kernel_status())["state"], "version_unsupported")
        client.get = AsyncMock(side_effect=[
            response({"meta": True, "version": "1.19.0"}),
            response({"mode": "global"}), response({"proxies": {}}), response({"rules": []}),
        ])
        with patch.object(self.module.httpx, "AsyncClient", return_value=client):
            self.assertEqual(asyncio.run(manager._kernel_status())["state"], "config_not_applied")
        client.get = AsyncMock(side_effect=[
            response({"meta": True, "version": "1.19.0"}),
            response({"mode": "rule"}), response({"proxies": {}}), response({"rules": []}),
        ])
        with patch.object(self.module.httpx, "AsyncClient", return_value=client):
            self.assertEqual(asyncio.run(manager._kernel_status())["state"], "runtime_inconsistent")

    def test_runtime_groups_must_follow_selected_node_members(self):
        document = self._manager_for_runtime()._runtime_document()
        groups = {item["name"]: item for item in document["proxy-groups"]}
        self.assertNotIn("DIRECT", groups["香港自动"].get("proxies", []))
        self.assertEqual(groups["香港自动"].get("use"), ["provider-sub-hk"])
        self.assertEqual(groups["新加坡自动"].get("use"), ["provider-sub-sg"])

    def test_runtime_apply_must_verify_groups_and_rules(self):
        manager = self._manager_for_runtime()
        response = self.module.httpx.Response(
            200,
            json={"mode": "rule", "mixed-port": 7890, "proxies": {}},
            request=self.module.httpx.Request("GET", "http://mihomo:9090/configs"),
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.put.return_value = response
        client.get.return_value = response
        with patch.object(self.module.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(manager.runtime_apply())
        self.assertNotEqual(result.get("applied"), True, "仅核对 mode 不得报告配置应用成功")


if __name__ == "__main__":
    unittest.main()
