from __future__ import annotations

import os


TRAFFIC_INVENTORY=[
    {'id':'astrbot-http-proxy','name':'AstrBot 全局 HTTP 代理','restart':True,
     'method':'设置核心 http_proxy/https_proxy 指向插件稳定入口'},
    {'id':'provider-proxy','name':'模型 Provider 独立代理','restart':False,
     'method':'各 Provider 的 proxy 字段接入稳定入口'},
    {'id':'platform-sdk','name':'机器人平台 SDK','restart':True,
     'method':'飞书/Telegram 等适配器 HTTP 与 WebSocket 代理'},
    {'id':'plugin-http','name':'插件公共 HTTP 客户端','restart':False,
     'method':'继承核心代理或显式配置'},
    {'id':'mcp-egress','name':'MCP 外部请求','restart':False,
     'method':'MCP 进程/容器出口指向稳定入口'},
    {'id':'updates','name':'插件市场与依赖下载','restart':False,
     'method':'更新组件走统一入口'},
    {'id':'recent-verification','name':'最近一次受控验证请求','restart':False,
     'method':'通过请求级内核连接记录核对规则与出口链路'},
]


def traffic_inventory(state: dict, application: dict, environ: dict|None=None) -> list[dict]:
    environ=environ if environ is not None else os.environ
    entry=str((state.get('proxy_entry') or {}).get('http_url') or '').rstrip('/')
    configured={str(environ.get(key,'')).rstrip('/') for key in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY') if environ.get(key)}
    verification=application.get('verification') if isinstance(application,dict) else {}
    traced=bool(isinstance(verification,dict) and verification.get('verified') and
                verification.get('trace',{}).get('request_correlated') and
                application.get('status')=='applied' and
                application.get('saved_revision')==application.get('applied_revision')==verification.get('runtime_revision'))
    values=[]
    for definition in TRAFFIC_INVENTORY:
        item=dict(definition)
        if item['id']=='astrbot-http-proxy':
            if entry and entry in configured:
                item.update({'status':'managed' if traced else 'unknown',
                             'message':'已观察到 AstrBot 环境使用插件入口，且请求级证据完整' if traced else '已观察到 AstrBot 环境指向插件入口，但尚无请求级出口证据'})
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
        else:
            item.update({'status':'not_connected','message':'当前版本尚未实现该接入点的配置与验证'})
        values.append(item)
    return values
