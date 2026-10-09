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
        {'id': 'updates', 'name': '其他插件市场与依赖下载', 'restart': False,
         'method': '由各市场客户端或依赖安装器自行决定代理方式，当前未统一接入',
         'verification': '各下载器请求的入口、规则、连接链路和结果校验',
         'bypass_risk': '市场、GitHub、PyPI 与 pip 安装器可能使用独立进程或客户端'},
        {'id': 'plugin-subscriptions', 'name': '代理管理中心订阅请求', 'restart': False,
         'method': '导入预览、手动与定时刷新显式使用稳定 HTTP 入口',
         'verification': '真实订阅请求（含重定向）的内核连接记录、规则与出口链路；入口故障时失败关闭',
         'bypass_risk': '不读取环境代理；入口缺失或不可用时请求失败，不回退直连'},
        {'id': 'kernel-update-check', 'name': '内核更新检查', 'restart': False,
         'method': 'HTTPX 使用 trust_env=True 请求官方 Release API，可能继承 AstrBot 进程环境代理',
         'verification': '更新检查请求的入口、规则、内核连接和出口记录；当前无请求级证据',
         'bypass_risk': '是否经过插件稳定入口取决于进程代理环境及目标是否命中 NO_PROXY'},
        {'id': 'kernel-artifact-download', 'name': '内核制品下载', 'restart': False,
         'method': '固定内核制品显式使用稳定入口；无可用内核时仅经用户选择使用受限直连引导通道',
         'verification': '受信 HTTPS 目标逐跳校验、固定 SHA-256；稳定入口下载仍需请求级证据',
         'bypass_risk': '首次安装仅固定制品允许受限直连；普通下载入口失败不回退直连'},
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
            for key in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')
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
                self._provider_status(item, audit, astrbot)
            elif identifier == 'platform-sdk':
                self._platform_status(item, audit, astrbot)
            elif identifier == 'updates':
                item.update({'status': 'not_connected', 'message': '其他插件市场与依赖下载尚未统一接入稳定入口'})
            elif identifier == 'plugin-subscriptions':
                item.update({'status': 'unknown' if entry else 'not_connected',
                             'evidence_status': 'unverified' if entry else 'blocked',
                             'message': '订阅请求已显式使用稳定入口，实际规则与出口需请求级验证' if entry else
                             '稳定 HTTP 入口缺失，订阅请求已阻止'})
                item['integration'] = {
                    'state': 'managed' if entry else 'pending',
                    'mode': 'explicit-entry',
                    'message': '每跳重定向校验公网目标；入口故障不回退直连',
                }
            elif identifier == 'kernel-artifact-download':
                item.update({'status':'unknown','evidence_status':'unverified',
                             'message':'固定制品使用显式入口或受限首次安装通道；不据此宣称业务流量已接管，稳定入口下载仍需请求级证据',
                             'integration':{'state':'configured','mode':'explicit-entry-or-bootstrap',
                                            'message':'不读取环境代理；首次安装由用户触发受限直连，已有可用内核时入口故障不回退'}})
            elif identifier == 'kernel-update-check':
                may_inherit_entry = bool(entry and (entry in configured or astrbot.get('effective')))
                item.update({
                    'status': 'unknown',
                    'evidence_status': 'unverified',
                    'message': (
                        'HTTPX trust_env=True，当前进程指向插件稳定入口，可能继承该入口；尚无本类请求级证据'
                        if may_inherit_entry else
                        'HTTPX trust_env=True，可继承进程环境代理；是否经过插件稳定入口尚未确认，且无请求级证据'
                    ),
                    'integration': {
                        'state': 'possible',
                        'mode': 'environment-inherited',
                        'message': '使用 HTTPX 环境代理继承；未记录本类请求的入口、规则、连接链路和出口证据',
                    },
                })
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
    def _provider_status(item: dict, audit: dict, astrbot: dict) -> None:
        entries = audit.get('providers', [])
        providers = [value for value in entries if value.get('kind', 'provider') == 'provider']
        model_entries = [value for value in entries if value.get('kind') == 'model']
        compatibility = audit.get('compatibility', {})
        compatibility = compatibility if isinstance(compatibility, dict) else {}
        supported_types = {
            key for key, value in (compatibility.get('providers') or {}).items()
            if isinstance(value, dict) and value.get('state') == 'installed'
        }
        partial_types = {
            key for key, value in (compatibility.get('providers') or {}).items()
            if isinstance(value, dict) and value.get('state') == 'partial'
        }
        adapters_installed = bool(supported_types or partial_types)
        stable = [value for value in providers if value.get('enabled') and value.get('proxy') == 'stable_entry']
        other = [value for value in providers if value.get('enabled') and value.get('proxy') == 'other_proxy']
        unsupported = [value for value in providers if value.get('enabled') and value.get('type') not in supported_types and value.get('type') not in partial_types]
        partial = [value for value in providers if value.get('enabled') and value.get('type') in partial_types]
        if compatibility.get('state') == 'unsupported':
            item.update({'status': 'not_connected', 'message': '官方兼容层未启用：' + str(compatibility.get('message') or '运行时版本不受支持')})
        elif (unsupported or partial) and adapters_installed:
            item.update({'status': 'unknown', 'message': '官方兼容层已为 ' + str(len(supported_types)) + ' 类 Provider 接入完整适配、' + str(len(partial_types)) + ' 类接入部分路径；仍有 ' + str(len(unsupported)) + ' 类未适配，且无新增请求级证据'})
        elif adapters_installed:
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
        adapter_status = 'unsupported'
        if adapters_installed:
            adapter_status = 'partial' if unsupported or partial else 'installed'
        elif compatibility.get('state') != 'unsupported':
            adapter_status = 'not_installed'
        if adapter_status == 'installed':
            integration_state = 'adapter-installed'
            integration_message = 'Provider 适配层已安装；这不代表 Provider 请求已经经过稳定入口，当前无请求级传输证据'
            integration_mode = 'runtime-entry'
        elif adapter_status == 'partial':
            integration_state = 'adapter-partial'
            integration_message = 'Provider 适配层仅覆盖部分已启用类型；已适配路径仍无请求级传输证据'
            integration_mode = 'runtime-entry'
        elif stable:
            integration_state = 'entry-configured'
            integration_message = '发现 Provider 稳定入口配置；当前无请求级传输证据'
            integration_mode = 'explicit-entry'
        elif providers and astrbot.get('effective'):
            integration_state = 'possible'
            integration_message = '部分 Provider 客户端可能继承 AstrBot 环境代理；当前无请求级传输证据'
            integration_mode = 'environment-inherited'
        else:
            integration_state = 'not-installed'
            integration_message = '没有已安装的 Provider 适配或可确认的稳定入口配置'
            integration_mode = ''
        item['discovered'] = providers
        item['model_count'] = len(model_entries)
        item['adapter_status'] = adapter_status
        item['transport_status'] = 'unverified'
        item['transport_evidence'] = {
            str(value.get('type')): 'unverified'
            for value in providers if value.get('enabled') and value.get('type')
        }
        item['integration'] = {
            'state': integration_state,
            'adapter_status': adapter_status,
            'transport_status': 'unverified',
            'mode': integration_mode,
            'adapted_types': sorted(supported_types),
            'partial_types': sorted(partial_types),
            'message': integration_message,
        }

    @staticmethod
    def _platform_status(item: dict, audit: dict, astrbot: dict) -> None:
        platforms = audit.get('platforms', [])
        compatibility = audit.get('compatibility', {})
        compatibility = compatibility if isinstance(compatibility, dict) else {}
        platform_reports = compatibility.get('platforms') or {}
        adapted_platforms = {
            name: list(value.get('protocols') or [])
            for name, value in platform_reports.items()
            if isinstance(value, dict) and value.get('state') == 'installed'
        }
        if adapted_platforms:
            adapter_status = 'installed' if len(adapted_platforms) == len(platform_reports) else 'partial'
        else:
            adapter_status = 'unsupported' if compatibility.get('state') == 'unsupported' or any(
                isinstance(value, dict) and value.get('state') == 'unsupported' for value in platform_reports.values()
            ) else 'not_installed'
        message = (
            '官方平台适配层' + ('部分已安装' if adapter_status == 'partial' else '已安装') + '；HTTP、WebSocket、轮询和媒体仍需分别进行请求级验证'
            if adapted_platforms else
            '发现 ' + str(len(platforms)) + ' 个平台；全局代理可能覆盖部分 HTTP，HTTP、WebSocket 和媒体仍需逐项请求级验证'
            if platforms else '未发现可审计的平台配置'
        )
        transport_evidence = {
            name: {protocol: 'unverified' for protocol in protocols}
            for name, protocols in adapted_platforms.items()
        }
        if adapter_status in {'installed', 'partial'}:
            integration_state = 'adapter-partial' if adapter_status == 'partial' else 'adapter-installed'
            integration_message = '平台适配层已安装；HTTP、WebSocket、媒体或轮询是否经过稳定入口仍需分别取得请求级证据'
            integration_mode = 'runtime-entry'
        elif platforms and astrbot.get('effective'):
            integration_state = 'possible'
            integration_message = '平台客户端可能继承 AstrBot 环境代理；各传输路径尚无请求级证据'
            integration_mode = 'environment-inherited'
        else:
            integration_state = 'not-installed'
            integration_message = '没有已安装的平台适配层；全局代理配置本身不证明平台传输已接入'
            integration_mode = ''
        item.update({
            'status': 'unknown' if platforms and (astrbot.get('effective') or adapted_platforms) else 'not_connected',
            'message': message,
            'discovered': platforms,
            'adapter_status': adapter_status,
            'transport_status': 'unverified',
            'transport_evidence': transport_evidence,
            'integration': {
                'state': integration_state,
                'adapter_status': adapter_status,
                'transport_status': 'unverified',
                'mode': integration_mode,
                'adapted_platforms': adapted_platforms,
                'message': integration_message,
            },
        })



TRAFFIC_REGISTRY = TrafficRegistry()
TRAFFIC_INVENTORY = TRAFFIC_REGISTRY.definitions


__all__ = ['TRAFFIC_INVENTORY', 'TRAFFIC_REGISTRY', 'TrafficRegistry']
