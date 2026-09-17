import asyncio
import base64
import copy
import json
import importlib.util
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
        from proxy_manager.domain import security, identity
        cls.module.safe_url = security.safe_url
        cls.module.safe_error = security.safe_error
        cls.module.ProxyManager._stable_node_id = staticmethod(__import__('proxy_manager.domain.identity', fromlist=['stable_node_id']).stable_node_id)


    def test_core_adapter_contract_keeps_mihomo_logic_inside_adapter(self):
        from proxy_manager.cores.registry import current_adapter
        manager=self._manager_for_runtime(); adapter=current_adapter(manager.state)
        self.assertEqual(adapter.id,'mihomo')
        document=adapter.render(manager.state)
        self.assertEqual(document['rules'][-1],'MATCH,DIRECT')
        self.assertEqual(manager._runtime_document(), document)
        self.assertEqual(manager._mihomo_proxy(manager.state['nodes'][0])['name'],'node-hk-1')
        source=Path(__file__).resolve().parents[1]/'proxy_manager'/'plugin.py'
        text=source.read_text(encoding='utf-8')
        self.assertNotIn('external-controller', text)
        self.assertNotIn('skip-cert-verify', text)
        self.assertNotIn('mixed-port', text)
        self.assertNotIn('/configs?force=true', text)
        package=source.parent
        for module in package.rglob('*.py'):
            self.assertNotIn('from proxy_manager.',module.read_text(encoding='utf-8'),str(module))

    def test_entry_imports_inside_astrbot_namespace_package(self):
        root=Path(__file__).resolve().parents[1]
        package_name='proxy_manager_package_test'
        package=types.ModuleType(package_name); package.__path__=[str(root)]
        sys.modules[package_name]=package
        try:
            spec=importlib.util.spec_from_file_location(package_name+'.main',root/'main.py')
            module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module
            spec.loader.exec_module(module)
            self.assertEqual(module.ProxyManager.__module__,package_name+'.main')
        finally:
            for name in [name for name in sys.modules if name==package_name or name.startswith(package_name+'.')]:
                sys.modules.pop(name,None)

    def test_normalized_nodes_record_executor_and_adapter_set(self):
        manager=self._manager_for_runtime()
        node=manager._normalize({'nodes':[{'id':'a','name':'AnyTLS','protocol':'anytls','endpoint':'anytls://secret@example.com:443'}]})['nodes'][0]
        self.assertEqual(node['executor'],'mihomo')
        self.assertEqual(node['adapters'],['mihomo'])
        http_node=manager._normalize({'nodes':[{'id':'b','name':'HTTP','protocol':'http','endpoint':'http://proxy.example:8080'}]})['nodes'][0]
        self.assertEqual(http_node['executor'],'direct-http')

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
            self.assertEqual(json.loads(manager.path.read_text())["version"], 3)
            self.assertEqual(manager.path.stat().st_mode & 0o777, 0o600)
            asyncio.run(manager.persist(state))
            self.assertEqual(manager.backup.stat().st_mode & 0o777, 0o600)

    def test_startup_hardens_existing_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('config.json','config.previous.json','health.json','events.jsonl')]
            for path in paths:
                path.write_text('{}'); path.chmod(0o644)
            context = types.SimpleNamespace(register_web_api=lambda *args: None)
            with patch.object(self.module.StarTools,'get_data_dir',return_value=Path(directory)):
                manager = self.module.ProxyManager(context,{})
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in paths))
            if manager.auto_task: manager.auto_task.cancel()

    def test_stable_node_id_and_error_redaction(self):
        helper = self.module.ProxyManager._stable_node_id
        self.assertEqual(helper("sub-a", "http://node:80"), helper("sub-a", "http://node:80"))
        self.assertNotEqual(helper("sub-a", "http://node:80"), helper("sub-b", "http://node:80"))
        self.assertNotIn("token", self.module.safe_error("GET https://user:token@example.com/x"))

    def _manager_for_runtime(self):
        manager = self.module.ProxyManager.__new__(self.module.ProxyManager)
        manager.state = {
            "nodes": [
                {"id": "hk-1", "name": "HK 1", "display_name":"HK 1", "protocol":"anytls", "engine":"mihomo", "kind": "mihomo", "endpoint": "anytls://secret@example.com:443", "connection":{"uri":"anytls://secret@example.com:443"}, "kernel_name":"node-hk-1", "support":{"status":"supported","reason":""}, "subscription_id": "sub-hk", "enabled": True},
                {"id": "sg-1", "name": "SG 1", "display_name":"SG 1", "protocol":"anytls", "engine":"mihomo", "kind": "mihomo", "endpoint": "anytls://secret2@example.com:443", "connection":{"uri":"anytls://secret2@example.com:443"}, "kernel_name":"node-sg-1", "support":{"status":"supported","reason":""}, "subscription_id": "sub-sg", "enabled": True},
            ],
            "groups": [
                {"id": "direct", "name": "直连", "kernel_name":"DIRECT", "mode": "direct", "node_ids": [], "selected": "", "enabled": True},
                {"id": "hk", "name": "香港自动", "kernel_name":"group-hk", "mode": "url-test", "node_ids": ["hk-1"], "selected": "hk-1", "enabled": True},
                {"id": "sg", "name": "新加坡自动", "kernel_name":"group-sg", "mode": "url-test", "node_ids": ["sg-1"], "selected": "sg-1", "enabled": True},
            ],
            "routes": [{"id": "meta", "host": "meta.example", "match": "suffix", "target": "hk", "priority": 10, "enabled": True}],
            "subscriptions": [
                {"id": "sub-hk", "name": "HK", "url": "https://sub.example/hk", "enabled": True, "interval": 60},
                {"id": "sub-sg", "name": "SG", "url": "https://sub.example/sg", "enabled": True, "interval": 60},
            ],
            "platforms": {}, "control": {"enabled": True, "url": "http://mihomo:9090", "secret": "secret", "timeout": 8,
                                          "deployment": "dedicated", "scope": "full", "listen":"0.0.0.0:9090"},
            "proxy_entry": {"http_url": "http://proxy.example:7890", "socks_url": "", "source": "configured"},
        }
        manager.events = []
        manager._test_dir = tempfile.TemporaryDirectory()
        manager.events_path = Path(manager._test_dir.name) / "events.jsonl"
        manager.health = {}
        manager.health_path = Path(manager._test_dir.name) / "health.json"
        manager.path = Path(manager._test_dir.name) / "config.json"
        manager.backup = Path(manager._test_dir.name) / "config.previous.json"
        manager.runtime_path = Path(manager._test_dir.name) / "runtime-application.json"
        manager.runtime_backup = Path(manager._test_dir.name) / "runtime-application.previous.json"
        manager.runtime_application = {}
        manager.lock = asyncio.Lock(); manager.refresh_lock = asyncio.Lock(); manager.apply_lock = asyncio.Lock(); manager.previews = {}
        return manager

    def test_kernel_not_configured_is_explicit(self):
        manager = self._manager_for_runtime()
        manager.state["control"] = {"enabled": False, "url": "", "secret": "", "timeout": 8}
        status = asyncio.run(manager._kernel_status())
        self.assertEqual(status["state"], "not_configured")
        result = asyncio.run(manager._probe_node({"node_id": "hk-1"}))
        self.assertTrue(result["skipped"])
        self.assertNotIn("hk-1",manager.health)
        self.assertIn("内核未就绪",result["reason"])

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
            self.assertEqual(asyncio.run(manager._kernel_status())["state"], "saved")
        client.get = AsyncMock(side_effect=[
            response({"meta": True, "version": "1.19.0"}),
            response({"mode": "rule"}), response({"proxies": {}}), response({"rules": []}),
        ])
        revision=manager._runtime_revision(manager._runtime_document())
        manager.runtime_application={'status':'applied','applied_revision':revision,'document':manager._runtime_document()}
        with patch.object(self.module.httpx, "AsyncClient", return_value=client):
            self.assertEqual(asyncio.run(manager._kernel_status())["state"], "runtime_inconsistent")

    def test_kernel_status_rejects_wrong_secret(self):
        manager=self._manager_for_runtime(); request=self.module.httpx.Request('GET','http://mihomo:9090/version')
        denied=self.module.httpx.Response(401,json={'message':'Unauthorized'},request=request)
        client=AsyncMock(); client.__aenter__.return_value=client; client.get=AsyncMock(return_value=denied)
        with patch.object(self.module.httpx,'AsyncClient',return_value=client):
            self.assertEqual(asyncio.run(manager._kernel_status())['state'],'auth_failed')

    def test_kernel_crash_is_reported_as_connection_failure(self):
        manager=self._manager_for_runtime(); client=AsyncMock(); client.__aenter__.return_value=client
        client.get=AsyncMock(side_effect=self.module.httpx.ConnectError('core stopped'))
        with patch.object(self.module.httpx,'AsyncClient',return_value=client):
            status=asyncio.run(manager._kernel_status())
        self.assertEqual(status['state'],'connection_failed'); self.assertFalse(status['ready'])

    def test_runtime_groups_must_follow_selected_node_members(self):
        document = self._manager_for_runtime()._runtime_document()
        self.assertEqual(document["mixed-port"],7890)
        self.assertTrue(document['allow-lan']); self.assertEqual(document['bind-address'],'*')
        groups = {item["name"]: item for item in document["proxy-groups"]}
        self.assertEqual(groups["group-hk"].get("proxies"), ["node-hk-1"])
        self.assertEqual(groups["group-sg"].get("proxies"), ["node-sg-1"])
        self.assertNotIn("use", groups["group-hk"])

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

    def test_long_anytls_uri_is_lossless_and_generates_mihomo_parameters(self):
        manager = self._manager_for_runtime(); padding='x'*420
        uri=f'anytls://credential@[2001:db8::1]:443?sni=example.com&insecure=1&padding={padding}#长参数'
        nodes, _ = manager._parse_subscription(uri, 'sub-long')
        self.assertEqual(nodes[0]['endpoint'], uri)
        normalized = manager._normalize({'nodes':nodes})['nodes'][0]
        self.assertEqual(normalized['endpoint'], uri)
        proxy = manager._mihomo_proxy(normalized)
        self.assertEqual(proxy['server'], '2001:db8::1')
        self.assertEqual(proxy['password'], 'credential')
        self.assertEqual(proxy['sni'], 'example.com')
        self.assertTrue(proxy['skip-cert-verify'])
        self.assertEqual(proxy['padding'], padding)

    def test_yaml_anytls_http_and_socks_preserve_auth_ipv6_and_tls(self):
        manager = self._manager_for_runtime()
        yaml_text = '''proxies:
  - {name: AnyTLS v6, type: anytls, server: "2001:db8::10", port: 443, password: any-pass, sni: example.com, skip-cert-verify: true}
  - {name: HTTP TLS, type: http, server: "2001:db8::20", port: 8443, username: alice, password: http-pass, tls: true}
  - {name: SOCKS, type: socks5, server: socks.example, port: 1080, username: bob, password: socks-pass}
'''
        nodes, discovered = manager._parse_subscription(yaml_text, 'sub-yaml')
        self.assertEqual(discovered, {'anytls','http','socks5'})
        self.assertEqual([node['protocol'] for node in nodes], ['anytls','https','socks5'])
        self.assertTrue(all(node['support']['status']=='supported' for node in nodes))
        self.assertIn('[2001:db8::10]:443', nodes[0]['endpoint'])
        self.assertEqual(nodes[0]['connection']['password'], 'any-pass')
        self.assertTrue(manager._mihomo_proxy(nodes[1])['tls'])
        self.assertEqual(manager._mihomo_proxy(nodes[2])['username'], 'bob')

    def test_unverified_protocols_are_preserved_with_reason(self):
        manager = self._manager_for_runtime()
        nodes, discovered = manager._parse_subscription('vless://uuid@example.com:443#VLESS\nunknownx://opaque#未知', 'sub-other')
        self.assertEqual(discovered, {'vless','unknownx'})
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]['support']['status'], 'unverified')
        self.assertEqual(nodes[1]['support']['status'], 'unsupported')
        self.assertTrue(all(node['support']['reason'] for node in nodes))
        self.assertTrue(all(not node['enabled'] for node in nodes))

    def test_subscription_notice_is_marked_but_not_auto_excluded(self):
        manager = self._manager_for_runtime()
        nodes, _ = manager._parse_subscription('anytls://credential@example.com:443#剩余流量：20 GB', 'sub-notice')
        self.assertTrue(nodes[0]['suspected_notice'])
        self.assertTrue(nodes[0]['notice_reason'])
        self.assertFalse(nodes[0]['excluded'])
        self.assertTrue(nodes[0]['enabled'])

    def test_legacy_mihomo_kind_recovers_real_protocol(self):
        manager = self._manager_for_runtime()
        normalized = manager._normalize({'nodes':[{'id':'old','name':'A','kind':'mihomo','endpoint':'anytls://secret@example.com:443','subscription_id':'sub-a'}]})
        self.assertEqual(normalized['nodes'][0]['protocol'], 'anytls')
        self.assertEqual(normalized['nodes'][0]['support']['status'], 'supported')

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

    def test_region_survives_normalization_for_node_filters(self):
        manager=self._manager_for_runtime()
        node=manager._normalize({'nodes':[{'id':'hk','name':'AnyTLS 香港 1','protocol':'anytls','endpoint':'anytls://secret@example.com:443'}]})['nodes'][0]
        self.assertEqual(node['region'],'HK')

    def test_same_subscription_groups_only_include_selected_region_nodes(self):
        manager = self._manager_for_runtime()
        manager.state['nodes'][1]['subscription_id'] = 'sub-hk'
        manager.state['subscriptions'] = manager.state['subscriptions'][:1]
        document = manager._runtime_document()
        groups = {item['name']: item for item in document['proxy-groups']}
        self.assertEqual(groups['group-hk'].get('proxies'), ['node-hk-1'])
        self.assertEqual(groups['group-sg'].get('proxies'), ['node-sg-1'])
        self.assertNotIn('use', groups['group-hk'])

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
        with patch.object(manager, '_kernel_status', AsyncMock(return_value={'state': 'applied','message':'已应用'})), \
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

    def test_vmess_display_name_and_subscription_order_do_not_change_identity(self):
        helper = self.module.ProxyManager._stable_node_id
        def vmess(name):
            payload={'v':'2','ps':name,'add':'example.com','port':'443','id':'uuid','net':'ws'}
            return 'vmess://'+base64.b64encode(json.dumps(payload).encode()).decode()
        self.assertEqual(helper('sub-a',vmess('名称一')), helper('sub-a',vmess('名称二')))
        manager = self._manager_for_runtime()
        first, _ = manager._parse_subscription(vmess('A')+'\n'+vmess('B'), 'sub-a')
        second, _ = manager._parse_subscription(vmess('B')+'\n'+vmess('A'), 'sub-a')
        self.assertEqual({node['id'] for node in first}, {node['id'] for node in second})

    def test_v2_migration_updates_all_references_health_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); endpoint='anytls://secret@example.com:443#旧名称'
            raw={'version':2,'nodes':[{'id':'old-id','name':'旧名称','kind':'mihomo','endpoint':endpoint,'subscription_id':'sub-a','enabled':True}],
                 'groups':[{'id':'g','name':'G','mode':'select','node_ids':['old-id'],'selected':'old-id','enabled':True}],
                 'routes':[],'platforms':{},'subscriptions':[{'id':'sub-a','name':'A','url':'https://sub.example/a','enabled':True,'interval':60,'node_ids':['old-id']}],
                 'control':{'enabled':False,'url':'','secret':'','timeout':8}}
            (root/'config.json').write_text(json.dumps(raw)); (root/'health.json').write_text(json.dumps({'old-id':{'status':'ok'}}))
            context=types.SimpleNamespace(register_web_api=lambda *args:None)
            with patch.object(self.module.StarTools,'get_data_dir',return_value=root):
                first=self.module.ProxyManager(context,{})
                new_id=first.state['nodes'][0]['id']
                self.assertEqual(first.state['version'],3)
                self.assertEqual(first.state['groups'][1]['node_ids'],[new_id])
                self.assertEqual(first.state['groups'][1]['selected'],new_id)
                self.assertEqual(first.state['subscriptions'][0]['node_ids'],[new_id])
                self.assertIn(new_id,first.health); self.assertNotIn('old-id',first.health)
                backup=(root/'config.pre-v3.json').read_text(); self.assertEqual(json.loads(backup)['version'],2)
                self.assertEqual((root/'config.pre-v3.json').stat().st_mode & 0o777,0o600)
                second=self.module.ProxyManager(context,{})
                self.assertEqual(second.state['nodes'][0]['id'],new_id)
                self.assertEqual((root/'config.pre-v3.json').read_text(),backup)

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

    def test_subscription_diff_preserves_alias_and_deleted_reference(self):
        manager = self._manager_for_runtime(); subscription=manager.state['subscriptions'][0]
        old=manager.state['nodes'][0]
        old.update({'source_name':'香港 A','display_name':'我的香港','name':'我的香港','user_alias':'我的香港',
                    'excluded':True,'exclusion_reason':'用户排除','parameter_version':'v1'})
        subscription['node_ids']=[old['id']]
        replacement=copy.deepcopy(old); replacement.update({'source_name':'香港 B','display_name':'香港 B','name':'香港 B',
                                                              'user_alias':'','excluded':False,'parameter_version':'v2'})
        added=copy.deepcopy(manager.state['nodes'][1]); added['id']='new-node'; added['parameter_version']='new'
        diff=manager._replace_subscription_nodes(subscription,[replacement,added])
        current=next(node for node in manager.state['nodes'] if node['id']==old['id'])
        self.assertEqual(current['display_name'],'我的香港'); self.assertTrue(current['excluded'])
        self.assertEqual([item['id'] for item in diff['added']],['new-node'])
        self.assertEqual([item['id'] for item in diff['changed']],[old['id']])
        removed=manager._replace_subscription_nodes(subscription,[replacement])
        deleted=next(node for node in manager.state['nodes'] if node['id']=='new-node')
        self.assertTrue(deleted['invalid_reference']); self.assertFalse(deleted['enabled'])
        self.assertEqual([item['id'] for item in removed['deleted']],['new-node'])

    def test_manual_interval_zero_stays_manual_after_failure(self):
        manager=self._manager_for_runtime()
        normalized=manager._normalize({'subscriptions':[{'id':'manual','url':'https://sub.example/manual','interval':0}]})
        subscription=normalized['subscriptions'][0]
        self.assertEqual(subscription['interval'],0)
        manager._record_subscription_error(subscription,'temporary failure')
        self.assertEqual(subscription['next_refresh_at'],0)

    def test_failed_refresh_keeps_last_valid_nodes_and_groups(self):
        manager=self._manager_for_runtime(); manager.state=manager._normalize(manager.state)
        before_nodes=copy.deepcopy(manager.state['nodes']); before_groups=copy.deepcopy(manager.state['groups'])
        response=self.module.httpx.Response(503,request=self.module.httpx.Request('GET','https://sub.example/hk'))
        client=AsyncMock(); client.__aenter__.return_value=client; client.get=AsyncMock(return_value=response)
        with patch.object(self.module.httpx,'AsyncClient',return_value=client):
            with self.assertRaisesRegex(ValueError,'订阅请求失败'):
                asyncio.run(manager._refresh_with_retry('sub-hk',attempts=1))
        self.assertEqual(manager.state['nodes'],before_nodes); self.assertEqual(manager.state['groups'],before_groups)
        self.assertTrue(manager.state['subscriptions'][0]['last_error'])

    def test_expired_import_preview_is_rejected_and_removed(self):
        manager=self._manager_for_runtime(); manager.previews['old']={'at':0,'items':[]}
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'preview_id':'old'}))
        with patch('proxy_manager.plugin.request',fake_request):
            result=asyncio.run(manager.subscription_import())
        self.assertEqual(result['status'],400); self.assertNotIn('old',manager.previews)

    def test_control_status_and_select_use_internal_kernel_mapping(self):
        manager=self._manager_for_runtime(); request=self.module.httpx.Request('GET','http://mihomo:9090/proxies')
        def response(payload): return self.module.httpx.Response(200,json=payload,request=request)
        client=AsyncMock(); client.__aenter__.return_value=client
        client.get=AsyncMock(side_effect=[response({'meta':True,'version':'1.19.0'}),response({'proxies':{'group-hk':{'type':'Selector','now':'node-hk-1'}}})])
        with patch.object(self.module.httpx,'AsyncClient',return_value=client):
            status=asyncio.run(manager.control_status())
        hk=next(group for group in status['groups'] if group['id']=='hk')
        self.assertEqual(hk['display_name'],'香港自动'); self.assertEqual(hk['selected_node_id'],'hk-1')
        put_client=AsyncMock(); put_client.__aenter__.return_value=put_client; put_client.request=AsyncMock(return_value=response({}))
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'group_id':'hk','node_id':'hk-1'}))
        with patch('proxy_manager.plugin.request',fake_request), patch.object(self.module.httpx,'AsyncClient',return_value=put_client):
            result=asyncio.run(manager.control_select())
        self.assertTrue(result['ok'])
        put_client.request.assert_awaited_once_with('PUT','/proxies/group-hk',json={'name':'node-hk-1'})

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

    def test_first_runtime_apply_failure_installs_verified_fail_closed_config(self):
        manager = self._manager_for_runtime()
        request = self.module.httpx.Request('GET', 'http://mihomo:9090/check')
        def response(payload):
            return self.module.httpx.Response(200, json=payload, request=request)
        expected = manager._runtime_document()
        client = AsyncMock(); client.__aenter__.return_value = client
        client.put.return_value = response({})
        client.get.side_effect = [
            response({'mode': 'rule'}),
            response({'proxies': {item['name']: {'type': 'URLTest','all':['wrong-member'],'now':'wrong-member'} for item in expected['proxy-groups']}}),
            response({'rules': [{'payload': 'wrong-rule'} for _ in expected['rules']]}),
            response({'mode': 'rule'}), response({'proxies': {}}),
            response({'rules': [{'type':'MATCH','payload':'','proxy':'REJECT'}]}),
        ]
        with patch.object(manager, '_kernel_status', AsyncMock(return_value={'state': 'applied'})), \
             patch.object(self.module.httpx, 'AsyncClient', return_value=client):
            result = asyncio.run(manager.runtime_apply())
        self.assertEqual(client.put.await_count,2)
        put_args = client.put.await_args_list[0]
        self.assertEqual(put_args.args[0], '/configs?force=true')
        self.assertEqual(put_args.kwargs['json']['path'], '')
        self.assertIn('proxy-groups:', put_args.kwargs['json']['payload'])
        self.assertEqual([call.args[0] for call in client.get.await_args_list], ['/configs', '/proxies', '/rules']*2)
        self.assertNotEqual(result.get('applied'), True, '规则内容不一致时不得报告应用成功')
        self.assertEqual(manager.runtime_application['status'],'fail_closed')
        self.assertEqual(manager.runtime_application['applied_revision'],'')
        self.assertEqual(manager.runtime_application['document']['rules'],['MATCH,REJECT'])
        recovery_payload=client.put.await_args_list[1].kwargs['json']['payload']
        self.assertIn('MATCH,REJECT',recovery_payload)
        self.assertNotIn('MATCH,DIRECT',recovery_payload)

    def test_runtime_apply_failure_restores_only_previous_verified_revision(self):
        manager=self._manager_for_runtime(); previous=manager._runtime_document()
        previous['rules']=['DOMAIN,verified.example,group-hk','MATCH,DIRECT']
        manager.runtime_application={'status':'applied','applied_revision':manager._runtime_revision(previous),'document':previous}
        request=self.module.httpx.Request('GET','http://mihomo:9090/check')
        def response(payload): return self.module.httpx.Response(200,json=payload,request=request)
        proxies={proxy['name']:{'type':proxy['type']} for proxy in previous['proxies']}
        type_names={'select':'Selector','url-test':'URLTest','fallback':'Fallback'}
        for group in previous['proxy-groups']:
            proxies[group['name']]={'type':type_names[group['type']],'all':group['proxies'],'now':group['proxies'][0]}
        restored_rules=[{'type':kind,'payload':payload,'proxy':target} for kind,payload,target in manager._expected_rules(previous)]
        client=AsyncMock(); client.__aenter__.return_value=client; client.put=AsyncMock(return_value=response({}))
        client.get=AsyncMock(side_effect=[
            response({'mode':'global'}),response({'proxies':{}}),response({'rules':[]}),
            response({'mode':'rule'}),response({'proxies':proxies}),response({'rules':restored_rules}),
        ])
        with patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'applied'})), \
             patch.object(self.module.httpx,'AsyncClient',return_value=client):
            result=asyncio.run(manager.runtime_apply())
        self.assertEqual(result['status'],500)
        self.assertEqual(manager.runtime_application['status'],'pending_apply')
        self.assertEqual(manager.runtime_application['document'],previous)
        self.assertEqual(manager.runtime_application['applied_revision'],manager._runtime_revision(previous))
        self.assertIn('verified.example',client.put.await_args_list[1].kwargs['json']['payload'])

    def test_runtime_apply_and_recovery_failure_reports_restore_failed(self):
        manager=self._manager_for_runtime(); request=self.module.httpx.Request('GET','http://mihomo:9090/check')
        def response(payload): return self.module.httpx.Response(200,json=payload,request=request)
        client=AsyncMock(); client.__aenter__.return_value=client
        client.put=AsyncMock(side_effect=[response({}),self.module.httpx.ConnectError('core stopped during recovery')])
        client.get=AsyncMock(side_effect=[response({'mode':'global'}),response({'proxies':{}}),response({'rules':[]})])
        with patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'saved'})), \
             patch.object(self.module.httpx,'AsyncClient',return_value=client):
            result=asyncio.run(manager.runtime_apply())
        self.assertEqual(result['status'],500)
        self.assertEqual(manager.runtime_application['status'],'restore_failed')
        self.assertIn('恢复核对失败',manager.runtime_application['message'])

    def test_unverified_or_tampered_recovery_document_is_never_restored(self):
        manager=self._manager_for_runtime(); document=manager._runtime_document()
        manager.runtime_application={'status':'applied','applied_revision':'wrong-revision','document':document}
        self.assertIsNone(manager._verified_recovery_document(manager.runtime_application))
        manager.runtime_application={'status':'saved','applied_revision':manager._runtime_revision(document),'document':document}
        self.assertIsNone(manager._verified_recovery_document(manager.runtime_application))

    def test_fail_closed_kernel_status_requires_running_reject_rule(self):
        manager=self._manager_for_runtime(); recovery=manager._fail_closed_document(manager.state['control'],manager.state['proxy_entry'])
        manager.runtime_application={'status':'fail_closed','applied_revision':'','document':recovery}
        request=self.module.httpx.Request('GET','http://mihomo:9090/check')
        def response(payload): return self.module.httpx.Response(200,json=payload,request=request)
        client=AsyncMock(); client.__aenter__.return_value=client
        client.get=AsyncMock(side_effect=[response({'meta':True,'version':'1.19.0'}),response({'mode':'rule'}),response({'proxies':{}}),response({'rules':[{'type':'MATCH','payload':'','proxy':'REJECT'}]})])
        with patch.object(self.module.httpx,'AsyncClient',return_value=client):
            status=asyncio.run(manager._kernel_status())
        self.assertEqual(status['state'],'fail_closed'); self.assertFalse(status['ready'])

    def test_fail_closed_kernel_status_rejects_direct_runtime_drift(self):
        manager=self._manager_for_runtime(); recovery=manager._fail_closed_document(manager.state['control'],manager.state['proxy_entry'])
        manager.runtime_application={'status':'fail_closed','applied_revision':'','document':recovery}
        request=self.module.httpx.Request('GET','http://mihomo:9090/check')
        def response(payload): return self.module.httpx.Response(200,json=payload,request=request)
        client=AsyncMock(); client.__aenter__.return_value=client
        client.get=AsyncMock(side_effect=[response({'meta':True,'version':'1.19.0'}),response({'mode':'rule'}),response({'proxies':{}}),response({'rules':[{'type':'MATCH','payload':'','proxy':'DIRECT'}]})])
        with patch.object(self.module.httpx,'AsyncClient',return_value=client):
            status=asyncio.run(manager._kernel_status())
        self.assertEqual(status['state'],'runtime_inconsistent'); self.assertIn('失败关闭',status['message'])

    def test_runtime_apply_success_persists_verified_revision(self):
        manager=self._manager_for_runtime(); document=manager._runtime_document()
        request=self.module.httpx.Request('GET','http://mihomo:9090/check')
        def response(payload): return self.module.httpx.Response(200,json=payload,request=request)
        proxies={proxy['name']:{'type':proxy['type']} for proxy in document['proxies']}
        type_names={'select':'Selector','url-test':'URLTest','fallback':'Fallback'}
        for group in document['proxy-groups']:
            proxies[group['name']]={'type':type_names[group['type']],'all':group['proxies'],'now':group['proxies'][0]}
        rules=[{'type':kind,'payload':payload,'proxy':target} for kind,payload,target in manager._expected_rules(document)]
        client=AsyncMock(); client.__aenter__.return_value=client; client.put=AsyncMock(return_value=response({}))
        client.get=AsyncMock(side_effect=[response({'mode':'rule'}),response({'proxies':proxies}),response({'rules':rules})])
        with patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'saved'})), \
             patch.object(self.module.httpx,'AsyncClient',return_value=client):
            result=asyncio.run(manager.runtime_apply())
        self.assertTrue(result['applied']); self.assertEqual(result['saved_revision'],result['applied_revision'])
        self.assertEqual(manager.runtime_application['status'],'applied')
        self.assertEqual(manager.runtime_path.stat().st_mode & 0o777,0o600)

    def test_runtime_verification_rejects_wrong_group_members_and_rule_order(self):
        manager=self._manager_for_runtime(); manager.state['rule_groups']=[
            {'id':'one','name':'One','domains':[{'host':'one.example','match':'exact'}],'priority':1,'target':'hk','enabled':True},
            {'id':'two','name':'Two','domains':[{'host':'two.example','match':'exact'}],'priority':2,'target':'sg','enabled':True}]
        document=manager._runtime_document()
        proxies={proxy['name']:{'type':proxy['type']} for proxy in document['proxies']}
        for group in document['proxy-groups']:
            proxies[group['name']]={'type':'URLTest','all':['wrong'],'now':'wrong'}
        rules=[{'type':kind,'payload':payload,'proxy':target} for kind,payload,target in reversed(manager._expected_rules(document))]
        errors=manager._verify_runtime_data(document,{'mode':'rule'},proxies,rules)
        self.assertTrue(any('成员' in error for error in errors)); self.assertTrue(any('规则' in error for error in errors))

    def test_shared_instance_application_is_blocked_before_write(self):
        manager=self._manager_for_runtime(); manager.state['control'].update({'deployment':'existing','scope':'providers-groups-rules'})
        client=AsyncMock(); client.__aenter__.return_value=client
        with patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'saved'})), \
             patch.object(self.module.httpx,'AsyncClient',return_value=client):
            result=asyncio.run(manager.runtime_apply())
        self.assertEqual(result['status'],400); client.put.assert_not_awaited()

    def test_probe_task_reports_current_run_skips_without_overwriting_health(self):
        manager=self._manager_for_runtime(); manager.state['nodes'][0]['excluded']=True
        manager.health['hk-1']={'status':'ok','latency_ms':9,'checked_at':1}
        result=asyncio.run(manager._probe_one({'node_id':'hk-1'}))
        self.assertEqual(result['status'],'skipped'); self.assertIn('排除',result['reason'])
        self.assertEqual(manager.health['hk-1']['latency_ms'],9)

    def test_probe_task_does_not_truncate_over_one_hundred_nodes(self):
        manager=self._manager_for_runtime(); manager.probe_tasks={}
        node_ids=['node-'+str(index) for index in range(125)]
        manager.probe_tasks['task']={'id':'task','status':'running','total':len(node_ids),'completed':0,'results':[],'cancelled':False,'started_at':1}
        with patch.object(manager,'_probe_one',AsyncMock(side_effect=lambda payload:{'node_id':payload['node_id'],'status':'skipped','reason':'测试'})):
            asyncio.run(manager._run_probe_task('task',node_ids,'https://example.com',5,7))
        self.assertEqual(manager.probe_tasks['task']['completed'],125)
        self.assertEqual(len(manager.probe_tasks['task']['results']),125)

    def test_auto_group_parameters_and_fail_closed_membership(self):
        manager=self._manager_for_runtime(); group=next(item for item in manager.state['groups'] if item['id']=='hk')
        group.update({'test_url':'https://probe.example/204','test_interval':45,'tolerance':17,'failure_policy':'fail-closed'})
        item=next(item for item in manager._runtime_document()['proxy-groups'] if item['name']=='group-hk')
        self.assertEqual((item['url'],item['interval'],item['tolerance']),('https://probe.example/204',45,17))
        self.assertFalse(item['lazy']); self.assertNotIn('DIRECT',item['proxies'])

    def test_rule_group_preview_and_runtime_use_same_order(self):
        manager=self._manager_for_runtime(); manager.state['rule_groups']=[
            {'id':'suffix','name':'Meta suffix','domains':[{'host':'facebook.com','match':'suffix'}],'priority':20,'target':'sg','enabled':True},
            {'id':'exact','name':'Meta exact','domains':[{'host':'graph.facebook.com','match':'exact'}],'priority':10,'target':'hk','enabled':True}]
        matched=manager._match_rule('graph.facebook.com'); self.assertEqual(matched['rule_group_id'],'exact')
        self.assertEqual(manager._runtime_document()['rules'][:2],[
            'DOMAIN,graph.facebook.com,group-hk','DOMAIN-SUFFIX,facebook.com,group-sg'])

    def test_outbound_verification_keeps_entry_rule_and_exit_evidence_separate(self):
        manager=self._manager_for_runtime(); manager.state['rule_groups']=[
            {'id':'ip','name':'IP','domains':[{'host':'api.ipify.org','match':'exact'}],'priority':1,'target':'hk','enabled':True}]
        response=self.module.httpx.Response(200,json={'ip':'203.0.113.9'},request=self.module.httpx.Request('GET','https://api.ipify.org/?format=json'))
        proxies=self.module.httpx.Response(200,json={'proxies':{'group-hk':{'now':'node-hk-1'}}},request=self.module.httpx.Request('GET','http://mihomo:9090/proxies'))
        entry=AsyncMock(); entry.__aenter__.return_value=entry; entry.get=AsyncMock(return_value=response)
        control=AsyncMock(); control.__aenter__.return_value=control; control.get=AsyncMock(return_value=proxies)
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'url':'https://api.ipify.org?format=json'}))
        with patch('proxy_manager.plugin.request',fake_request), patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'applied'})), \
             patch.object(self.module.httpx,'AsyncClient',side_effect=[entry,control]):
            result=asyncio.run(manager.verify_outbound())
        self.assertTrue(result['verified']); self.assertEqual(result['entry']['state'],'passed')
        self.assertEqual(result['rule']['state'],'matched'); self.assertEqual(result['exit']['state'],'confirmed')
        self.assertEqual(result['exit']['ip'],'203.0.113.9'); self.assertEqual(result['actual_selection'],'node-hk-1')

    def test_entry_success_without_exit_ip_is_unconfirmed(self):
        manager=self._manager_for_runtime()
        response=self.module.httpx.Response(204,content=b'',request=self.module.httpx.Request('GET','https://www.gstatic.com/generate_204'))
        entry=AsyncMock(); entry.__aenter__.return_value=entry; entry.get=AsyncMock(return_value=response)
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'url':'https://www.gstatic.com/generate_204'}))
        with patch('proxy_manager.plugin.request',fake_request), patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'applied'})), \
             patch.object(self.module.httpx,'AsyncClient',return_value=entry):
            result=asyncio.run(manager.verify_outbound())
        self.assertFalse(result['verified']); self.assertEqual(result['entry']['state'],'passed')
        self.assertEqual(result['rule']['state'],'default'); self.assertEqual(result['exit']['state'],'unconfirmed')


if __name__ == "__main__":
    unittest.main()
