import asyncio
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from proxy_manager.compat.lease import ComponentLease
from proxy_manager.compat.provider_transport import (
    ProxySession, ScopedAiohttp, ScopedDashscopeCall, build_transport_provider,
    scoped_xinference_client,
)


ENTRY = 'http://127.0.0.1:17890'


class TestProviderTransport(unittest.IsolatedAsyncioTestCase):
    async def test_session_forces_entry_even_for_loopback_and_override(self):
        original = Mock()
        original.close = AsyncMock()
        original.__aenter__ = AsyncMock(return_value=original)
        original.__aexit__ = AsyncMock()
        session = ProxySession(original, ComponentLease('test', ENTRY))
        async with session as entered:
            self.assertIs(entered, session)
            session.get('http://localhost/audio', proxy=None)
            session.post('https://example.invalid', proxy='http://other')
            session.delete('https://example.invalid')
        self.assertTrue(all(call.kwargs['proxy'] == ENTRY for call in original.request.call_args_list))
        await session.close()
        original.close.assert_awaited_once()

    async def test_missing_entry_fails_before_client_creation(self):
        module = Mock()
        factory = ScopedAiohttp(module, ComponentLease('test', ''))
        with self.assertRaises(ValueError):
            factory.ClientSession()
        module.ClientSession.assert_not_called()

    async def test_lazy_and_ephemeral_sessions_are_scoped_without_global_patch(self):
        module = types.SimpleNamespace(ClientSession=Mock())
        raw = Mock(closed=False)
        module.ClientSession.return_value = raw
        namespace = {'aiohttp': module}
        exec('''
class Base:
    def __init__(self, config, settings):
        self.config = config
        self.client = None
    async def _get_client(self):
        if self.client is None or self.client.closed:
            self.client = aiohttp.ClientSession(trust_env=True)
        return self.client
''', namespace)
        base = namespace['Base']
        wrapped = build_transport_provider('nvidia_rerank', base, ComponentLease('test', ENTRY))
        config = {'nested': ['original']}
        instance = wrapped(config, {})
        first = await instance._get_client()
        self.assertIsInstance(first, ProxySession)
        self.assertIs(base._get_client.__globals__['aiohttp'], module)
        self.assertIs((await base({}, {})._get_client()), raw)
        raw.closed = True
        raw2 = Mock(closed=False)
        module.ClientSession.return_value = raw2
        rebuilt = await instance._get_client()
        self.assertIsNot(first, rebuilt)
        rebuilt.post('https://example.invalid')
        self.assertEqual(raw2.request.call_args.kwargs['proxy'], ENTRY)
        self.assertFalse(module.ClientSession.call_args.kwargs['trust_env'])
        self.assertNotIn('proxy', config)

    async def test_dashscope_call_supplies_owned_session_and_closes_on_failure(self):
        sdk = types.SimpleNamespace(call=Mock(side_effect=RuntimeError('refused')))
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        with patch('requests.Session', return_value=session):
            with self.assertRaises(RuntimeError):
                ScopedDashscopeCall(sdk, ComponentLease('test', ENTRY)).call(model='probe')
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies, {'http': ENTRY, 'https': ENTRY})
        self.assertIs(sdk.call.call_args.kwargs['session'], session)
        session.__exit__.assert_called_once()

    async def test_xinference_constructor_auth_and_separate_model_handle(self):
        class Client:
            def __init__(self, base_url):
                self.base_url = base_url
                self.session = Mock()
                self._check_cluster_authenticated()

            async def get_model(self, uid):
                return types.SimpleNamespace(session=Mock())

        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {'auth': True}
        with patch('requests.Session', return_value=session):
            scoped = scoped_xinference_client(Client, ComponentLease('test', ENTRY))
            client = scoped('https://example.invalid')
        self.assertTrue(client._cluster_authed)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_args.kwargs['proxies'], {'http': ENTRY, 'https': ENTRY})
        self.assertIsInstance(client.session, ProxySession)
        model = await client.get_model('probe')
        self.assertIsInstance(model.session, ProxySession)
        model.session.post('https://example.invalid/v1/rerank')
        self.assertEqual(model.session._session.request.call_args.kwargs['proxy'], ENTRY)

    async def test_partial_and_local_coverage_are_not_reported_as_complete(self):
        from proxy_manager.compat.provider_registry import PROVIDER_ADAPTER_MAP
        from proxy_manager.compat.registry import SUPPORTED_PROVIDER_TYPES

        self.assertEqual(len(SUPPORTED_PROVIDER_TYPES), 40)
        self.assertEqual(PROVIDER_ADAPTER_MAP['dashscope_tts'].proxy_mode, 'partial')
        self.assertNotIn('dashscope_tts', SUPPORTED_PROVIDER_TYPES)
        for name in ('sensevoice_stt_selfhost', 'openai_whisper_selfhost'):
            self.assertEqual(PROVIDER_ADAPTER_MAP[name].proxy_mode, 'partial')
            self.assertNotIn(name, SUPPORTED_PROVIDER_TYPES)
        for name in ('genie_tts',):
            self.assertEqual(PROVIDER_ADAPTER_MAP[name].proxy_mode, 'unverified')
            self.assertIn('本地', PROVIDER_ADAPTER_MAP[name].coverage)
