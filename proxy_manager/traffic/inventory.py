from __future__ import annotations

import os


TRAFFIC_INVENTORY=[
    {'id':'astrbot-http-proxy','name':'AstrBot 全局 HTTP 代理','restart':True,
     'method':'设置核心 http_proxy/https_proxy 指向插件稳定入口','verification':'AstrBot 进程请求与内核连接记录、规则、链路及出口 IP','bypass_risk':'显式 trust_env=false 或原生 socket 不继承环境'},
    {'id':'provider-proxy','name':'模型 Provider 独立代理','restart':False,
     'method':'官方 Provider 的 proxy 字段指向稳定入口','verification':'不携带业务凭据的 Provider 专项请求与内核连接记录','bypass_risk':'部分实现显式 trust_env=false，未填写 proxy 时不会继承环境'},
    {'id':'platform-sdk','name':'机器人平台 SDK','restart':True,
     'method':'平台专用 proxy 字段或 SDK 代理能力','verification':'重启适配器后对 HTTP、WebSocket、媒体分别关联内核连接记录','bypass_risk':'SDK 长连接、媒体客户端或 webhook 可能不继承环境'},
    {'id':'plugin-http','name':'插件公共 HTTP 客户端','restart':False,
     'method':'继承核心代理或显式配置','verification':'插件声明接入点并提供无凭据请求级验证','bypass_risk':'第三方插件可使用 trust_env=false、裸 socket 或自建客户端'},
    {'id':'mcp-egress','name':'MCP 外部请求','restart':True,
     'method':'stdio 注入回环代理；独立容器使用带随机认证的私网入口；同机进程需受控回环转发','verification':'每个 MCP 的无业务凭据出站请求与内核记录','bypass_risk':'MCP 可显式禁用环境代理、使用裸 socket，或从独立网络直接出站'},
    {'id':'updates','name':'插件市场与依赖下载','restart':False,
     'method':'更新组件显式使用稳定入口','verification':'下载请求的内核记录和制品摘要校验','bypass_risk':'市场、GitHub、PyPI 与 pip 安装器是独立进程或客户端'},
    {'id':'recent-verification','name':'最近一次受控验证请求','restart':False,
     'method':'通过请求级内核连接记录核对规则与出口链路','verification':'同次请求的入口、规则、代理链、节点与出口 IP','bypass_risk':'只代表该次受控请求，不代表其他组件'},
]


def traffic_inventory(state: dict, application: dict, environ: dict|None=None, astrbot: dict|None=None, audit: dict|None=None) -> list[dict]:
    environ=environ if environ is not None else os.environ
    entry=str((state.get('proxy_entry') or {}).get('http_url') or '').rstrip('/')
    configured={str(environ.get(key,'')).rstrip('/') for key in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY') if environ.get(key)}
    verification=application.get('verification') if isinstance(application,dict) else {}
    verification=verification if isinstance(verification,dict) else {}
    traced=bool(isinstance(verification,dict) and verification.get('verified') and verification.get('scope')=='astrbot-core' and
                verification.get('trace',{}).get('request_correlated') and
                application.get('status')=='applied' and
                application.get('saved_revision')==application.get('applied_revision')==verification.get('runtime_revision'))
    astrbot=astrbot if isinstance(astrbot,dict) else {}
    audit=audit if isinstance(audit,dict) else {}
    values=[]; policy='managed' if astrbot.get('configured') else 'pending'
    for definition in TRAFFIC_INVENTORY:
        item=dict(definition)
        if item['id']=='astrbot-http-proxy':
            if astrbot.get('effective'):
                direct=(verification.get('group') or {}).get('id')=='direct'
                item.update({'status':'direct' if traced and direct else ('managed' if traced else 'unknown'),
                             'message':'AstrBot 请求已由内核规则明确选择 DIRECT' if traced and direct else (
                                 'AstrBot 请求已通过插件入口并取得完整请求级证据' if traced else 'AstrBot 当前进程已使用插件入口，但尚无请求级出口证据')})
            elif astrbot.get('configured'):
                item.update({'status':'unknown','message':'插件入口已写入 AstrBot 配置，等待重启后验证'})
            elif entry and entry in configured:
                item.update({'status':'unknown','message':'已观察到 AstrBot 环境指向插件入口，但缺少接入事务与请求级证据'})
            elif configured:
                item.update({'status':'not_connected','message':'AstrBot 当前使用其他代理入口'})
            else:
                item.update({'status':'not_connected','message':'AstrBot 当前未配置全局 HTTP 代理'})
        elif item['id']=='recent-verification':
            if traced:
                direct=(verification.get('group') or {}).get('id')=='direct'
                item.update({'status':'direct' if direct else 'managed',
                             'message':'最近请求由内核规则明确选择 DIRECT' if direct else '最近请求已关联到内核代理组和节点链路'})
            else:
                item.update({'status':'unknown','message':'尚无完整的请求级规则与出口证据'})
        elif item['id']=='provider-proxy':
            providers=audit.get('providers',[])
            stable=[value for value in providers if value.get('enabled') and value.get('proxy')=='stable_entry']
            other=[value for value in providers if value.get('enabled') and value.get('proxy')=='other_proxy']
            if stable:
                item.update({'status':'unknown','message':'已发现 '+str(len(stable))+' 个 Provider 指向稳定入口；尚无请求级 Provider 证据'})
            elif other:
                item.update({'status':'not_connected','message':'已发现 '+str(len(other))+' 个 Provider 使用其他代理入口'})
            elif providers and astrbot.get('effective'):
                item.update({'status':'unknown','message':'未发现 Provider 专用 proxy；部分客户端可能继承全局代理，但尚无请求级证据'})
            else:
                item.update({'status':'not_connected','message':'未发现已启用 Provider 的稳定入口专用 proxy 配置'})
            item['discovered']=providers
            item['integration']={'state':policy,'mode':'astrbot-environment',
                                 'message':'由代理管理插件兼容 AstrBot Provider；未命中规则默认 DIRECT'}
        elif item['id']=='platform-sdk':
            platforms=audit.get('platforms',[])
            item.update({'status':'unknown' if platforms and astrbot.get('effective') else 'not_connected',
                         'message':('发现 '+str(len(platforms))+' 个平台；全局代理可能覆盖部分 HTTP，HTTP、WebSocket 和媒体仍需逐项请求级验证' if platforms else '未发现可审计的平台配置'),
                         'discovered':platforms})
            item['integration']={'state':policy,'mode':'astrbot-environment',
                                 'message':'由代理管理插件兼容 AstrBot 平台适配器；长连接仍需请求级验证'}
        elif item['id']=='plugin-http':
            count=int(audit.get('plugin_count',0) or 0)
            declarations=audit.get('plugin_integrations',[])
            compatible=[value for value in declarations if (value.get('declaration') or {}).get('state')=='compatible']
            environment=[value for value in compatible if value['declaration'].get('mode')=='astrbot-environment']
            if environment:
                integration='managed' if astrbot.get('configured') else 'pending'
                message='发现 '+str(len(environment))+' 个插件声明遵守 AstrBot 代理环境；尚无请求级证据'
            elif compatible:
                integration='declared'; message='发现 '+str(len(compatible))+' 个插件已声明协议，但未声明使用 AstrBot 代理环境'
            else:
                integration='needs_protocol'; message='发现 '+str(count)+' 个插件配置项；尚无有效统一接入协议声明'
            item.update({'status':'unknown' if environment and astrbot.get('configured') else 'not_connected',
                         'message':message,
                         'discovered':declarations,
                         'integration':{'state':integration,
                                        'mode':'astrbot-environment','message':'第三方插件通过 astrbot.proxy-manager/v1 声明出站兼容性'}})
        elif item['id']=='mcp-egress':
            mcps=audit.get('mcps',[])
            configured=[value for value in mcps if value.get('proxy')=='configured']
            compatible=[value for value in mcps if (value.get('declaration') or {}).get('state')=='compatible']
            supported_configured=[value for value in configured if (value.get('declaration') or {}).get('state')=='compatible']
            item.update({'status':'unknown' if configured else 'not_connected',
                         'message':('发现 '+str(len(configured))+' 个 MCP 已配置入口，但尚无请求级出口证据' if configured else
                                    ('发现 '+str(len(mcps))+' 个 MCP，尚未配置或无法安全接入其外部出口' if mcps else '未发现 MCP 配置')),
                         'discovered':mcps})
            item['integration']={'state':'managed' if supported_configured else ('declared' if compatible else 'needs_protocol'),
                                 'mode':'protocol','message':'MCP 通过 astrbot.proxy-manager/v1 声明 stdio 或私网入口兼容性'}
        elif item['id']=='updates':
            item.update({'status':'not_connected','message':'插件市场、GitHub、PyPI 和依赖安装器尚未统一接入稳定入口'})
        else:
            item.update({'status':'not_connected','message':'当前版本尚未实现该接入点的配置与验证'})
        if 'integration' not in item:
            item['integration']={'state':policy if item['id'] in {'astrbot-http-proxy','updates','recent-verification'} else 'needs_protocol',
                                 'mode':'astrbot-environment' if item['id']=='astrbot-http-proxy' else '',
                                 'message':'默认未命中规则由统一内核选择 DIRECT'}
        values.append(item)
    return values
