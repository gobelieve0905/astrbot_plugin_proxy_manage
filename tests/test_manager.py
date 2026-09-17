import asyncio
import copy
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

    def test_redacted_snapshot_round_trip_preserves_complete_credentials(self):
        manager = self.module.ProxyManager.__new__(self.module.ProxyManager)
        raw = {
            'nodes': [{'id':'old','name':'AnyTLS','protocol':'anytls','kind':'mihomo',
                       'endpoint':'anytls://user:node-pass@example.com:443?sni=example.com#HK',
                       'connection':{'uri':'anytls://user:node-pass@example.com:443?sni=example.com#HK',
                                     'server':'example.com','username':'user','password':'node-pass',
                                     'tls':{'token':'nested-token'}},
                       'subscription_id':'sub-a','enabled':True}],
            'groups': [{'id':'direct','name':'直连','mode':'direct','node_ids':[],'selected':'','enabled':True}],
            'routes': [], 'platforms': {},
            'subscriptions': [{'id':'sub-a','name':'A','url':'https://sub.example/list?token=sub-token',
                               'enabled':True,'interval':60,'node_ids':[]}],
            'control': {'enabled':True,'url':'http://controller:9090','secret':'control-secret','timeout':8},
            'proxy_entry': {'http_url':'http://proxy-user:proxy-pass@proxy:7890',
                            'socks_url':'socks5://sock-user:sock-pass@proxy:7891','source':'configured'},
        }
        manager.state = manager._normalize(raw); manager.health = {}; manager.events = []
        public = manager.snapshot()
        serialized = json.dumps(public, ensure_ascii=False)
        for secret in ('node-pass','nested-token','sub-token','control-secret','proxy-pass','sock-pass'):
            self.assertNotIn(secret, serialized)
        payload = copy.deepcopy(public)
        manager._restore_redacted(payload)
        restored = manager._validate(payload)
        node = restored['nodes'][0]
        self.assertEqual(node['endpoint'], manager.state['nodes'][0]['endpoint'])
        self.assertEqual(node['connection'], manager.state['nodes'][0]['connection'])
        self.assertEqual(restored['subscriptions'][0]['url'], manager.state['subscriptions'][0]['url'])
        self.assertEqual(restored['control']['secret'], 'control-secret')
        self.assertEqual(restored['proxy_entry'], manager.state['proxy_entry'])

    def test_mask_keep_empty_clear_and_new_value_replace_are_distinct(self):
        manager = self._manager_for_runtime()
        manager.state['nodes'][0]['connection'] = {'password':'old-pass','token':'old-token'}
        manager.state['proxy_entry'] = {'http_url':'http://old-proxy:7890','socks_url':'socks5://old-proxy:7891','source':'configured'}
        manager.state['control']['secret'] = 'old-secret'
        payload = manager.snapshot()
        payload['control']['secret'] = ''
        payload['proxy_entry']['http_url'] = ''
        payload['proxy_entry']['socks_url'] = 'socks5://new-proxy:1080'
        payload['nodes'][0]['connection']['password'] = 'new-pass'
        payload['nodes'][0]['connection']['token'] = ''
        manager._restore_redacted(payload)
        self.assertEqual(payload['control']['secret'], '')
        self.assertEqual(payload['proxy_entry']['http_url'], '')
        self.assertEqual(payload['proxy_entry']['socks_url'], 'socks5://new-proxy:1080')
        self.assertEqual(payload['nodes'][0]['endpoint'], manager.state['nodes'][0]['endpoint'])
        self.assertEqual(payload['nodes'][0]['connection'], {'password':'new-pass','token':''})

    def test_public_diagnostics_redact_urls_tokens_and_authorization(self):
        manager = self._manager_for_runtime()
        manager.health = {'hk-1': {'status':'error','error':'GET anytls://user:pass@host:443 failed token=abc'}}
        manager.events = [{'at':1,'message':'Authorization: Bearer top-secret password=hunter2'}]
        serialized = json.dumps(manager.snapshot(), ensure_ascii=False)
        for secret in ('user:pass','abc','top-secret','hunter2'):
            self.assertNotIn(secret, serialized)

    def test_persist_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.module.ProxyManager.__new__(self.module.ProxyManager)
            manager.path = Path(directory) / "config.json"; manager.backup = Path(directory) / "config.previous.json"; manager.state = {}
            state = {"nodes": [], "groups": [{"id": "direct", "name": "直连", "mode": "direct", "node_ids": [], "selected": "", "enabled": True}], "routes": [], "subscriptions": [], "platforms": {}, "control": {"enabled": False, "url": "", "secret": "", "timeout": 8}}
            asyncio.run(manager.persist(state))
            self.assertEqual(json.loads(manager.path.read_text())["version"], 2)
            self.assertEqual(manager.path.stat().st_mode & 0o777, 0o600)
            asyncio.run(manager.persist(state))
            self.assertEqual(manager.backup.stat().st_mode & 0o777, 0o600)

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
            "platforms": {}, "control": {"enabled": True, "url": "http://mihomo:9090", "secret": "secret", "timeout": 8,
                                          "deployment": "existing", "scope": "providers-groups-rules"},
            "proxy_entry": {"http_url": "http://proxy.example:7890", "socks_url": "", "source": "configured"},
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
        self.assertNotIn("mixed-port", document)
        groups = {item["name"]: item for item in document["proxy-groups"]}
        self.assertNotIn("DIRECT", groups["香港自动"].get("proxies", []))
        self.assertEqual(groups["香港自动"].get("use"), ["provider-sub-hk"])
        self.assertEqual(groups["新加坡自动"].get("use"), ["provider-sub-sg"])

    def test_proxy_entry_is_separate_from_control_endpoint(self):
        manager = self._manager_for_runtime()
        normalized = manager._normalize({"control": {"enabled": True, "url": "http://control.example:9090", "secret": "key"}, "proxy_entry": {"http_url": "http://proxy.example:7890"}})
        self.assertEqual(normalized["control"]["url"], "http://control.example:9090")
        self.assertEqual(normalized["proxy_entry"]["http_url"], "http://proxy.example:7890")

    def test_anytls_keeps_protocol_and_complete_uri(self):
        manager = self._manager_for_runtime()
        nodes, _ = manager._parse_subscription('anytls://user:password@example.com:443?security=tls&sni=example.com#香港 AnyTLS', 'sub-a')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['protocol'], 'anytls')
        self.assertEqual(nodes[0]['engine'], 'mihomo')
        self.assertIn('security=tls&sni=example.com', nodes[0]['endpoint'])
        self.assertEqual(nodes[0]['display_name'], '香港 AnyTLS')

    def test_excluded_nodes_are_not_candidates(self):
        manager = self._manager_for_runtime()
        manager.state['nodes'][0]['excluded'] = True
        with self.assertRaises(ValueError):
            manager.resolve('hk')

    def test_legacy_ids_migrate_group_and_selection(self):
        manager = self._manager_for_runtime()
        normalized = manager._normalize({'nodes':[{'id':'old-1','name':'重复','kind':'mihomo','endpoint':'anytls://a','subscription_id':'sub-a','enabled':True}], 'groups':[{'id':'g','name':'G','mode':'select','node_ids':['old-1'],'selected':'old-1'}]})
        new_id = manager._stable_node_id('sub-a','anytls://a')
        self.assertEqual(normalized['nodes'][0]['id'], new_id)
        group = next(item for item in normalized['groups'] if item['id'] == 'g')
        self.assertEqual(group['node_ids'], [new_id])
        self.assertEqual(group['selected'], new_id)

    def test_same_display_name_from_two_subscriptions_has_distinct_ids(self):
        manager = self._manager_for_runtime()
        text = 'anytls://a@example.com:443#香港'
        first, _ = manager._parse_subscription(text, 'sub-a')
        second, _ = manager._parse_subscription(text, 'sub-b')
        self.assertEqual(first[0]['display_name'], second[0]['display_name'])
        self.assertNotEqual(first[0]['id'], second[0]['id'])

    def test_same_subscription_groups_only_include_selected_region_nodes(self):
        manager = self._manager_for_runtime()
        manager.state['nodes'][1]['subscription_id'] = 'sub-hk'
        manager.state['subscriptions'] = manager.state['subscriptions'][:1]
        document = manager._runtime_document()
        groups = {item['name']: item for item in document['proxy-groups']}
        self.assertEqual(groups['香港自动'].get('proxies'), ['node-hk-1'])
        self.assertEqual(groups['新加坡自动'].get('proxies'), ['node-sg-1'])
        self.assertNotIn('use', groups['香港自动'])

    def test_same_name_nodes_use_distinct_kernel_names_for_delay(self):
        manager = self._manager_for_runtime()
        manager.state['nodes'][0].update({'name': '同名节点', 'kernel_name': 'node-sub-hk-hk-1'})
        manager.state['nodes'][1].update({'name': '同名节点', 'kernel_name': 'node-sub-sg-sg-1'})
        response = self.module.httpx.Response(
            200,
            json={'delay': 25},
            request=self.module.httpx.Request('GET', 'http://mihomo:9090/proxies/node/delay'),
        )
        client = AsyncMock(); client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=response)
        with patch.object(manager, '_kernel_status', AsyncMock(return_value={'state': 'connected'})), \
             patch.object(self.module.httpx, 'AsyncClient', return_value=client):
            asyncio.run(manager._probe_node({'node_id': 'hk-1'}))
            asyncio.run(manager._probe_node({'node_id': 'sg-1'}))
        paths = [call.args[0] for call in client.get.await_args_list]
        self.assertEqual(paths, [
            '/proxies/node-sub-hk-hk-1/delay',
            '/proxies/node-sub-sg-sg-1/delay',
        ])

    def test_display_name_change_does_not_change_stable_id(self):
        helper = self.module.ProxyManager._stable_node_id
        before = helper('sub-a', 'anytls://token@example.com:443?sni=example.com#旧名称')
        after = helper('sub-a', 'anytls://token@example.com:443?sni=example.com#新名称')
        self.assertEqual(before, after)

    def test_refresh_preserves_excluded_node_preferences(self):
        manager = self._manager_for_runtime()
        old = manager.state['nodes'][0]
        old.update({'excluded': True, 'exclusion_reason': '流量提示'})
        manager.state['subscriptions'][0]['node_ids'] = [old['id']]
        refreshed = dict(old, excluded=False, exclusion_reason='')
        manager._replace_subscription_nodes(manager.state['subscriptions'][0], [refreshed])
        current = next(node for node in manager.state['nodes'] if node['id'] == old['id'])
        self.assertTrue(current['excluded'])
        self.assertEqual(current['exclusion_reason'], '流量提示')

    def test_missing_manual_selection_does_not_fall_back_silently(self):
        manager = self._manager_for_runtime()
        group = next(item for item in manager.state['groups'] if item['id'] == 'hk')
        group['node_ids'] = ['missing-node', 'hk-1']
        group['selected'] = 'missing-node'
        with self.assertRaisesRegex(ValueError, '选择.*失效|失效.*选择'):
            manager.resolve('hk')

    def test_direct_fallback_maps_to_mihomo_direct(self):
        document = self._manager_for_runtime()._runtime_document()
        self.assertEqual(document['rules'][-1], 'MATCH,DIRECT')

    def test_runtime_apply_must_verify_groups_and_rules(self):
        manager = self._manager_for_runtime()
        request = self.module.httpx.Request('GET', 'http://mihomo:9090/check')
        def response(payload):
            return self.module.httpx.Response(200, json=payload, request=request)
        expected = manager._runtime_document()
        client = AsyncMock(); client.__aenter__.return_value = client
        client.put.return_value = response({})
        client.get.side_effect = [
            response({'mode': 'rule'}),
            response({'proxies': {item['name']: {'type': 'URLTest'} for item in expected['proxy-groups']}}),
            response({'rules': [{'payload': 'wrong-rule'} for _ in expected['rules']]}),
        ]
        with patch.object(manager, '_kernel_status', AsyncMock(return_value={'state': 'connected'})), \
             patch.object(self.module.httpx, 'AsyncClient', return_value=client):
            result = asyncio.run(manager.runtime_apply())
        client.put.assert_awaited_once()
        put_args = client.put.await_args
        self.assertEqual(put_args.args[0], '/configs?force=true')
        self.assertEqual(put_args.kwargs['json']['path'], '/config.yaml')
        self.assertIn('proxy-groups:', put_args.kwargs['json']['payload'])
        self.assertEqual([call.args[0] for call in client.get.await_args_list], ['/configs', '/proxies', '/rules'])
        self.assertNotEqual(result.get('applied'), True, '规则内容不一致时不得报告应用成功')


if __name__ == "__main__":
    unittest.main()
