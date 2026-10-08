from __future__ import annotations

import importlib
import ipaddress
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .integration import declaration, plugin_declarations


def runtime_label() -> str:
    try:
        module = importlib.import_module('astrbot')
        version = str(getattr(module, '__version__', '') or '未知版本')
    except ImportError:
        version = '未知版本'
    return 'AstrBot ' + version


class AstrBotTrafficAudit:
    """Read current AstrBot configuration without exposing credentials or changing it."""

    def __init__(self, root: Path):
        self.root=root
        self.config_path=root/'data'/'cmd_config.json'
        self.mcp_path=root/'data'/'mcp_server.json'

    def _config(self) -> dict:
        try:
            value=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):
            return {}

    @staticmethod
    def _items(value: object) -> list[dict]:
        if isinstance(value,list): return [item for item in value if isinstance(item,dict)]
        if isinstance(value,dict): return [item for item in value.values() if isinstance(item,dict)]
        return []

    @staticmethod
    def _name(item: dict, fallback: str) -> str:
        return str(item.get('id') or item.get('name') or item.get('provider') or item.get('type') or fallback)[:80]

    @staticmethod
    def _proxy_state(item: dict, entry: str) -> str:
        value=str(item.get('proxy') or item.get('discord_proxy') or '').rstrip('/')
        return 'stable_entry' if value and value==entry.rstrip('/') else ('other_proxy' if value else 'unset')

    @staticmethod
    def _mcp_locality(item: dict) -> tuple[str,str]:
        if item.get('command'): return 'stdio','AstrBot 子进程'
        raw=str(item.get('url') or item.get('server_url') or '')
        try: host=(urlsplit(raw).hostname or '').lower()
        except ValueError: host=''
        if host in {'localhost','127.0.0.1','::1'}: return 'same_host','同机独立进程'
        try:
            address=ipaddress.ip_address(host)
            if address.is_private: return 'container','私有网络服务'
        except ValueError: pass
        if host and '.' not in host: return 'container','独立容器'
        return 'external','外部 MCP 服务'

    @staticmethod
    def _mcp_proxy_matches(proxy: str, entry: str, locality: str, private: dict) -> bool:
        try:
            parsed = urlsplit(proxy)
            if locality == 'stdio':
                return bool(entry and proxy.rstrip('/') == entry.rstrip('/'))
            if locality != 'container' or not private.get('enabled'):
                return False
            return bool(
                parsed.scheme == 'http'
                and parsed.hostname == private.get('service_host', 'astrbot')
                and parsed.port == int(private.get('port', 17891))
                and private.get('username') and private.get('password')
                and unquote(parsed.username or '') == private['username']
                and unquote(parsed.password or '') == private['password']
                and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment
            )
        except (TypeError, ValueError):
            return False

    def _mcps(self, entry: str, private: dict|None, components: list[dict]|None=None) -> list[dict]:
        try: value=json.loads(self.mcp_path.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError): return []
        servers=value.get('mcpServers',{}) if isinstance(value,dict) else {}
        iterable=servers.items() if isinstance(servers,dict) else enumerate(servers if isinstance(servers,list) else [])
        results=[]
        for key,item in iterable:
            if not isinstance(item,dict): continue
            policy=next((route for route in components or [] if route['id']=='mcp-'+str(key)), None)
            expected_entry='http://127.0.0.1:'+str(policy['port']) if policy else entry
            expected_private={**(private or {}), 'port':policy['port']} if policy else private
            locality,label=self._mcp_locality(item)
            declared=declaration(item.get('proxy_manager'))
            env=item.get('env') if isinstance(item.get('env'),dict) else {}
            proxies=[str(env[key]) for key in ('HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy') if env.get(key)]
            connected=bool(proxies) and all(self._mcp_proxy_matches(proxy,expected_entry,locality,expected_private or {}) for proxy in proxies)
            # One scheme or an external no_proxy value cannot prove complete HTTP egress.
            connected=connected and all(env.get(key) or env.get(key.upper()) for key in ('http_proxy','https_proxy'))
            bypass=str(env.get('no_proxy') or env.get('NO_PROXY') or '')
            if any(host.strip() not in {'localhost','127.0.0.1','::1'} for host in bypass.split(',') if host.strip()):
                connected=False
            expected_mode='astrbot-environment' if locality=='stdio' else 'private-network'
            protocol_connected=connected and declared.get('state')=='compatible' and declared.get('mode')==expected_mode
            try:
                transport=str(item.get('transport') or urlsplit(str(item.get('url') or '')).scheme or 'unknown')[:24]
            except ValueError:
                transport='unknown'
            results.append({'id':'mcp-'+str(key),'name':str(item.get('name') or key)[:80], 'kind':'mcp',
                            'transport':'stdio' if item.get('command') else transport,
                            'locality':locality,'locality_label':label,
                            'proxy':'configured' if connected else ('other_proxy' if proxies else 'unset'),
                            'protocol_connected':bool(protocol_connected),
                            'declaration':declared,
                            'restart':'重启 MCP 进程' if locality in {'stdio','same_host'} else ('重建或重启容器' if locality=='container' else '由外部服务管理')})
        return results

    def snapshot(self, entry: str, private: dict|None=None, components: list[dict]|None=None) -> dict:
        config=self._config()
        providers=[]
        for source in ('provider_sources','provider','provider_tts_settings','provider_stt_settings','provider_embedding_settings'):
            for index,item in enumerate(self._items(config.get(source))):
                kind='model' if source=='provider' and item.get('provider_source_id') and not item.get('provider_type') else 'provider'
                providers.append({'id':source+'-'+str(index),'name':self._name(item,source+' '+str(index+1)),
                                  'type':str(item.get('type') or item.get('provider_type') or item.get('provider') or 'unknown')[:80],
                                  'kind':kind,'enabled':bool(item.get('enable',True)),'proxy':self._proxy_state(item,entry)})
        platforms=[]
        for index,item in enumerate(self._items(config.get('platform'))):
            platforms.append({'id':str(item.get('id') or 'platform-'+str(index+1))[:80],
                              'name':self._name(item,'平台 '+str(index+1)),
                              'type':str(item.get('type') or 'unknown')[:80],
                              'enabled':bool(item.get('enable',True)),'proxy':self._proxy_state(item,entry)})
        plugins=config.get('plugin_set')
        plugin_count=len(plugins) if isinstance(plugins,(dict,list)) else 0
        runner=config.get('agent_runner') if isinstance(config.get('agent_runner'),dict) else {}
        plugins=config.get('plugin_set')
        return {'version':runtime_label(),'readable':self.config_path.is_file(),'providers':providers,
                'platforms':platforms,'plugin_count':plugin_count,
                'mcps':self._mcps(entry,private,components),
                'plugin_integrations':plugin_declarations(self.root,plugins),
                'agent_runner':str(runner.get('runner_type') or 'unknown')[:80],
                'computer_runtime':str((config.get('provider_settings') or {}).get('computer_use_runtime') or 'unknown')[:80]}
