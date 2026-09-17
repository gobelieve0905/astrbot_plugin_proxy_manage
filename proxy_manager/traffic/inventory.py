TRAFFIC_INVENTORY=[
    {'id':'astrbot-http-proxy','name':'AstrBot 全局 HTTP 代理','status':'unverified','restart':True,
     'method':'设置核心 http_proxy/https_proxy 指向插件稳定入口'},
    {'id':'provider-proxy','name':'模型 Provider 独立代理','status':'unverified','restart':False,
     'method':'各 Provider 的 proxy 字段接入稳定入口'},
    {'id':'platform-sdk','name':'机器人平台 SDK','status':'unverified','restart':True,
     'method':'飞书/Telegram 等适配器 HTTP 与 WebSocket 代理'},
    {'id':'plugin-http','name':'插件公共 HTTP 客户端','status':'unverified','restart':False,
     'method':'继承核心代理或显式配置'},
    {'id':'mcp-egress','name':'MCP 外部请求','status':'unverified','restart':False,
     'method':'MCP 进程/容器出口指向稳定入口'},
    {'id':'updates','name':'插件市场与依赖下载','status':'unverified','restart':False,
     'method':'更新组件走统一入口'},
]
