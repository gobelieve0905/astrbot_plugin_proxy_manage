from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from ..domain.model import compiled_rules
from .base import CoreAdapter


class XrayAdapter(CoreAdapter):
    """Xray-core adapter.

    Xray exposes a gRPC control API rather than the HTTP control surface used by
    the other adapters.  The adapter therefore owns configuration generation and
    process health, while group selection and runtime rule inspection are
    reported as limited capabilities instead of being guessed from a foreign API.
    """

    id = 'xray'

    def capabilities(self) -> dict:
        return {
            'id': self.id,
            'protocols': {'http', 'https', 'socks', 'socks5', 'socks5h', 'vmess', 'vless', 'trojan', 'ss'},
            'groups': {'select'},
            'rules': {'exact', 'suffix'},
            'probe': False,
            'hot_reload': False,
            'inspect': False,
            'control': 'process-only',
            'platforms': ['linux', 'darwin', 'windows'],
        }

    def artifact(self) -> dict:
        return json.loads(Path(__file__).with_name('xray_artifacts.json').read_text(encoding='utf-8'))

    def config_filename(self) -> str:
        return 'xray-config.json'

    def serialize(self, document: dict) -> bytes:
        return (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode()

    def command(self, binary: Path, config: Path) -> list[str]:
        return [str(binary), 'run', '-c', str(config)]

    @staticmethod
    def _query(parsed) -> dict[str, str]:
        return {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}

    @staticmethod
    def _stream_settings(query: dict[str, str]) -> dict:
        network = query.get('type', query.get('net', 'tcp')).lower()
        settings: dict = {'network': network}
        if network in {'ws', 'websocket'}:
            settings['network'] = 'ws'
            ws = {'path': query.get('path', '/') or '/'}
            host = query.get('host')
            if host:
                ws['headers'] = {'Host': host}
            settings['wsSettings'] = ws
        elif network in {'grpc', 'gun'}:
            settings['network'] = 'grpc'
            settings['grpcSettings'] = {'serviceName': query.get('serviceName', query.get('service-name', ''))}
        elif network in {'http', 'h2'}:
            settings['network'] = 'h2'
            settings['httpSettings'] = {'path': query.get('path', '/') or '/'}
            host = query.get('host')
            if host:
                settings['httpSettings']['host'] = [host]
        elif network in {'xhttp', 'splithttp'}:
            settings['network'] = 'splithttp'
            settings['splithttpSettings'] = {'path': query.get('path', '/') or '/'}
        if query.get('security') in {'tls', 'reality'} or query.get('tls') in {'1', 'true', 'tls'}:
            tls: dict = {'serverName': query.get('sni', query.get('servername', ''))}
            alpn = query.get('alpn')
            if alpn:
                tls['alpn'] = [item for item in alpn.split(',') if item]
            if query.get('allowInsecure', query.get('insecure', '')).lower() in {'1', 'true', 'yes'}:
                tls['allowInsecure'] = True
            settings['security'] = 'tls'
            settings['tlsSettings'] = tls
        return settings

    @classmethod
    def _outbound(cls, node: dict) -> dict:
        protocol = str(node.get('protocol', '')).lower()
        if protocol == 'socks':
            protocol = 'socks5'
        connection = node.get('connection') if isinstance(node.get('connection'), dict) else {}
        parsed = urlsplit(str(connection.get('uri') or node.get('endpoint') or ''))
        query = cls._query(parsed)
        server = str(connection.get('server') or parsed.hostname or '')
        port = connection.get('port') or parsed.port
        tag = str(node.get('kernel_name') or 'node-' + str(node.get('id', '')))
        username = str(connection.get('username') or unquote(parsed.username or ''))
        password = str(connection.get('password') or unquote(parsed.password or ''))
        if protocol in {'http', 'https'}:
            server_item = {'address': server, 'port': int(port)}
            if username:
                server_item['users'] = [{'user': username, 'pass': password}]
            return {'tag': tag, 'protocol': 'http', 'settings': {'servers': [server_item]}}
        if protocol in {'socks5', 'socks5h'}:
            server_item = {'address': server, 'port': int(port)}
            if username:
                server_item['users'] = [{'user': username, 'pass': password}]
            return {'tag': tag, 'protocol': 'socks', 'settings': {'servers': [server_item]}}
        if protocol == 'vmess':
            try:
                import base64
                raw = base64.b64decode(str(node.get('endpoint', '')).split('://', 1)[1] + '===').decode()
                payload = json.loads(raw)
            except (ValueError, UnicodeError, json.JSONDecodeError, IndexError) as exc:
                raise ValueError('VMess 节点参数无效：' + str(node.get('id', ''))) from exc
            server = str(payload.get('add') or payload.get('address') or server)
            port = int(payload.get('port') or port or 0)
            if not server or not port:
                raise ValueError('VMess 节点缺少服务器或端口：' + str(node.get('id', '')))
            user = {'id': str(payload.get('id') or payload.get('uuid') or ''),
                    'alterId': int(payload.get('aid') or payload.get('alterId') or 0),
                    'security': str(payload.get('scy') or payload.get('security') or 'auto')}
            if not user['id']:
                raise ValueError('VMess 节点缺少 UUID：' + str(node.get('id', '')))
            stream_query = {str(key): str(value) for key, value in payload.items() if value is not None}
            stream_query.update(query)
            return {'tag': tag, 'protocol': 'vmess', 'settings': {'vnext': [{'address': server, 'port': int(port), 'users': [user]}]},
                    'streamSettings': cls._stream_settings(stream_query)}
        if not server or not port:
            raise ValueError('节点连接参数缺少服务器或端口：' + str(node.get('id', '')))
        if protocol == 'vless':
            user_id = str(connection.get('uuid') or connection.get('id') or username)
            if not user_id:
                user_id = str(parsed.username or '')
            if not user_id:
                raise ValueError('VLESS 节点缺少 UUID：' + str(node.get('id', '')))
            user = {'id': user_id, 'encryption': query.get('encryption', 'none')}
            if query.get('flow'):
                user['flow'] = query['flow']
            return {'tag': tag, 'protocol': 'vless', 'settings': {'vnext': [{'address': server, 'port': int(port), 'users': [user]}]},
                    'streamSettings': cls._stream_settings(query)}
        if protocol == 'trojan':
            credential = password or username
            if not credential:
                raise ValueError('Trojan 节点缺少密码：' + str(node.get('id', '')))
            return {'tag': tag, 'protocol': 'trojan', 'settings': {'servers': [{'address': server, 'port': int(port), 'password': credential}]},
                    'streamSettings': cls._stream_settings(query)}
        if protocol == 'ss':
            method = str(connection.get('method') or query.get('method') or '')
            credential = password or str(connection.get('password') or '')
            if not method or not credential:
                # SIP002 URLs put method:password in the userinfo portion.
                try:
                    decoded = unquote(parsed.username or '')
                    method, credential = decoded.split(':', 1)
                except (ValueError, AttributeError) as exc:
                    raise ValueError('Shadowsocks 节点缺少加密方式或密码：' + str(node.get('id', ''))) from exc
            return {'tag': tag, 'protocol': 'shadowsocks', 'settings': {'servers': [{'address': server, 'port': int(port),
                                                                                       'method': method, 'password': credential}]}}
        raise ValueError('Xray 不支持节点协议：' + protocol)

    @staticmethod
    def _private_user(entry: dict) -> list[dict]:
        private = entry.get('private') if isinstance(entry.get('private'), dict) else {}
        if not private.get('enabled'):
            return []
        username = str(private.get('username', ''))
        password = str(private.get('password', ''))
        if not username or not password:
            raise ValueError('私有网络入口缺少认证凭据')
        return [{'user': username, 'pass': password}]

    def render(self, state: dict, compiled: list[dict] | None = None) -> dict:
        compiled = compiled if compiled is not None else compiled_rules(state)
        runnable = {
            node['id']: node for node in state['nodes']
            if node.get('enabled') and not node.get('excluded') and not node.get('invalid_reference')
            and self.id in node.get('adapters', []) and node.get('support', {}).get('status') == 'supported'
        }
        outbounds = [self._outbound(node) for node in sorted(runnable.values(), key=lambda item: item['id'])]
        outbounds.extend([{'protocol': 'freedom', 'tag': 'DIRECT'}, {'protocol': 'blackhole', 'tag': 'REJECT'}])
        names = {group['id']: ('DIRECT' if group['id'] == 'direct' else group.get('kernel_name', 'group-' + group['id']))
                 for group in state['groups']}
        for group in state['groups']:
            if group['id'] == 'direct':
                continue
            if group.get('mode') != 'select':
                raise ValueError('Xray 仅支持 select 代理组：' + str(group.get('name', group['id'])))
            members = [runnable[node_id]['kernel_name'] for node_id in group.get('node_ids', []) if node_id in runnable]
            if not members:
                raise ValueError('代理组没有可应用的 Xray 节点：' + str(group.get('name', group['id'])))
            selected = runnable.get(group.get('selected')) or runnable.get(group.get('node_ids', [''])[0])
            outbounds.append({'protocol': 'freedom', 'tag': names[group['id']],
                              'proxySettings': {'tag': selected['kernel_name']} if selected else None})
        outbounds = [item for item in outbounds if item.get('proxySettings') is not None or item.get('protocol') != 'freedom' or item.get('tag') in {'DIRECT'}]
        rules = []
        for route in compiled:
            target = names.get(route['target'])
            if not target:
                continue
            domain = route['host'].removeprefix('*.')
            rules.append({'type': 'field', 'domain': [('full:' if route['match'] == 'exact' else 'domain:') + domain],
                          'outboundTag': target})
        rules.append({'type': 'field', 'network': 'tcp,udp', 'outboundTag': names.get('direct', 'DIRECT')})
        entry = state.get('proxy_entry', {})
        http_port = urlsplit(str(entry.get('http_url', ''))).port or 17890
        socks_port = http_port + 2
        private = entry.get('private') if isinstance(entry.get('private'), dict) else {}
        inbounds = [
            {'tag': 'proxy-entry-http', 'listen': '127.0.0.1', 'port': http_port, 'protocol': 'http',
             'settings': {'accounts': []}},
            {'tag': 'proxy-entry-socks', 'listen': '127.0.0.1', 'port': socks_port, 'protocol': 'socks',
             'settings': {'auth': 'noauth', 'udp': True}},
        ]
        if private.get('enabled'):
            inbounds.append({'tag': 'private-proxy-entry', 'listen': private.get('listen', '0.0.0.0'),
                             'port': int(private.get('port', 17891)), 'protocol': 'http',
                             'settings': {'accounts': self._private_user(entry)}})
        return {'log': {'loglevel': 'warning'}, 'inbounds': inbounds, 'outbounds': outbounds,
                'routing': {'domainStrategy': 'AsIs', 'rules': rules}}

    def validate(self, document: dict) -> None:
        if not isinstance(document, dict) or not isinstance(document.get('inbounds'), list) or not isinstance(document.get('outbounds'), list):
            raise ValueError('Xray 候选配置格式无效')
        tags = [item.get('tag') for item in document['outbounds']]
        if any(not tag for tag in tags) or len(tags) != len(set(tags)):
            raise ValueError('Xray 出站标签为空或重复')
        available = set(tags)
        for rule in document.get('routing', {}).get('rules', []):
            if rule.get('outboundTag') not in available:
                raise ValueError('Xray 分流规则目标无效')
        ports = [int(item.get('port', 0)) for item in document['inbounds']]
        if any(port <= 0 or port > 65535 for port in ports) or len(ports) != len(set(ports)):
            raise ValueError('Xray 入口端口无效或重复')

    def expected_rules(self, document: dict) -> list[tuple[str, str, str]]:
        result = []
        for rule in document.get('routing', {}).get('rules', []):
            domains = rule.get('domain') or []
            for domain in domains:
                kind = 'DOMAIN' if str(domain).startswith('full:') else 'DOMAIN-SUFFIX'
                result.append((kind, str(domain).split(':', 1)[-1], str(rule.get('outboundTag', ''))))
        result.append(('MATCH', '', str(document.get('routing', {}).get('rules', [{}])[-1].get('outboundTag', ''))))
        return result

    def verify(self, document: dict, runtime: object, proxies: object, rules: object) -> list[str]:
        # Xray has no equivalent HTTP runtime inspection endpoint.  Structural
        # validation is still performed before launch; runtime evidence is
        # deliberately reported as unavailable rather than fabricated.
        return []

    def fail_closed_document(self, control: dict, entry: dict) -> dict:
        document = copy.deepcopy(self.render({'nodes': [], 'groups': [{'id': 'direct', 'kernel_name': 'DIRECT'}],
                                              'proxy_entry': entry, 'control': control}, []))
        document['outbounds'] = [{'protocol': 'blackhole', 'tag': 'REJECT'}]
        document['routing']['rules'] = [{'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'REJECT'}]
        return document

    def control(self, state: dict) -> tuple[dict, dict]:
        raise ValueError('Xray 当前仅提供进程级运行控制，不支持 HTTP 控制接口')

    def inspect(self, state: dict, application: dict, runtime: object = None, proxies: object = None,
                rules: object = None, version: str = '') -> dict:
        base = {'ready': True, 'version': version, 'adapter': self.id, 'deployment': 'dedicated',
                'scope': 'full', 'saved_revision': application.get('saved_revision', ''),
                'applied_revision': application.get('applied_revision', ''), 'control': 'process-only'}
        if application.get('status') == 'restore_failed':
            return {**base, 'state': 'restore_failed', 'ready': False, 'message': 'Xray 恢复失败'}
        return {**base, 'state': 'running_limited', 'message': 'Xray 已运行；该内核不提供统一 HTTP 控制面，代理组切换需重新应用配置'}

    async def fetch_runtime(self, state: dict) -> dict:
        return {'state': 'running_limited', 'ready': True, 'adapter': self.id,
                'message': 'Xray 使用进程级健康检查；暂无 HTTP 控制接口'}

    async def healthy(self, state: dict) -> bool:
        return True

    async def apply(self, state: dict, document: dict, *, supervisor=None, binary=None, config=None):
        if supervisor is None or binary is None or config is None:
            raise ValueError('Xray 应用配置需要受监督重启上下文')
        await self.restart(supervisor, binary, config)

    async def select(self, state: dict, group: dict, node: dict):
        raise ValueError('Xray 不支持运行时代理组切换，请重新应用配置')

    async def probe(self, state: dict, node: dict, target: str, timeout: int) -> int:
        raise ValueError('Xray 暂不支持通过控制接口测速，请使用直接节点测速')

    async def proxies(self, state: dict) -> dict:
        return {'version': '', 'proxies': {}}

    async def group_selection(self, state: dict, group: dict) -> str:
        return str(group.get('selected') or '')

    async def connection_snapshot(self, state: dict, host: str) -> list[dict]:
        return []
