from __future__ import annotations

import copy
import os
from collections.abc import Iterable, Mapping


class TrafficRegistry:
    """统一维护流量接入定义，并根据运行证据生成公开状态。"""

    DEFAULT_DEFINITIONS = (
        {'id': 'astrbot-http-proxy', 'name': 'AstrBot 全局 HTTP 代理', 'restart': True,
         'method': '设置核心 http_proxy/https_proxy 指向插件稳定入口',
         'verification': 'AstrBot 进程请求与内核连接记录、规则、链路及出口 IP',
         'bypass_risk': '显式 trust_env=false 或原生 socket 不继承环境'},
        {'id': 'provider-proxy', 'name': '模型 Provider 独立代理', 'restart': False,
         'method': '官方 Provider 的 proxy 字段指向稳定入口',
         'verification': '不携带业务凭据的 Provider 专项请求与内核连接记录',
         'bypass_risk': '部分实现显式 trust_env=false，未填写 proxy 时不会继承环境'},
        {'id': 'platform-sdk', 'name': '机器人平台 SDK', 'restart': True,
         'method': '平台专用 proxy 字段或 SDK 代理能力',
         'verification': '重启适配器后对 HTTP、WebSocket、媒体分别关联内核连接记录',
         'bypass_risk': 'SDK 长连接、媒体客户端或 webhook 可能不继承环境'},
        {'id': 'updates', 'name': '插件市场与依赖下载', 'restart': False,
         'method': '更新组件显式使用稳定入口',
         'verification': '下载请求的内核记录和制品摘要校验',
         'bypass_risk': '市场、GitHub、PyPI 与 pip 安装器是独立进程或客户端'},
        {'id': 'plugin-subscriptions', 'name': '代理管理中心订阅请求', 'restart': False,
         'method': '导入预览、手动与定时刷新显式使用稳定 HTTP 入口',
         'verification': '真实订阅请求（含重定向）的内核连接记录、规则与出口链路；入口故障时失败关闭',
         'bypass_risk': '不读取环境代理；入口缺失或不可用时请求失败，不回退直连'},
        {'id': 'recent-verification', 'name': '最近一次受控验证请求', 'restart': False,
         'method': '通过请求级内核连接记录核对规则与出口链路',
         'verification': '同次请求的入口、规则、代理链、节点与出口 IP',
         'bypass_risk': '只代表该次受控请求，不代表其他组件'},
    )

    def __init__(self, definitions: Iterable[Mapping[str, object]] | None = None):
        self._definitions: dict[str, dict] = {}
        for definition in self.DEFAULT_DEFINITIONS if definitions is None else definitions:
            self.register(definition)

    @property
    def definitions(self) -> list[dict]:
        """返回按注册顺序排列的定义副本。"""
        return [copy.deepcopy(value) for value in self._definitions.values()]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def register(self, definition: Mapping[str, object]) -> dict:
        """注册一个流量定义；重复 ID 或缺少基本字段会被拒绝。"""
        if not isinstance(definition, Mapping):
            raise TypeError('流量定义必须是映射')
        item = copy.deepcopy(dict(definition))
        identifier = str(item.get('id') or '').strip()
        if not identifier:
            raise ValueError('流量定义缺少 id')
        if not str(item.get('name') or '').strip():
            raise ValueError('流量定义缺少 name')
        if identifier in self._definitions:
            raise ValueError('流量定义重复：' + identifier)
        item['id'] = identifier
        self._definitions[identifier] = item
        return copy.deepcopy(item)

    def get(self, identifier: str) -> dict | None:
        value = self._definitions.get(str(identifier))
        return copy.deepcopy(value) if value is not None else None

    def snapshot(self, state: dict, application: dict, environ: dict | None = None,
                 astrbot: dict | None = None, audit: dict | None = None) -> list[dict]:
        """根据当前配置、进程和审计证据生成统一的流量接入清单。"""
        environ = environ if environ is not None else os.environ
        state = state if isinstance(state, dict) else {}
        entry = str((state.get('proxy_entry') or {}).get('http_url') or '').rstrip('/')
        configured = {
            str(environ.get(key, '')).rstrip('/')
            for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY')
            if environ.get(key)
        }
        application = application if isinstance(application, dict) else {}
        verification = application.get('verification')
        verification = verification if isinstance(verification, dict) else {}
        traced = bool(
            verification.get('verified')
            and verification.get('scope') == 'astrbot-core'
            and verification.get('trace', {}).get('request_correlated')
            and application.get('status') == 'applied'
            and application.get('saved_revision') == application.get('applied_revision')
            == verification.get('runtime_revision')
        )
        astrbot = astrbot if isinstance(astrbot, dict) else {}
        audit = audit if isinstance(audit, dict) else {}
        policy = 'managed' if astrbot.get('configured') else 'pending'
        values = []
        for definition in self._definitions.values():
            item = copy.deepcopy(definition)
            identifier = item['id']
            if identifier == 'astrbot-http-proxy':
                if astrbot.get('effective'):
                    direct = (verification.get('group') or {}).get('id') == 'direct'
                    item.update({
                        'status': 'direct' if traced and direct else ('managed' if traced else 'unknown'),
                        'message': 'AstrBot 请求已由内核规则明确选择 DIRECT' if traced and direct else (
                            'AstrBot 请求已通过插件入口并取得完整请求级证据'
                            if traced else 'AstrBot 当前进程已使用插件入口，但尚无请求级出口证据'),
                    })
                elif astrbot.get('configured'):
                    item.update({'status': 'unknown', 'message': '插件入口已写入 AstrBot 配置，等待重启后验证'})
                elif entry and entry in configured:
                    item.update({'status': 'unknown', 'message': '已观察到 AstrBot 环境指向插件入口，但缺少接入事务与请求级证据'})
                elif configured:
                    item.update({'status': 'not_connected', 'message': 'AstrBot 当前使用其他代理入口'})
                else:
                    item.update({'status': 'not_connected', 'message': 'AstrBot 当前未配置全局 HTTP 代理'})
            elif identifier == 'recent-verification':
                if traced:
                    direct = (verification.get('group') or {}).get('id') == 'direct'
                    item.update({'status': 'direct' if direct else 'managed',
                                 'message': '最近请求由内核规则明确选择 DIRECT' if direct else
                                 '最近请求已关联到内核代理组和节点链路'})
                else:
                    item.update({'status': 'unknown', 'message': '尚无完整的请求级规则与出口证据'})
            elif identifier == 'provider-proxy':
                self._provider_status(item, audit, astrbot, policy)
            elif identifier == 'platform-sdk':
                self._platform_status(item, audit, astrbot, policy)
            elif identifier == 'updates':
                item.update({'status': 'not_connected', 'message': '插件市场、GitHub、PyPI 和依赖安装器尚未统一接入稳定入口'})
            elif identifier == 'plugin-subscriptions':
                item.update({'status': 'unknown' if entry else 'not_connected',
                             'message': '订阅请求已显式使用稳定入口，实际规则与出口需请求级验证' if entry else
                             '稳定 HTTP 入口缺失，订阅请求已阻止'})
                item['integration'] = {
                    'state': 'managed' if entry else 'pending',
                    'mode': 'explicit-entry',
                    'message': '每跳重定向校验公网目标；入口故障不回退直连',
                }
            else:
                item.update({'status': 'not_connected', 'message': '当前版本尚未实现该接入点的配置与验证'})
            if 'integration' not in item:
                item['integration'] = {
                    'state': policy,
                    'mode': 'astrbot-environment' if identifier == 'astrbot-http-proxy' else '',
                    'message': '默认未命中规则由统一内核选择 DIRECT',
                }
            values.append(item)
        return values

    inventory = snapshot

    @staticmethod
    def _provider_status(item: dict, audit: dict, astrbot: dict, policy: str) -> None:
        entries = audit.get('providers', [])
        providers = [value for value in entries if value.get('kind', 'provider') == 'provider']
        model_entries = [value for value in entries if value.get('kind') == 'model']
        compatibility = audit.get('compatibility', {})
        compatibility = compatibility if isinstance(compatibility, dict) else {}
        supported_types = {
            key for key, value in (compatibility.get('providers') or {}).items()
            if isinstance(value, dict) and value.get('state') == 'installed'
        }
        stable = [value for value in providers if value.get('enabled') and value.get('proxy') == 'stable_entry']
        other = [value for value in providers if value.get('enabled') and value.get('proxy') == 'other_proxy']
        unsupported = [value for value in providers if value.get('enabled') and value.get('type') not in supported_types]
        if compatibility.get('state') == 'unsupported':
            item.update({'status': 'not_connected', 'message': '官方兼容层未启用：' + str(compatibility.get('message') or '运行时版本不受支持')})
        elif unsupported and compatibility.get('state') == 'installed':
            item.update({'status': 'unknown', 'message': '官方兼容层已接入支持的 Provider，但仍有 ' + str(len(unsupported)) + ' 个 Provider 未适配；尚无请求级 Provider 证据'})
        elif compatibility.get('state') == 'installed':
            item.update({'status': 'unknown', 'message': '官方兼容层已为 ' + str(len(supported_types)) + ' 类 Provider 注入稳定入口；尚无请求级 Provider 证据'})
        elif unsupported and stable:
            item.update({'status': 'unknown', 'message': '已接入 ' + str(len(stable)) + ' 个 Provider，但仍有 ' + str(len(unsupported)) + ' 个 Provider 未适配'})
        elif stable:
            item.update({'status': 'unknown', 'message': '已发现 ' + str(len(stable)) + ' 个 Provider 指向稳定入口；尚无请求级 Provider 证据'})
        elif other:
            item.update({'status': 'not_connected', 'message': '已发现 ' + str(len(other)) + ' 个 Provider 使用其他代理入口'})
        elif providers and astrbot.get('effective'):
            item.update({'status': 'unknown', 'message': '未发现 Provider 专用 proxy；部分客户端可能继承全局代理，但尚无请求级证据'})
        else:
            item.update({'status': 'not_connected', 'message': '未发现已启用 Provider 的稳定入口专用 proxy 配置'})
        item['discovered'] = providers
        item['model_count'] = len(model_entries)
        item['integration'] = {
            'state': 'managed' if compatibility.get('state') == 'installed' else policy,
            'mode': 'runtime-entry',
            'message': '由版本化官方兼容层注入 Provider 稳定入口；未命中规则默认 DIRECT',
        }

    @staticmethod
    def _platform_status(item: dict, audit: dict, astrbot: dict, policy: str) -> None:
        platforms = audit.get('platforms', [])
        compatibility = audit.get('compatibility', {})
        compatibility = compatibility if isinstance(compatibility, dict) else {}
        message = (
            '官方兼容层已接入已验证平台；HTTP、WebSocket 和媒体仍需请求级验证'
            if compatibility.get('state') == 'installed' else
            '发现 ' + str(len(platforms)) + ' 个平台；全局代理可能覆盖部分 HTTP，HTTP、WebSocket 和媒体仍需逐项请求级验证'
            if platforms else '未发现可审计的平台配置'
        )
        item.update({
            'status': 'unknown' if platforms and (astrbot.get('effective') or compatibility.get('state') == 'installed') else 'not_connected',
            'message': message,
            'discovered': platforms,
            'integration': {
                'state': 'managed' if compatibility.get('state') == 'installed' else policy,
                'mode': 'runtime-entry',
                'message': '由版本化官方兼容层注入平台 SDK 代理；长连接仍需请求级验证',
            },
        })



TRAFFIC_REGISTRY = TrafficRegistry()
TRAFFIC_INVENTORY = TRAFFIC_REGISTRY.definitions


__all__ = ['TRAFFIC_INVENTORY', 'TRAFFIC_REGISTRY', 'TrafficRegistry']
