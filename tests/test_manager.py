import asyncio
import base64
import copy
import gzip
import hashlib
import json
import importlib.util
import os
import sys
import tarfile
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


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
        self.assertEqual(manager._adapter().render_proxy(manager.state['nodes'][0])['name'],'node-hk-1')
        source=Path(__file__).resolve().parents[1]/'proxy_manager'/'plugin.py'
        text=source.read_text(encoding='utf-8')
        self.assertNotIn('external-controller', text)
        self.assertNotIn('skip-cert-verify', text)
        self.assertNotIn('mixed-port', text)
        self.assertNotIn('/configs?force=true', text)
        package=source.parent
        for module in package.rglob('*.py'):
            self.assertNotIn('from proxy_manager.',module.read_text(encoding='utf-8'),str(module))

    def test_component_lease_is_scoped_and_redacts_nothing_extra(self):
        from proxy_manager.compat.lease import ComponentLease

        lease = ComponentLease.from_entry(
            "astrbot",
            {"http_url": "http://127.0.0.1:17890", "socks_url": "socks5://127.0.0.1:17890"},
            revision="r1",
        )
        child = lease.for_component("platform:telegram")
        self.assertEqual(child.component_id, "platform:telegram")
        self.assertEqual(child.http_proxy, lease.http_proxy)
        self.assertEqual(child.socks_proxy, lease.socks_proxy)
        self.assertEqual(child.as_public_dict()["no_proxy"], ["localhost", "127.0.0.1", "::1"])

    def test_compatibility_fingerprint_rejects_unknown_runtime(self):
        from proxy_manager.compat import registry

        with patch.object(registry, "_astrbot_version", return_value="4.29.0"), patch.object(
            registry,
            "_package_version",
            side_effect=lambda name: registry.SUPPORTED_SDK_VERSIONS[name],
        ):
            report = registry.inspect_runtime()
        self.assertEqual(report.state, "unsupported")
        self.assertIn("AstrBot 4.29.0", report.message)

    def test_telegram_compatibility_sets_bot_and_polling_proxies(self):
        from proxy_manager.compat.telegram import build_proxy_adapter
        from proxy_manager.compat.lease import ComponentLease

        module_name = "compat_test_telegram"
        fake = types.ModuleType(module_name)

        class Builder:
            def __init__(self):
                self.values = {}
                type(self).last = self

            def token(self, value):
                self.values["token"] = value
                return self

            def base_url(self, value):
                self.values["base_url"] = value
                return self

            def base_file_url(self, value):
                self.values["base_file_url"] = value
                return self

            def proxy(self, value):
                self.values["proxy"] = value
                return self

            def get_updates_proxy(self, value):
                self.values["get_updates_proxy"] = value
                return self

            def build(self):
                return types.SimpleNamespace(bot=types.SimpleNamespace(base_url="base"), add_handler=lambda *_: None)

        fake.ApplicationBuilder = Builder
        fake.filters = types.SimpleNamespace(ALL=object())
        fake.TelegramMessageHandler = lambda **kwargs: kwargs
        fake.logger = types.SimpleNamespace(debug=lambda *_: None)
        sys.modules[module_name] = fake
        try:
            class Base:
                __module__ = module_name

                def __init__(self, *_args):
                    self.config = {"telegram_token": "token"}
                    self.base_url = "https://api.telegram.org/bot"
                    self.file_base_url = "https://api.telegram.org/file/bot"
                    self.message_handler = lambda *_: None
                    self._build_application()

            adapter_class = build_proxy_adapter(
                Base,
                ComponentLease(component_id="telegram", http_proxy="http://127.0.0.1:17890"),
            )
            adapter = adapter_class({}, {}, None)
            self.assertEqual(Builder.last.values["proxy"], "http://127.0.0.1:17890")
            self.assertEqual(Builder.last.values["get_updates_proxy"], "http://127.0.0.1:17890")
        finally:
            sys.modules.pop(module_name, None)

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

    def test_management_page_has_responsive_navigation_and_stable_controls(self):
        root=Path(__file__).resolve().parents[1]/'pages'/'manage'
        html=(root/'index.html').read_text(encoding='utf-8')
        script=(root/'app.js').read_text(encoding='utf-8')
        styles='\n'.join((root/name).read_text(encoding='utf-8') for name in ('style.css','health.css','download.css'))
        self.assertIn('流量控制 · 0.3.15',html)
        self.assertIn('平台域名模板',html)
        self.assertIn('traffic_inventory',script)
        self.assertIn('kernel-resources',script)
        self.assertIn('<details class="kernel-resource',script)
        self.assertIn('data-kernel-file',script)
        self.assertIn('data-kernel-upload',script)
        self.assertLess(script.index('<h2>运行控制</h2>'),script.index('<h2>内核资源管理</h2>'))
        self.assertIn('data-kernel-install',script)
        self.assertIn('core-enable',script)
        self.assertIn('kernel-update-check',script)
        self.assertIn('data-kernel-check',script)
        self.assertIn('data-kernel-uninstall',script)
        self.assertIn('kernel-progress',script + styles)
        self.assertIn('kernel-download-url',script + styles)
        self.assertNotIn('download-status',script + styles)
        self.assertNotIn("$('kernel-install')",script)
        self.assertNotIn("$('kernel-install-cancel')",script)
        self.assertIn('aria-label="主导航"',html)
        self.assertIn("classList.toggle('active'",script)
        self.assertIn("$('content').dataset.view=tab",script)
        self.assertIn('@media (max-width: 700px)',styles)
        self.assertIn('input[type="checkbox"]',styles)
        self.assertIn('grid-template-columns: minmax(210px, 1fr)',styles)

    def test_fixed_artifact_manifest_and_offline_digest_enforcement(self):
        from proxy_manager.runtime.artifacts import ArtifactManager
        from proxy_manager.cores.registry import current_adapter
        with tempfile.TemporaryDirectory() as directory:
            manager=ArtifactManager(Path(directory),current_adapter().artifact())
            self.assertEqual(manager.manifest['version'],'1.19.31')
            self.assertIn(manager.platform['libc'],{'glibc','musl','none'})
            if manager.selected() is None: self.skipTest('测试平台不在固定制品清单中')
            archive=gzip.compress(b'fixed-test-binary')
            selected=manager.manifest['artifacts'][manager.selected()['key']]
            selected.update({'format':'gz','sha256':hashlib.sha256(archive).hexdigest(),'name':'fixture.gz'})
            status=manager.install(archive,'offline')
            self.assertTrue(status['ready']); self.assertEqual(status['source'],'offline')
            before=manager.binary.read_bytes()
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                manager.install(archive+b'tampered','offline')
            self.assertEqual(manager.binary.read_bytes(),before)
            self.assertEqual(manager.binary.stat().st_mode & 0o777,0o700)

    def test_artifact_catalog_exposes_installed_version_and_fixed_updates(self):
        from proxy_manager.runtime.artifacts import ArtifactManager
        manifest={
            'adapter':'fixture','version':'2.0.0','recommended_version':'2.0.0',
            'versions':{
                '1.0.0':{'version':'1.0.0','artifacts':{}},
                '2.0.0':{'version':'2.0.0','artifacts':{}},
            },
            'artifacts':{},
        }
        with patch('proxy_manager.runtime.artifacts.detect_platform',return_value={'os':'linux','arch':'amd64','libc':'glibc','machine':'x86_64'}), tempfile.TemporaryDirectory() as directory:
            archive=gzip.compress(b'old-core')
            manifest['versions']['1.0.0']['artifacts']['linux-amd64-glibc']={'name':'old.gz','format':'gz','sha256':hashlib.sha256(archive).hexdigest()}
            manifest['versions']['2.0.0']['artifacts']['linux-amd64-glibc']={'name':'new.gz','format':'gz','sha256':'0'*64}
            manager=ArtifactManager(Path(directory),manifest,'1.0.0')
            manager.install(archive)
            status=manager.status()
            self.assertEqual(status['installed_version'],'1.0.0')
            self.assertEqual(status['recommended_version'],'2.0.0')
            self.assertTrue(status['update_available'])

    def test_artifact_uninstall_is_idempotent_and_preserves_update_check(self):
        from proxy_manager.runtime.artifacts import ArtifactManager
        manifest={'adapter':'fixture','version':'1.0.0','artifacts':{}}
        with patch('proxy_manager.runtime.artifacts.detect_platform',return_value={'os':'linux','arch':'amd64','libc':'glibc','machine':'x86_64'}), tempfile.TemporaryDirectory() as directory:
            archive=gzip.compress(b'installed-core')
            manifest['artifacts']['linux-amd64-glibc']={'name':'core.gz','format':'gz','sha256':hashlib.sha256(archive).hexdigest()}
            manager=ArtifactManager(Path(directory),manifest)
            manager.install(archive)
            manager.update_check_path.write_text('{"state":"up_to_date"}',encoding='utf-8')
            manager.previous_binary.write_bytes(b'previous')
            manager.previous_metadata.write_text('{}',encoding='utf-8')
            result=manager.uninstall()
            self.assertEqual(result['state'],'uninstalled')
            self.assertFalse(manager.binary.exists())
            self.assertFalse(manager.metadata_path.exists())
            self.assertFalse(manager.previous_binary.exists())
            self.assertFalse(manager.previous_metadata.exists())
            self.assertEqual(json.loads(manager.update_check_path.read_text(encoding='utf-8'))['state'],'up_to_date')
            self.assertEqual(manager.uninstall()['state'],'not_installed')

    def test_artifact_resource_task_exposes_operation_and_progress(self):
        from proxy_manager.runtime.artifacts import ArtifactInstallTask
        async def scenario():
            manager=types.SimpleNamespace(download=AsyncMock(return_value={'ready':True}),commit=lambda: None,rollback=lambda: None)
            task=ArtifactInstallTask(manager,AsyncMock())
            initial=task.start()
            self.assertEqual(initial['operation'],'install')
            self.assertGreaterEqual(initial['progress'],0)
            await asyncio.sleep(0.01)
            self.assertEqual(task.status()['progress'],100)
            task=ArtifactInstallTask(types.SimpleNamespace(uninstall=lambda: {'message':'done'}),AsyncMock())
            uninstall=task.start_uninstall(lambda _progress: asyncio.sleep(0, result={'message':'done'}))
            self.assertEqual(uninstall['operation'],'uninstall')
            await asyncio.sleep(0.01)
            self.assertEqual(task.status()['progress'],100)
        asyncio.run(scenario())

    def test_manual_update_check_does_not_download_and_flags_unreviewed_release(self):
        from proxy_manager.runtime.artifacts import ArtifactManager

        manifest={
            'adapter':'fixture','version':'1.0.0','recommended_version':'1.0.0',
            'release_api_url':'https://api.github.com/repos/example/fixture/releases/latest',
            'versions':{'1.0.0':{'version':'1.0.0','artifacts':{}}},'artifacts':{},
        }
        response=self.module.httpx.Response(
            200,
            json={'tag_name':'v2.0.0','name':'Fixture 2.0.0','html_url':'https://github.com/example/fixture/releases/tag/v2.0.0'},
            request=self.module.httpx.Request('GET',manifest['release_api_url']),
        )
        client=AsyncMock(); client.__aenter__.return_value=client; client.get=AsyncMock(return_value=response)
        with tempfile.TemporaryDirectory() as directory, patch.object(self.module.httpx,'AsyncClient',return_value=client):
            manager=ArtifactManager(Path(directory),manifest)
            manager.download=AsyncMock()
            result=asyncio.run(manager.check_update())
            self.assertEqual(result['state'],'pending_review')
            self.assertEqual(result['latest_version'],'2.0.0')
            manager.download.assert_not_awaited()
            self.assertFalse(manager.binary.exists())

    def test_manual_update_check_accepts_only_fixed_release_versions(self):
        from proxy_manager.runtime.artifacts import ArtifactManager

        manifest={
            'adapter':'fixture','version':'1.0.0','recommended_version':'1.0.0',
            'release_api_url':'https://api.github.com/repos/example/fixture/releases/latest',
            'versions':{
                '1.0.0':{'version':'1.0.0','artifacts':{}},
                '2.0.0':{'version':'2.0.0','artifacts':{}},
            },'artifacts':{},
        }
        response=self.module.httpx.Response(
            200,
            json={'tag_name':'v2.0.0'},
            request=self.module.httpx.Request('GET',manifest['release_api_url']),
        )
        client=AsyncMock(); client.__aenter__.return_value=client; client.get=AsyncMock(return_value=response)
        with tempfile.TemporaryDirectory() as directory, patch.object(self.module.httpx,'AsyncClient',return_value=client):
            manager=ArtifactManager(Path(directory),manifest)
            result=asyncio.run(manager.check_update())
            self.assertEqual(result['state'],'update_available')
            self.assertEqual(result['recommended_version'],'1.0.0')

    def test_artifact_update_rolls_back_previous_binary_when_activation_fails(self):
        from proxy_manager.runtime.artifacts import ArtifactInstallTask, ArtifactManager
        async def scenario():
            manifest={'adapter':'fixture','version':'1.0.0','artifacts':{}}
            with patch('proxy_manager.runtime.artifacts.detect_platform',return_value={'os':'linux','arch':'amd64','libc':'glibc','machine':'x86_64'}), tempfile.TemporaryDirectory() as directory:
                archive=gzip.compress(b'core')
                manifest['artifacts']['linux-amd64-glibc']={'name':'core.gz','format':'gz','sha256':hashlib.sha256(archive).hexdigest(),
                                                           'url':'https://example.invalid/core.gz'}
                manager=ArtifactManager(Path(directory),manifest)
                manager.install(archive)
                task=ArtifactInstallTask(manager,AsyncMock(side_effect=RuntimeError('start failed')))
                manager._download_source=AsyncMock(return_value=archive)
                task.start(); await asyncio.sleep(0); await asyncio.sleep(0.01)
                self.assertEqual(task.status()['state'],'failed')
                self.assertEqual(manager.binary.read_bytes(),b'core')
        asyncio.run(scenario())

    def test_tar_artifact_extracts_only_manifest_binary(self):
        from proxy_manager.runtime.artifacts import ArtifactManager
        manifest={'adapter':'fixture','version':'1.0.0','artifacts':{}}
        with tempfile.TemporaryDirectory() as directory:
            with patch('proxy_manager.runtime.artifacts.detect_platform',return_value={'os':'linux','arch':'amd64','libc':'glibc','machine':'x86_64'}):
                manager=ArtifactManager(Path(directory),manifest)
            archive_file=BytesIO()
            with tarfile.open(fileobj=archive_file,mode='w:gz') as package:
                binary=b'expected-core'; info=tarfile.TarInfo('release/core'); info.size=len(binary)
                package.addfile(info,BytesIO(binary))
                decoy=b'decoy'; info=tarfile.TarInfo('../../core'); info.size=len(decoy)
                package.addfile(info,BytesIO(decoy))
            archive=archive_file.getvalue()
            manifest['artifacts']['linux-amd64-glibc']={
                'name':'fixture.tar.gz','format':'tar.gz','binary_path':'release/core',
                'sha256':hashlib.sha256(archive).hexdigest(),'url':'https://example.invalid/core.tar.gz'}
            result=manager.install(archive)
            self.assertTrue(result['ready']); self.assertEqual(manager.binary.read_bytes(),binary)

    def test_artifact_download_retries_trusted_sources_with_same_digest(self):
        from proxy_manager.runtime.artifacts import ArtifactManager
        from proxy_manager.cores.registry import current_adapter
        with tempfile.TemporaryDirectory() as directory:
            manager=ArtifactManager(Path(directory),current_adapter().artifact())
            archive=gzip.compress(b'trusted-source-binary')
            selected=manager.manifest['artifacts'][manager.selected()['key']]
            selected.update({'format':'gz','sha256':hashlib.sha256(archive).hexdigest(),'name':'fixture.gz',
                             'sources':[{'id':'primary','name':'受信分发源','url':'https://cdn.example/core.gz'},
                                        {'id':'github','name':'GitHub 官方源','url':'https://github.example/core.gz'}]})
            progress=[]
            manager._download_source=AsyncMock(side_effect=[RuntimeError('timeout'),RuntimeError('timeout'),archive])
            with patch('proxy_manager.runtime.artifacts.asyncio.sleep',new=AsyncMock()):
                result=asyncio.run(manager.download(progress.append))
            self.assertTrue(result['ready']); self.assertEqual(result['source'],'github')
            self.assertEqual(manager._download_source.await_count,3)
            self.assertEqual([call.args[1]['id'] for call in manager._download_source.await_args_list],
                             ['primary','primary','github'])
            self.assertEqual(progress[-1]['phase'],'installed')

    def test_artifact_install_task_returns_immediately_and_can_cancel(self):
        from proxy_manager.runtime.artifacts import ArtifactInstallTask
        async def scenario():
            started=asyncio.Event()
            async def download(*_args): started.set(); await asyncio.Event().wait()
            manager=types.SimpleNamespace(download=download)
            task=ArtifactInstallTask(manager,AsyncMock())
            initial=task.start(); self.assertEqual(initial['state'],'running')
            await started.wait()
            cancelled=await task.cancel()
            self.assertEqual(cancelled['state'],'cancelled')
            self.assertEqual(cancelled['phase'],'cancelled')
        asyncio.run(scenario())

    def test_internal_runtime_secret_is_stable_and_not_client_configurable(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=self.module.ProxyManager.__new__(self.module.ProxyManager)
            manager.data_dir=Path(directory); manager.state={'control':{},'proxy_entry':{}}
            manager._bind_owned_runtime(); first=manager.state['control']['secret']
            manager._bind_owned_runtime()
            self.assertEqual(manager.state['control']['secret'],first); self.assertGreaterEqual(len(first),32)
            self.assertEqual(manager.state['control']['listen'],'127.0.0.1:19090')
            self.assertEqual(manager.state['proxy_entry']['http_url'],'http://127.0.0.1:17890')
            self.assertEqual((Path(directory)/'runtime'/'control.secret').stat().st_mode & 0o777,0o600)

    def test_xray_uses_a_separate_stable_socks_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=self.module.ProxyManager.__new__(self.module.ProxyManager)
            manager.data_dir=Path(directory)
            manager.state={'control':{'adapter':'xray'},'proxy_entry':{}}
            manager._bind_owned_runtime()
            self.assertEqual(manager.state['proxy_entry']['http_url'],'http://127.0.0.1:17890')
            self.assertEqual(manager.state['proxy_entry']['socks_url'],'socks5://127.0.0.1:17892')

    def test_core_preferences_are_closed_by_default_and_migrate_current_legacy_core(self):
        manager=self.module.ProxyManager.__new__(self.module.ProxyManager)
        fresh=manager._normalize({})['core_preferences']
        self.assertEqual({key:item['enabled'] for key,item in fresh.items()},
                         {'xray':False,'mihomo':False,'sing-box':False})
        legacy=manager._normalize({'control':{'adapter':'sing-box'}})['core_preferences']
        self.assertEqual({key:item['enabled'] for key,item in legacy.items()},
                         {'xray':False,'mihomo':False,'sing-box':True})

    def test_proxy_environment_sync_uses_selected_core_entry(self):
        manager=self.module.ProxyManager.__new__(self.module.ProxyManager)
        manager.state={'proxy_entry':{'http_url':'http://127.0.0.1:17890','socks_url':'socks5://127.0.0.1:17892'}}
        manager.astrbot_proxy=Mock()
        manager.astrbot_proxy.ensure.return_value={'status':'active'}
        self.assertEqual(manager._sync_owned_proxy_environment(),{'status':'active'})
        manager.astrbot_proxy.ensure.assert_called_once_with('http://127.0.0.1:17890','socks5://127.0.0.1:17892')
        manager.astrbot_proxy.apply_process_environment.assert_called_once_with('http://127.0.0.1:17890','socks5://127.0.0.1:17892')

    def test_private_entry_credentials_are_stable_private_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=self.module.ProxyManager.__new__(self.module.ProxyManager)
            manager.data_dir=Path(directory); manager.state={'control':{},'proxy_entry':{}}
            manager._bind_owned_runtime(); first=dict(manager.state['proxy_entry']['private'])
            manager._bind_owned_runtime(); second=manager.state['proxy_entry']['private']
            self.assertEqual((first['username'],first['password']),(second['username'],second['password']))
            credential_path=Path(directory)/'runtime'/'private-entry.json'
            self.assertEqual(credential_path.stat().st_mode & 0o777,0o600)
            public={'enabled':second['enabled'],'port':second['port'],'authenticated':True,'exposure':second['exposure']}
            self.assertNotIn(first['username'],json.dumps(public)); self.assertNotIn(first['password'],json.dumps(public))

    def test_corrupt_private_entry_credentials_rotate_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory)/'runtime'; runtime.mkdir(); (runtime/'private-entry.json').write_text('{broken')
            manager=self.module.ProxyManager.__new__(self.module.ProxyManager)
            manager.data_dir=Path(directory); manager.state={'control':{},'proxy_entry':{}}
            manager._bind_owned_runtime()
            stored=json.loads((runtime/'private-entry.json').read_text())
            self.assertGreaterEqual(len(stored['username']),16); self.assertGreaterEqual(len(stored['password']),32)

    def test_kernel_supervisor_times_out_and_terminates_process(self):
        from proxy_manager.runtime.supervisor import KernelSupervisor
        process=Mock(pid=1234,returncode=None)
        process.poll.side_effect=[None,0]
        process.wait.return_value=0
        with tempfile.TemporaryDirectory() as directory:
            supervisor=KernelSupervisor(Path(directory),AsyncMock(return_value=False))
            with patch('proxy_manager.runtime.supervisor.subprocess.Popen',return_value=process), \
                 patch('proxy_manager.runtime.supervisor.asyncio.sleep',new=AsyncMock()):
                with self.assertRaisesRegex(RuntimeError,'健康检查超时'):
                    asyncio.run(supervisor.start(Path(directory)/'core',Path(directory)/'config.yaml',0))
            process.terminate.assert_called_once_with()
            self.assertFalse(supervisor.pid_path.exists())
            self.assertEqual(supervisor.status()['state'],'failed')

    def test_kernel_supervisor_restarts_after_crash(self):
        from proxy_manager.runtime.supervisor import KernelSupervisor
        with tempfile.TemporaryDirectory() as directory:
            supervisor=KernelSupervisor(Path(directory),AsyncMock(return_value=True))
            supervisor.process=Mock(returncode=17); supervisor.process.poll.return_value=17
            supervisor.binary=Path(directory)/'core'; supervisor.config=Path(directory)/'config.yaml'
            async def respawn(_timeout): supervisor.stopping=True
            supervisor._spawn=AsyncMock(side_effect=respawn)
            with patch('proxy_manager.runtime.supervisor.asyncio.sleep',new=AsyncMock()):
                asyncio.run(supervisor._monitor())
            self.assertEqual(supervisor.restarts,1)
            self.assertEqual(supervisor.last_error,'内核异常退出，代码 17')
            supervisor._spawn.assert_awaited_once_with(12)

    def test_kernel_supervisor_stop_cancels_monitor_and_removes_pid(self):
        from proxy_manager.runtime.supervisor import KernelSupervisor
        process=Mock(pid=1234,returncode=None); process.poll.return_value=None; process.wait.return_value=0
        with tempfile.TemporaryDirectory() as directory:
            supervisor=KernelSupervisor(Path(directory),AsyncMock(return_value=True)); supervisor.process=process
            supervisor.last_error='旧故障'
            supervisor.pid_path.write_text('{"pid":1234}',encoding='utf-8')
            result=asyncio.run(supervisor.stop())
            process.terminate.assert_called_once_with()
            self.assertFalse(supervisor.pid_path.exists())
            self.assertEqual(result['state'],'stopped')

    def test_normalized_nodes_record_executor_and_adapter_set(self):
        manager=self._manager_for_runtime()
        node=manager._normalize({'nodes':[{'id':'a','name':'AnyTLS','protocol':'anytls','endpoint':'anytls://secret@example.com:443'}]})['nodes'][0]
        self.assertEqual(node['executor'],'mihomo')
        self.assertEqual(node['adapters'],['mihomo','sing-box'])
        http_node=manager._normalize({'nodes':[{'id':'b','name':'HTTP','protocol':'http','endpoint':'http://proxy.example:8080'}]})['nodes'][0]
        self.assertEqual(http_node['executor'],'direct-http')

    def test_invalid_proxy_scheme_is_rejected(self):
        self.assertFalse(self.module.safe_url("file:///etc/passwd"))
        self.assertTrue(self.module.safe_url("socks5://mihomo:7891"))
        self.assertFalse(self.module.safe_url("http://user:password@proxy:8080"))

    def test_user_controlled_urls_reject_private_and_metadata_addresses(self):
        from proxy_manager.traffic.safe_http import validate_public_url
        for value in ('http://127.0.0.1/admin','http://10.0.0.1/','http://169.254.169.254/latest/meta-data/'):
            with self.assertRaisesRegex(ValueError,'禁止访问'):
                asyncio.run(validate_public_url(value))

    def test_public_fetch_revalidates_redirect_destination(self):
        from proxy_manager.traffic.safe_http import fetch_public_url
        request=self.module.httpx.Request('GET','https://1.1.1.1/sub')
        response=self.module.httpx.Response(302,headers={'location':'http://127.0.0.1/admin'},request=request)
        client=AsyncMock(); client.__aenter__.return_value=client; client.build_request=Mock(return_value=request)
        client.send=AsyncMock(return_value=response)
        with patch('proxy_manager.traffic.safe_http.httpx.AsyncClient',return_value=client):
            with self.assertRaisesRegex(ValueError,'禁止访问'):
                asyncio.run(fetch_public_url('https://1.1.1.1/sub'))
        client.send.assert_awaited_once()

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
        self.assertEqual(payload['control']['secret'], 'old-secret')
        self.assertEqual(payload['proxy_entry']['http_url'], 'http://old-proxy:7890')
        self.assertEqual(payload['proxy_entry']['socks_url'], 'socks5://old-proxy:7891')
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
            self.assertEqual(json.loads(manager.path.read_text())["version"], 6)
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
        manager.refresh_lock = asyncio.Lock(); manager.operation_lock = asyncio.Lock(); manager.previews = {}; manager.probe_tasks = {}
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
        self.assertFalse(document['allow-lan']); self.assertEqual(document['bind-address'],'127.0.0.1')
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
        proxy = manager._adapter().render_proxy(normalized)
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
        self.assertTrue(manager._adapter().render_proxy(nodes[1])['tls'])
        self.assertEqual(manager._adapter().render_proxy(nodes[2])['username'], 'bob')

    def test_unverified_protocols_are_preserved_with_reason(self):
        manager = self._manager_for_runtime()
        nodes, discovered = manager._parse_subscription('vless://uuid@example.com:443#VLESS\nunknownx://opaque#未知', 'sub-other')
        self.assertEqual(discovered, {'vless','unknownx'})
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]['support']['status'], 'unverified')
        self.assertEqual(nodes[1]['support']['status'], 'unsupported')
        self.assertTrue(nodes[1]['support']['reason'])
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
             patch('proxy_manager.plugin.validate_public_url', new=AsyncMock()), \
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

    def test_transport_path_and_sni_are_part_of_stable_identity(self):
        helper=self.module.ProxyManager._stable_node_id
        first=helper('sub-a','vless://uuid@example.com:443?type=ws&path=%2Fa&sni=one.example')
        second=helper('sub-a','vless://uuid@example.com:443?type=ws&path=%2Fb&sni=one.example')
        third=helper('sub-a','vless://uuid@example.com:443?type=ws&path=%2Fa&sni=two.example')
        self.assertEqual(len({first,second,third}),3)

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
                self.assertEqual(first.state['version'],6)
                self.assertEqual(first.state['groups'][1]['node_ids'],[new_id])
                self.assertEqual(first.state['groups'][1]['selected'],new_id)
                self.assertEqual(first.state['subscriptions'][0]['node_ids'],[new_id])
                self.assertIn(new_id,first.health); self.assertNotIn('old-id',first.health)
                backup=(root/'config.pre-v5.json').read_text(); self.assertEqual(json.loads(backup)['version'],2)
                self.assertEqual((root/'config.pre-v5.json').stat().st_mode & 0o777,0o600)
                second=self.module.ProxyManager(context,{})
                self.assertEqual(second.state['nodes'][0]['id'],new_id)
                self.assertEqual((root/'config.pre-v5.json').read_text(),backup)

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
        with patch('proxy_manager.plugin.fetch_public_url',new=AsyncMock(return_value=response)):
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

    def test_recovery_document_is_bound_to_recorded_adapter(self):
        from proxy_manager.cores.mihomo import MihomoAdapter
        from proxy_manager.cores.sing_box import SingBoxAdapter
        from proxy_manager.runtime.transaction import verified_recovery_document
        manager=self._manager_for_runtime(); document=MihomoAdapter().render(manager.state,manager._compiled_rules())
        application={'status':'applied','adapter':'mihomo','applied_revision':MihomoAdapter().revision(document),'document':document}
        self.assertIsNone(verified_recovery_document(application,SingBoxAdapter()))
        legacy=dict(application); legacy.pop('adapter')
        self.assertEqual(verified_recovery_document(legacy,MihomoAdapter()),document)

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

    def test_runtime_verification_normalizes_mihomo_rule_fields(self):
        manager=self._manager_for_runtime(); document=manager._runtime_document()
        proxies={proxy['name']:{'type':proxy['type']} for proxy in document['proxies']}
        type_names={'select':'Selector','url-test':'URLTest','fallback':'Fallback'}
        for group in document['proxy-groups']:
            proxies[group['name']]={'type':type_names[group['type']],
                                    'all':group['proxies'],'now':group['proxies'][0]}
        rules=[{'type':('DomainSuffix' if kind=='DOMAIN-SUFFIX' else kind.title()),
                'payload':' '+payload+' ','proxy':' '+target+' '}
               for kind,payload,target in manager._expected_rules(document)]
        self.assertEqual(manager._verify_runtime_data(document,{'mode':'rule'},proxies,rules),[])

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

    def test_probe_task_preflights_kernel_once_for_native_nodes(self):
        manager=self._manager_for_runtime()
        manager.probe_tasks={'task':{'id':'task','status':'running','total':2,'completed':0,'results':[],
                                     'cancelled':False,'started_at':1}}
        kernel={'state':'applied','message':'已应用'}; received=[]

        async def probe(payload, *, kernel_status=None):
            received.append((payload['node_id'],kernel_status))
            return {'node_id':payload['node_id'],'status':'ok','latency_ms':12}

        with patch.object(manager,'_probe_kernel_status',AsyncMock(return_value=kernel)) as preflight, \
             patch.object(manager,'_probe_one',side_effect=probe):
            asyncio.run(manager._run_probe_task('task',['hk-1','sg-1'],'https://example.com',5,2))
        preflight.assert_awaited_once_with()
        self.assertEqual({node_id for node_id,_ in received},{'hk-1','sg-1'})
        self.assertTrue(all(status is kernel for _,status in received))

    def test_probe_retries_transient_kernel_connection_failure(self):
        manager=self._manager_for_runtime(); adapter=manager._adapter()
        status=AsyncMock(side_effect=[{'state':'connection_failed','message':'暂时不可用'},
                                      {'state':'applied','message':'已应用'}])
        with patch.object(manager,'_kernel_status',status), \
             patch('proxy_manager.plugin.asyncio.sleep',new=AsyncMock()), \
             patch('proxy_manager.plugin.validate_public_url',new=AsyncMock()), \
             patch.object(adapter,'probe',AsyncMock(return_value=23)):
            result=asyncio.run(manager._probe_one({'node_id':'hk-1'}))
        self.assertEqual(result['status'],'ok'); self.assertEqual(status.await_count,2)

    def test_probe_task_keeps_unavailable_kernel_as_skipped(self):
        manager=self._manager_for_runtime(); adapter=manager._adapter()
        manager.probe_tasks={'task':{'id':'task','status':'running','total':2,'completed':0,'results':[],
                                     'cancelled':False,'started_at':1}}
        with patch.object(manager,'_probe_kernel_status',AsyncMock(return_value={
            'state':'saved','message':'配置尚未应用'})), patch.object(adapter,'probe',AsyncMock()) as probe:
            asyncio.run(manager._run_probe_task('task',['hk-1','sg-1'],'https://example.com',5,2))
        self.assertEqual([item['status'] for item in manager.probe_tasks['task']['results']],['skipped','skipped'])
        probe.assert_not_awaited()

    def test_socks_scheme_uses_direct_probe_without_kernel_preflight(self):
        manager=self._manager_for_runtime(); node=manager.state['nodes'][0]
        node.update({'protocol':'socks','endpoint':'socks://user:pass@example.com:1080',
                     'connection':{'uri':'socks://user:pass@example.com:1080'}})
        response=self.module.httpx.Response(204,request=self.module.httpx.Request('GET','https://example.com'))
        client=AsyncMock(); client.__aenter__.return_value=client; client.get=AsyncMock(return_value=response)
        with patch.object(manager,'_kernel_status',AsyncMock()) as kernel, \
             patch('proxy_manager.plugin.validate_public_url',new=AsyncMock()), \
             patch.object(self.module.httpx,'AsyncClient',return_value=client) as factory:
            result=asyncio.run(manager._probe_one({'node_id':'hk-1'}))
        self.assertEqual(result['status'],'ok'); kernel.assert_not_awaited()
        self.assertEqual(factory.call_args.kwargs['proxy'],'socks5://user:pass@example.com:1080')

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

    def test_dynamic_traffic_inventory_never_infers_takeover_from_configuration_alone(self):
        from proxy_manager.traffic.inventory import traffic_inventory
        manager=self._manager_for_runtime(); entry=manager.state['proxy_entry']['http_url']
        pending=traffic_inventory(manager.state,{}, {'http_proxy':entry,'https_proxy':entry})
        self.assertEqual(pending[0]['status'],'unknown')
        verified={'status':'applied','saved_revision':'r1','applied_revision':'r1',
                  'verification':{'verified':True,'runtime_revision':'r1','trace':{'request_correlated':True}}}
        verified['verification']['scope']='astrbot-core'
        managed=traffic_inventory(manager.state,verified, {'http_proxy':entry,'https_proxy':entry},
                                  {'effective':True,'configured':True})
        self.assertEqual(managed[0]['status'],'managed')
        self.assertTrue(all(item['status']=='not_connected' for item in managed[1:-1]))
        self.assertEqual(managed[-1]['status'],'managed')

    def test_traffic_audit_discovers_configuration_without_credentials(self):
        from proxy_manager.traffic.audit import AstrBotTrafficAudit
        from proxy_manager.traffic.inventory import traffic_inventory
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'data'/'cmd_config.json'; config.parent.mkdir()
            config.write_text(json.dumps({'provider_sources':[{'id':'safe-provider','type':'openai','proxy':'http://127.0.0.1:17890','key':'secret'}],
                                          'platform':[{'id':'safe-platform','type':'discord','discord_proxy':'http://other:7890','token':'secret'}],
                                          'plugin_set':{'third-party':True},'agent_runner':{'runner_type':'builtin'}}),encoding='utf-8')
            audit=AstrBotTrafficAudit(root).snapshot('http://127.0.0.1:17890')
            self.assertEqual(audit['providers'][0]['proxy'],'stable_entry')
            self.assertEqual(audit['platforms'][0]['proxy'],'other_proxy')
            self.assertNotIn('secret',json.dumps(audit))
            values=traffic_inventory({'proxy_entry':{'http_url':'http://127.0.0.1:17890'}},{},audit=audit)
            provider=next(value for value in values if value['id']=='provider-proxy')
            platform=next(value for value in values if value['id']=='platform-sdk')
            self.assertEqual(provider['status'],'unknown'); self.assertEqual(platform['status'],'not_connected')

    def test_mcp_audit_classifies_transports_without_leaking_credentials(self):
        from proxy_manager.traffic.audit import AstrBotTrafficAudit
        from proxy_manager.traffic.inventory import traffic_inventory
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); data=root/'data'; data.mkdir()
            (data/'cmd_config.json').write_text('{}')
            (data/'mcp_server.json').write_text(json.dumps({'mcpServers':{
                'stdio':{'command':'node','args':['server.js'],'env':{'HTTP_PROXY':'http://127.0.0.1:17890','TOKEN':'hidden'}},
                'host':{'url':'http://127.0.0.1:9000/mcp'},
                'container':{'url':'http://applovin:6186/mcp','env':{'HTTPS_PROXY':'http://user:password@astrbot:17891'}},
                'external':{'url':'https://mcp.example.com/sse','headers':{'Authorization':'Bearer hidden'}},
            }}))
            audit=AstrBotTrafficAudit(root).snapshot('http://127.0.0.1:17890',{'port':17891})
            self.assertEqual([item['locality'] for item in audit['mcps']],['stdio','same_host','container','external'])
            serialized=json.dumps(audit)
            self.assertNotIn('hidden',serialized); self.assertNotIn('password',serialized); self.assertNotIn('Authorization',serialized)
            mcp=next(item for item in traffic_inventory({'proxy_entry':{'http_url':'http://127.0.0.1:17890'}},{},audit=audit) if item['id']=='mcp-egress')
            self.assertEqual(mcp['status'],'unknown')

    def test_audit_does_not_label_environment_inheritance_as_connected(self):
        from proxy_manager.traffic.inventory import traffic_inventory
        audit={'providers':[{'enabled':True,'proxy':'unset'}],'platforms':[{'enabled':True,'proxy':'unset'}]}
        values=traffic_inventory({'proxy_entry':{'http_url':'http://127.0.0.1:17890'}},{},
                                 astrbot={'effective':True},audit=audit)
        self.assertEqual(next(value for value in values if value['id']=='provider-proxy')['status'],'unknown')
        self.assertEqual(next(value for value in values if value['id']=='platform-sdk')['status'],'unknown')

    def test_integration_declaration_is_strict_and_does_not_expose_secrets(self):
        from proxy_manager.traffic.integration import PROTOCOL, declaration
        valid=declaration({'protocol':PROTOCOL,'mode':'astrbot-environment',
                           'protocols':['HTTPS','websocket'],'restart':'process','auto_apply':True})
        self.assertEqual(valid,{'state':'compatible','mode':'astrbot-environment',
                                'protocols':['https','websocket'],'restart':'process','auto_apply':True})
        self.assertEqual(declaration(None)['state'],'missing')
        for value in (
            {'protocol':'astrbot.proxy-manager/v2','mode':'astrbot-environment','protocols':['https']},
            {'protocol':PROTOCOL,'mode':'public-network','protocols':['https']},
            {'protocol':PROTOCOL,'mode':'manual','protocols':['icmp']},
            {'protocol':PROTOCOL,'mode':'manual','protocols':['https'],'token':'must-not-leak'},
        ):
            result=declaration(value)
            self.assertEqual(result['state'],'invalid')
            self.assertNotIn('must-not-leak',json.dumps(result))

    def test_integration_audit_discovers_new_plugin_and_mcp_declarations(self):
        from proxy_manager.traffic.audit import AstrBotTrafficAudit
        from proxy_manager.traffic.integration import PROTOCOL
        from proxy_manager.traffic.inventory import traffic_inventory
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); data=root/'data'; plugin=data/'plugins'/'declared-plugin'; plugin.mkdir(parents=True)
            config={'plugin_set':{'configured-without-file':True}}
            (data/'cmd_config.json').write_text(json.dumps(config),encoding='utf-8')
            (plugin/'proxy_manager_integration.json').write_text(json.dumps({
                'protocol':PROTOCOL,'mode':'astrbot-environment','protocols':['http','https'],
                'restart':'process'}),encoding='utf-8')
            (data/'mcp_server.json').write_text(json.dumps({'mcpServers':{
                'declared-mcp':{'command':'node','proxy_manager':{
                    'protocol':PROTOCOL,'mode':'private-network','protocols':['https'],'restart':'container'}},
            }}),encoding='utf-8')
            audit=AstrBotTrafficAudit(root).snapshot('http://127.0.0.1:17890',{'port':17891})
            plugins={item['name']:item for item in audit['plugin_integrations']}
            self.assertEqual(plugins['declared-plugin']['declaration']['state'],'compatible')
            self.assertEqual(plugins['configured-without-file']['declaration']['state'],'missing')
            self.assertEqual(audit['mcps'][0]['declaration']['state'],'compatible')
            values=traffic_inventory({'proxy_entry':{'http_url':'http://127.0.0.1:17890'}},{},
                                     astrbot={'configured':True,'effective':True},audit=audit)
            plugin_item=next(item for item in values if item['id']=='plugin-http')
            mcp_item=next(item for item in values if item['id']=='mcp-egress')
            self.assertEqual((plugin_item['integration']['state'],plugin_item['status']),('managed','unknown'))
            self.assertEqual((mcp_item['integration']['state'],mcp_item['status']),('declared','not_connected'))

    def test_astrbot_proxy_transaction_backs_up_narrows_and_restores(self):
        from proxy_manager.traffic.astrbot import AstrBotProxyTransaction, INTERNAL_NO_PROXY
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'cmd_config.json'; data=root/'plugin'; data.mkdir()
            mcp=root/'data'/'mcp_server.json'; mcp.parent.mkdir()
            mcp.write_text(json.dumps({'mcpServers':{
                'internal':{'url':'http://custom-mcp:6186/mcp'},
                'private':{'url':'http://10.0.0.8/mcp'},
                'external':{'url':'https://api.example.com/mcp'},
            }}),encoding='utf-8')
            original={'http_proxy':'http://legacy:7890','https_proxy':'http://legacy:7891',
                      'all_proxy':'socks5://legacy:7892','no_proxy':['localhost','10.*','.feishu.cn'],'other':True}
            config.write_text(json.dumps(original),encoding='utf-8'); config.chmod(0o640)
            with patch.dict(os.environ,{'ASTRBOT_ROOT':str(root)},clear=False): transaction=AstrBotProxyTransaction(data,config)
            pending=transaction.enable('http://127.0.0.1:17890','socks5://127.0.0.1:17890')
            current=json.loads(config.read_text())
            self.assertEqual(current['http_proxy'],'http://127.0.0.1:17890')
            self.assertEqual(current['https_proxy'],'http://127.0.0.1:17890')
            self.assertEqual(current['all_proxy'],'socks5://127.0.0.1:17890')
            self.assertEqual(tuple(current['no_proxy']),INTERNAL_NO_PROXY+('10.0.0.8','custom-mcp'))
            self.assertNotIn('api.example.com',current['no_proxy'])
            self.assertTrue(current['other']); self.assertEqual(pending['status'],'pending_restart')
            self.assertEqual(config.stat().st_mode & 0o777,0o640)
            restored=transaction.restore('http://127.0.0.1:17890','socks5://127.0.0.1:17890')
            self.assertEqual(json.loads(config.read_text()),original)
            self.assertEqual(restored['status'],'restore_pending_restart')
            self.assertEqual(transaction.path.stat().st_mode & 0o777,0o600)

    def test_astrbot_proxy_transaction_marks_restart_effective(self):
        from proxy_manager.traffic.astrbot import AstrBotProxyTransaction
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'cmd_config.json'; data=root/'plugin'; data.mkdir()
            config.write_text('{}',encoding='utf-8')
            transaction=AstrBotProxyTransaction(data,config); entry='http://127.0.0.1:17890'; socks='socks5://127.0.0.1:17890'
            transaction.enable(entry,socks)
            active=transaction.mark_started(entry,socks)
            self.assertFalse(active['effective'])
            with patch.dict(os.environ,{'http_proxy':entry,'https_proxy':entry,'all_proxy':socks},clear=False):
                active=transaction.mark_started(entry,socks)
            self.assertEqual(active['status'],'active'); self.assertTrue(active['effective'])

    def test_astrbot_proxy_transaction_applies_complete_process_environment(self):
        from proxy_manager.traffic.astrbot import AstrBotProxyTransaction, INTERNAL_NO_PROXY
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'cmd_config.json'; data=root/'plugin'; data.mkdir()
            config.write_text('{}',encoding='utf-8')
            entry='http://127.0.0.1:17890'; socks='socks5://127.0.0.1:17890'
            transaction=AstrBotProxyTransaction(data,config); transaction.enable(entry,socks)
            with patch.dict(os.environ,{},clear=True):
                transaction.apply_process_environment(entry,socks)
                active=transaction.mark_started(entry,socks)
                self.assertEqual(os.environ['http_proxy'],entry)
                self.assertEqual(os.environ['https_proxy'],entry)
                self.assertEqual(os.environ['all_proxy'],socks)
                self.assertEqual(os.environ['no_proxy'],','.join(INTERNAL_NO_PROXY))
            self.assertEqual(active['status'],'active'); self.assertTrue(active['effective'])

    def test_astrbot_proxy_transaction_ensure_repairs_incomplete_configuration(self):
        from proxy_manager.traffic.astrbot import AstrBotProxyTransaction, INTERNAL_NO_PROXY
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'cmd_config.json'; data=root/'plugin'; data.mkdir()
            entry='http://127.0.0.1:17890'; socks='socks5://127.0.0.1:17890'
            config.write_text(json.dumps({'http_proxy':entry,'https_proxy':entry,'no_proxy':list(INTERNAL_NO_PROXY)}),encoding='utf-8')
            transaction=AstrBotProxyTransaction(data,config)
            status=transaction.ensure(entry,socks)
            self.assertEqual(status['status'],'pending_restart')
            self.assertEqual(json.loads(config.read_text())['all_proxy'],socks)
            with patch.object(transaction,'enable',side_effect=AssertionError('already managed configuration must not be rewritten')):
                transaction.ensure(entry,socks)

    def test_astrbot_core_verification_uses_process_proxy_and_fails_closed(self):
        manager=self._manager_for_runtime(); entry=manager.state['proxy_entry']['http_url']
        manager.astrbot_proxy=types.SimpleNamespace(status=lambda *_args:{'effective':True})
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'url':'https://api.ipify.org?format=json'}))
        client=AsyncMock(); client.__aenter__.return_value=client
        client.get=AsyncMock(side_effect=self.module.httpx.ConnectError('kernel stopped'))
        with patch('proxy_manager.plugin.request',fake_request), patch('proxy_manager.plugin.validate_public_url',new=AsyncMock()), \
             patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'applied'})), \
             patch.object(manager._adapter(),'connection_snapshot',AsyncMock(return_value=[])), \
             patch.object(self.module.httpx,'AsyncClient',return_value=client) as factory:
            result=asyncio.run(manager.verify_astrbot_egress())
        self.assertFalse(result['verified']); self.assertEqual(result['scope'],'astrbot-core')
        self.assertIn('未回落到直连',result['entry']['message'])
        self.assertNotIn('proxy',factory.call_args.kwargs); self.assertTrue(factory.call_args.kwargs['trust_env'])

    def test_outbound_verification_samples_connection_while_request_is_active(self):
        manager=self._manager_for_runtime(); manager.state['rule_groups']=[
            {'id':'ip','name':'IP','domains':[{'host':'api.ipify.org','match':'exact'}],'priority':1,'target':'hk','enabled':True}]
        response=self.module.httpx.Response(200,json={'ip':'203.0.113.9'},request=self.module.httpx.Request('GET','https://api.ipify.org/?format=json'))
        entry=AsyncMock(); entry.__aenter__.return_value=entry
        request_active=False
        async def get_entry(*_args,**_kwargs):
            nonlocal request_active
            request_active=True
            await asyncio.sleep(.03)
            request_active=False
            return response
        entry.get=AsyncMock(side_effect=get_entry)
        control=AsyncMock(); control.__aenter__.return_value=control
        control.get=AsyncMock(return_value=self.module.httpx.Response(200,json={'proxies':{'group-hk':{'now':'node-hk-1'}}},request=self.module.httpx.Request('GET','http://mihomo/proxies')))
        trace={'id':'active','host':'api.ipify.org','rule':'DOMAIN','rule_payload':'api.ipify.org','chains':['node-hk-1','group-hk']}
        snapshots=0
        async def snapshot(*_args):
            nonlocal snapshots
            snapshots+=1
            return [trace] if snapshots>1 and request_active else []
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'url':'https://api.ipify.org?format=json'}))
        adapter=manager._adapter()
        with patch('proxy_manager.plugin.request',fake_request), patch('proxy_manager.plugin.validate_public_url',new=AsyncMock()), \
             patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'applied'})), \
             patch.object(adapter,'connection_snapshot',side_effect=snapshot), \
             patch.object(self.module.httpx,'AsyncClient',side_effect=[entry,control]):
            result=asyncio.run(manager.verify_outbound())
        self.assertTrue(result['verified']); self.assertTrue(result['trace']['request_correlated'])

    def test_astrbot_core_verification_attempts_request_when_kernel_is_stopped(self):
        manager=self._manager_for_runtime(); manager.astrbot_proxy=types.SimpleNamespace(status=lambda *_args:{'effective':True})
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'url':'https://api.ipify.org?format=json'}))
        client=AsyncMock(); client.__aenter__.return_value=client
        client.get=AsyncMock(side_effect=self.module.httpx.ConnectError('kernel stopped'))
        adapter=manager._adapter()
        with patch('proxy_manager.plugin.request',fake_request), patch('proxy_manager.plugin.validate_public_url',new=AsyncMock()), \
             patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'stopped','message':'内核未运行'})), \
             patch.object(adapter,'connection_snapshot',AsyncMock()) as snapshot, \
             patch.object(self.module.httpx,'AsyncClient',return_value=client):
            result=asyncio.run(manager.verify_astrbot_egress())
        client.get.assert_awaited_once(); snapshot.assert_not_awaited()
        self.assertFalse(result['verified']); self.assertEqual(result['entry']['state'],'failed')
        self.assertEqual(result['rule']['state'],'unconfirmed')

    def test_mihomo_connection_snapshot_accepts_null_connections(self):
        manager=self._manager_for_runtime(); adapter=manager._adapter()
        response=self.module.httpx.Response(200,json={'connections':None},request=self.module.httpx.Request('GET','http://mihomo/connections'))
        client=AsyncMock(); client.__aenter__.return_value=client; client.get=AsyncMock(return_value=response)
        with patch.object(self.module.httpx,'AsyncClient',return_value=client):
            self.assertEqual(asyncio.run(adapter.connection_snapshot(manager.state,'api.ipify.org')),[])

    def test_runtime_application_uses_verified_backup_when_primary_is_incomplete(self):
        manager=self._manager_for_runtime(); document=manager._runtime_document(); revision=manager._runtime_revision(document)
        manager.runtime_path.write_text('{broken',encoding='utf-8')
        manager.runtime_backup.write_text(json.dumps({'status':'applied','applied_revision':revision,'document':document}),encoding='utf-8')
        recovered=manager._load_runtime_application()
        self.assertEqual(recovered['applied_revision'],revision); self.assertIn('已恢复',recovered['message'])

    def test_runtime_record_cleanup_removes_expired_tasks_and_stale_health(self):
        manager=self._manager_for_runtime(); manager.health={'hk-1':{'status':'ok'},'removed':{'status':'error'}}
        manager.probe_tasks={'old':{'status':'completed','finished_at':1},'live':{'status':'running','started_at':1}}
        manager.previews={'old':{'at':1},'new':{'at':9500}}
        manager._cleanup_runtime_records(10000)
        self.assertEqual(set(manager.health),{'hk-1'}); self.assertEqual(set(manager.probe_tasks),{'live'}); self.assertEqual(set(manager.previews),{'new'})

    def test_event_log_rotates_before_unbounded_growth(self):
        manager=self._manager_for_runtime(); manager.events_path.write_bytes(b'x'*(1024*1024+1))
        manager.event({'action':'rotation','result':'ok'})
        self.assertTrue(manager.events_path.with_suffix('.jsonl.1').exists())
        self.assertLess(manager.events_path.stat().st_size,1024)

    def test_outbound_verification_keeps_entry_rule_and_exit_evidence_separate(self):
        manager=self._manager_for_runtime(); manager.state['rule_groups']=[
            {'id':'ip','name':'IP','domains':[{'host':'api.ipify.org','match':'exact'}],'priority':1,'target':'hk','enabled':True}]
        response=self.module.httpx.Response(200,json={'ip':'203.0.113.9'},request=self.module.httpx.Request('GET','https://api.ipify.org/?format=json'))
        proxies=self.module.httpx.Response(200,json={'proxies':{'group-hk':{'now':'node-hk-1'}}},request=self.module.httpx.Request('GET','http://mihomo:9090/proxies'))
        entry=AsyncMock(); entry.__aenter__.return_value=entry; entry.get=AsyncMock(return_value=response)
        control=AsyncMock(); control.__aenter__.return_value=control; control.get=AsyncMock(return_value=proxies)
        fake_request=types.SimpleNamespace(json=AsyncMock(return_value={'url':'https://api.ipify.org?format=json'}))
        adapter=manager._adapter(); trace={'id':'new','host':'api.ipify.org','rule':'DOMAIN','rule_payload':'api.ipify.org','chains':['node-hk-1','group-hk']}
        with patch('proxy_manager.plugin.request',fake_request), patch('proxy_manager.plugin.validate_public_url',new=AsyncMock()), \
             patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'applied'})), \
             patch.object(adapter,'connection_snapshot',AsyncMock(side_effect=[[],[trace]])), \
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
        adapter=manager._adapter()
        with patch('proxy_manager.plugin.request',fake_request), patch('proxy_manager.plugin.validate_public_url',new=AsyncMock()), \
             patch.object(manager,'_kernel_status',AsyncMock(return_value={'state':'applied'})), \
             patch.object(adapter,'connection_snapshot',AsyncMock(return_value=[])), \
             patch.object(self.module.httpx,'AsyncClient',return_value=entry), patch('proxy_manager.plugin.asyncio.sleep',new=AsyncMock()):
            result=asyncio.run(manager.verify_outbound())
        self.assertFalse(result['verified']); self.assertEqual(result['entry']['state'],'passed')
        self.assertEqual(result['rule']['state'],'unconfirmed'); self.assertEqual(result['exit']['state'],'unconfirmed')

    def test_mihomo_and_sing_box_share_complete_adapter_contract(self):
        from proxy_manager.cores.registry import all_adapters
        manager=self._manager_for_runtime()
        for adapter_id,adapter in all_adapters().items():
            with self.subTest(adapter=adapter_id):
                state=copy.deepcopy(manager.state); state['control']['adapter']=adapter_id
                state['proxy_entry']['private']={'enabled':True,'listen':'0.0.0.0','port':17891,
                                                  'username':'private-user','password':'private-password'}
                if adapter_id=='xray':
                    state['nodes']=[{'id':'xray-node','name':'Xray VLESS','display_name':'Xray VLESS','protocol':'vless',
                                     'endpoint':'vless://123e4567-e89b-12d3-a456-426614174000@example.com:443?security=tls&sni=example.com',
                                     'connection':{'uri':'vless://123e4567-e89b-12d3-a456-426614174000@example.com:443?security=tls&sni=example.com'},
                                     'kernel_name':'node-xray-node','adapters':['xray'],'support':{'status':'supported'},
                                     'enabled':True,'excluded':False,'invalid_reference':False}]
                    state['groups']=[{'id':'direct','name':'直连','kernel_name':'DIRECT','mode':'direct','node_ids':[],'selected':'','enabled':True},
                                     {'id':'xray','name':'Xray','kernel_name':'group-xray','mode':'select','node_ids':['xray-node'],'selected':'xray-node','enabled':True}]
                else:
                    for node in state['nodes']: node['adapters']=['mihomo','sing-box']
                document=adapter.render(state,manager._compiled_rules())
                adapter.validate(document)
                serialized=adapter.serialize(document)
                self.assertIsInstance(serialized,bytes); self.assertTrue(serialized)
                self.assertIn(adapter.config_filename(),{'config.yaml','config.json','xray-config.json'})
                command=adapter.command(Path('/core'),Path('/runtime')/adapter.config_filename())
                self.assertEqual(command[0],'/core'); self.assertIn(str(Path('/runtime')/adapter.config_filename()),command)
                manifest=adapter.artifact()
                self.assertEqual(manifest['adapter'],adapter_id); self.assertTrue(manifest['version'])
                self.assertTrue(manifest['artifacts']); self.assertTrue(adapter.revision(document))
                redacted=json.dumps(adapter.redact(document),ensure_ascii=False)
                self.assertNotIn('"secret": "secret"',redacted); self.assertNotIn('"password": "credential"',redacted)
                closed=adapter.fail_closed_document(state['control'],state['proxy_entry'])
                adapter.validate(closed); self.assertNotEqual(adapter.revision(document),adapter.revision(closed))
                serialized_closed=json.dumps(closed)
                self.assertIn('private-proxy-entry',serialized_closed); self.assertIn('private-password',serialized_closed)
                public=adapter.public_entry(state['proxy_entry'])
                self.assertNotIn('private-user',json.dumps(public)); self.assertNotIn('private-password',json.dumps(public))
                for method in ('start','stop','restart','apply','inspect','verify','probe','redact','healthy'):
                    self.assertTrue(callable(getattr(adapter,method)))

    def test_unknown_adapter_is_preserved_and_fails_closed(self):
        from proxy_manager.cores.base import UnsupportedAdapter
        from proxy_manager.cores.registry import current_adapter
        state=self._manager_for_runtime().state; state['control']['adapter']='future-core'
        adapter=current_adapter(state)
        self.assertIsInstance(adapter,UnsupportedAdapter); self.assertEqual(adapter.id,'future-core')
        with self.assertRaisesRegex(ValueError,'不支持或未知'):
            adapter.render(state,[])
        normalized=self._manager_for_runtime()._normalize(state)
        self.assertEqual(normalized['control']['adapter'],'future-core')

    def test_sing_box_official_manifest_and_native_json_shape(self):
        from proxy_manager.cores.sing_box import SingBoxAdapter
        adapter=SingBoxAdapter(); manifest=adapter.artifact()
        self.assertEqual(manifest['version'],'1.14.1')
        self.assertTrue(all(len(item['sha256'])==64 for item in manifest['artifacts'].values()))
        manager=self._manager_for_runtime(); manager.state['control']['adapter']='sing-box'
        for node in manager.state['nodes']: node['adapters']=['mihomo','sing-box']
        document=adapter.render(manager.state,manager._compiled_rules())
        self.assertEqual(document['inbounds'][0]['type'],'mixed')
        self.assertIn('clash_api',document['experimental'])
        self.assertTrue(any(item['type']=='anytls' for item in document['outbounds']))


if __name__ == "__main__":
    unittest.main()
