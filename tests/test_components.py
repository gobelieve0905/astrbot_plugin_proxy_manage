import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from proxy_manager.domain.components import component_entries, component_tag, normalize_component_routes
from proxy_manager.domain.model import normalize_state
from proxy_manager.traffic.audit import AstrBotTrafficAudit
from proxy_manager.traffic.integration import PROTOCOL


def fixture():
    state, _ = normalize_state({'nodes': [], 'groups': [], 'routes': [], 'subscriptions': [],
                               'control': {'enabled': True, 'deployment': 'dedicated', 'scope': 'full',
                                           'url': 'http://127.0.0.1:19090', 'listen': '127.0.0.1:19090'},
                               'proxy_entry': {'http_url': 'http://127.0.0.1:17890', 'private': {'enabled': False}}})
    state['component_routes'] = normalize_component_routes([
        {'id': 'plugin-example', 'kind': 'plugin', 'target': 'direct', 'enabled': True},
        {'id': 'mcp-example', 'kind': 'mcp', 'target': 'missing-group', 'enabled': True},
        {'id': 'plugin-stopped', 'kind': 'plugin', 'target': 'direct', 'enabled': False},
    ])
    return state


class TestComponentPolicies(unittest.TestCase):
    def test_ports_survive_normalization_and_reject_missing_or_disabled_groups(self):
        state = fixture()
        normalized, _ = normalize_state(state)
        self.assertEqual(normalized['component_routes'], state['component_routes'])
        entries = component_entries(normalized)
        self.assertEqual([item['outbound'] for item in entries], ['DIRECT', 'REJECT', 'REJECT'])
        self.assertEqual(len({item['port'] for item in entries}), 3)
        self.assertNotEqual(component_tag('plugin-a.b'), component_tag('plugin-a-b'))
        state['groups'][0]['enabled'] = False
        self.assertTrue(all(item['outbound'] == 'REJECT' for item in component_entries(state)))

    def test_invalid_types_and_duplicate_ports_are_rejected(self):
        for values in ({}, [{'id': 'plugin-a', 'kind': 'plugin', 'enabled': 'false'}],
                       [{'id': 'plugin-a', 'kind': 'plugin', 'enabled': True, 'port': 17890}],
                       [{'id': 'plugin-a', 'kind': 'plugin', 'enabled': True, 'port': 18000},
                        {'id': 'mcp-b', 'kind': 'mcp', 'enabled': True, 'port': 18000}]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalize_component_routes(values)

    def test_all_adapters_route_component_entries_before_domain_rules_and_catchall(self):
        from proxy_manager.cores.registry import all_adapters
        state = fixture()
        state['rule_groups'] = [{'id': 'all', 'name': 'all', 'enabled': True, 'priority': 1,
                                 'target': 'direct', 'domains': [{'type': 'DOMAIN', 'payload': 'example.com'}]}]
        entries = component_entries(state)
        for adapter_id, adapter in all_adapters().items():
            with self.subTest(adapter=adapter_id):
                document = adapter.render(state); adapter.validate(document)
                self.assertEqual(adapter.expected_rules(document)[:3],
                                 [('IN-NAME', item['tag'], item['outbound']) for item in entries])
                inbounds = document.get('listeners', document.get('inbounds'))
                for entry in entries:
                    inbound = next(item for item in inbounds if (item.get('name') or item.get('tag')) == entry['tag'])
                    self.assertEqual(inbound.get('port', inbound.get('listen_port')), entry['port'])
                    self.assertEqual(inbound['listen'], '127.0.0.1')

    def test_private_component_entries_require_authentication_on_every_adapter(self):
        from proxy_manager.cores.registry import all_adapters
        state = fixture(); state['component_routes'][0]['scope'] = 'private'
        with self.assertRaises(ValueError): component_entries(state)
        state['proxy_entry']['private'] = {'enabled': True, 'listen': '0.0.0.0', 'port': 17891,
                                           'username': 'fixture-user', 'password': 'fixture-secret'}
        tag = component_tag('plugin-example')
        for adapter_id, adapter in all_adapters().items():
            with self.subTest(adapter=adapter_id):
                document = adapter.render(state); adapter.validate(document)
                inbound = next(item for item in document.get('listeners', document.get('inbounds'))
                               if (item.get('name') or item.get('tag')) == tag)
                self.assertEqual(inbound['listen'], '0.0.0.0')
                self.assertIn('fixture-secret', json.dumps(inbound))
                self.assertNotIn('fixture-secret', json.dumps(adapter.redact(document)))

    def test_mcp_audit_matches_dedicated_component_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'data').mkdir()
            server = {'command': 'node', 'proxy_manager': {'protocol': PROTOCOL, 'mode': 'astrbot-environment',
                       'protocols': ['http', 'https'], 'auto_apply': False},
                       'env': {'HTTP_PROXY': 'http://127.0.0.1:18001', 'HTTPS_PROXY': 'http://127.0.0.1:18001'}}
            (root/'data'/'mcp_server.json').write_text(json.dumps({'mcpServers': {'example': server}}))
            state = fixture()
            audit = AstrBotTrafficAudit(root).snapshot('http://127.0.0.1:17890', components=state['component_routes'])
            self.assertTrue(audit['mcps'][0]['protocol_connected'])
            server['env']['HTTP_PROXY'] = 'http://127.0.0.1:17890'
            (root/'data'/'mcp_server.json').write_text(json.dumps({'mcpServers': {'example': server}}))
            self.assertFalse(AstrBotTrafficAudit(root).snapshot('http://127.0.0.1:17890', components=state['component_routes'])['mcps'][0]['protocol_connected'])


class TestComponentBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_manager import install_astrbot_stubs
        install_astrbot_stubs()
        from proxy_manager.plugin import ProxyManager
        cls.manager_type = ProxyManager

    def manager(self):
        manager = self.manager_type.__new__(self.manager_type)
        manager.state = fixture()
        manager.supervisor = Mock(); manager.supervisor.status.return_value = {'ready': True}
        document = manager._runtime_document()
        manager.runtime_application = {'status': 'applied', 'applied_revision': manager._runtime_revision(document)}
        return manager

    def test_market_plugin_lease_uses_dedicated_entry_and_refuses_stale_disabled_policy(self):
        manager = self.manager()
        manager.context = Mock()
        manager.context.get_registered_star.return_value = types.SimpleNamespace(activated=True, star_cls=object(), root_dir_name='example')
        manager._refresh_integration_audit = Mock(return_value={'plugin_integrations': [{
            'id':'plugin-example', 'name':'example', 'kind':'plugin',
            'declaration':{'state':'compatible','mode':'astrbot-environment','protocols':['http','https']}}]})
        manager._astrbot_status = Mock(return_value={'effective': True, 'no_proxy': ['localhost']})
        manager._entry_urls = Mock(return_value=('http://127.0.0.1:17890', ''))
        lease = manager.get_proxy_manager_lease('example', enabled=True)
        self.assertEqual(lease['http_proxy'], 'http://127.0.0.1:18000')
        self.assertEqual(lease['target'], 'direct')
        manager.state['component_routes'][0]['enabled'] = False
        with self.assertRaises(ValueError): manager.get_proxy_manager_lease('example', enabled=True)
        manager.state['component_routes'][0]['enabled'] = True
        manager.runtime_application['applied_revision'] = 'stale'
        with self.assertRaises(RuntimeError): manager.get_proxy_manager_lease('example', enabled=True)

    def test_save_owns_ports_and_keeps_removed_entries_rejected(self):
        manager = self.manager()
        manager._refresh_integration_audit = Mock(return_value={'plugin_integrations': [{
            'id':'plugin-example','kind':'plugin','declaration':{'state':'compatible',
                'mode':'astrbot-environment','protocols':['http']}}]})
        payload = {'component_routes': [{**manager.state['component_routes'][0], 'port': 18500}]}
        manager._prepare_component_routes(payload)
        self.assertEqual(payload['component_routes'][0]['port'], 18000)
        self.assertEqual([item['enabled'] for item in payload['component_routes']], [True, False, False])
        payload['component_routes'][0]['id'] = 'plugin-unknown'
        with self.assertRaises(ValueError): manager._prepare_component_routes(payload)

    def test_explicit_mcp_bind_backs_up_and_preserves_unrelated_configuration(self):
        manager = self.manager(); manager.state['component_routes'][1]['target'] = 'direct'
        manager.runtime_application['applied_revision'] = manager._runtime_revision(manager._runtime_document())
        manager.operation_lock = asyncio.Lock(); manager.event = Mock(); manager.snapshot = Mock(return_value={})
        manager._refresh_integration_audit = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'data').mkdir()
            manager.traffic_audit = AstrBotTrafficAudit(root)
            config = {'mcpServers': {'example': {'command':'node', 'args':['server.js'],
                      'env':{'BUSINESS_TOKEN':'do-not-expose','NO_PROXY':'external.example'},
                      'proxy_manager':{'protocol':PROTOCOL,'mode':'astrbot-environment','protocols':['https'],'auto_apply':False}},
                      'other':{'command':'unrelated'}}}
            original = json.dumps(config); manager.traffic_audit.mcp_path.write_text(original)
            from proxy_manager import plugin
            with patch.object(plugin, 'request', types.SimpleNamespace(json=AsyncMock(return_value={'id':'mcp-example'}))):
                result = asyncio.run(manager.component_bind())
            self.assertIn('重启', result['message'])
            saved = json.loads(manager.traffic_audit.mcp_path.read_text())
            self.assertEqual(saved['mcpServers']['example']['env']['HTTPS_PROXY'], 'http://127.0.0.1:18001')
            self.assertEqual(saved['mcpServers']['example']['env']['BUSINESS_TOKEN'], 'do-not-expose')
            self.assertEqual(saved['mcpServers']['example']['env']['NO_PROXY'], 'localhost,127.0.0.1,::1')
            self.assertEqual(saved['mcpServers']['other'], config['mcpServers']['other'])
            self.assertEqual((root/'data'/'mcp_server.proxy-manager-backup.json').read_text(), original)
            self.assertNotIn('do-not-expose', json.dumps(result))
