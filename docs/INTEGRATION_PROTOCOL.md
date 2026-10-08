# 代理管理中心插件与 MCP 接入协议

版本：`astrbot.proxy-manager/v1`

适用版本：代理管理中心 0.4.2

## 目标与选择

第三方插件或 MCP 只有在其用户明确选择“由代理管理中心管理出站流量”后，才应使用本协议。协议是 opt-in 合同，不是全局 monkey patch：代理管理中心发现声明后只做审计，不会替组件修改配置、注入环境变量、重启进程或转发凭据。

组件负责让实际外部请求使用所声明的入口。代理管理中心负责提供稳定入口、记录声明和运行配置、核对内核请求证据，并在入口或内核不可用时保持失败关闭。只有同一次请求同时具有入口、规则、代理链路和出口证据，页面才会显示“已接管”或“明确直连”。

## 声明格式

插件在自己的安装目录放置 `proxy_manager_integration.json`；MCP 在 AstrBot `mcp_server.json` 对应服务器对象内放置 `proxy_manager`。声明只能包含下列五个字段：

```json
{
  "protocol": "astrbot.proxy-manager/v1",
  "mode": "astrbot-environment",
  "protocols": ["http", "https", "websocket"],
  "restart": "process",
  "auto_apply": false
}
```

字段必须使用规定类型。`mode` 只能是 `astrbot-environment`、`private-network` 或 `manual`；`protocols` 是 `http`、`https`、`websocket`、`tcp`、`udp` 的非空数组；`restart` 是 `none`、`process`、`container`、`astrbot` 之一；`auto_apply` 必须是布尔值。未知字段、未知协议、空数组和非法类型会使声明无效。声明禁止 URL、端口、用户名、密码、Token、Cookie、Authorization、Headers、订阅地址和客户端参数。

## 三种模式

`astrbot-environment` 适用于 AstrBot 子进程或能可靠继承其环境的插件/MCP。组件在创建 HTTP/WebSocket 客户端前验证 `HTTP_PROXY`、`HTTPS_PROXY`（及小写别名）均指向稳定入口。入口缺失、冲突或内核停止必须返回错误，不能改用系统代理、旧代理或直连。使用 `trust_env=false`、裸 socket 或自建连接池的请求不能声称已接入。

`private-network` 适用于明确受信的独立容器。组件由管理员注入代理中心生成的私网 HTTP 入口和认证信息，入口只在受信容器网络可达，不得用 `ports` 发布到宿主机或公网。凭据只存在运行时密钥配置中。代理中心核对私网主机、端口和认证是否匹配；认证不匹配或设置外部 `NO_PROXY` 不算已配置。

`manual` 适用于组件自行管理代理入口。代理中心只登记能力和风险，不提供自动注入，不把声明标成已接管。组件必须在自己的文档中说明入口、凭据存储、失败关闭和真实流量验证方法。

## Python 客户端

Python 插件可使用 `proxy_manager.traffic.integration.managed_http_proxy` 作为 opt-in 检查：

```python
from proxy_manager.traffic.integration import managed_http_proxy

proxy = managed_http_proxy(declaration, enabled=settings.manage_egress, environ=os.environ)
client = httpx.AsyncClient(proxy=proxy) if proxy else httpx.AsyncClient()
```

`enabled=False` 返回 `None`；`enabled=True` 时声明无效或入口缺失会抛出 `ValueError`。调用方应把错误展示为入口不可用并停止本次请求，不能捕获后静默直连。非 Python 组件遵循相同语义。

## MCP 示例

stdio MCP：

```json
{
  "command": "node",
  "args": ["server.js"],
  "env": {"HTTP_PROXY": "http://127.0.0.1:17890", "HTTPS_PROXY": "http://127.0.0.1:17890"},
  "proxy_manager": {
    "protocol": "astrbot.proxy-manager/v1",
    "mode": "astrbot-environment",
    "protocols": ["https"],
    "restart": "process",
    "auto_apply": false
  }
}
```

独立私网 MCP 的代理环境和认证由管理员通过运行时密钥注入，声明只写能力字段，不复制地址或认证到 Git、日志或工具参数。

## 状态和验收

`needs_protocol` 表示无有效声明，`declared` 表示声明有效但仍需人工接入，`configured` 表示入口和声明匹配但等待证据；只有同一次请求具备入口、规则、链路和出口证据才显示 `managed` 或 `direct`。缺少证据显示 `unknown`，未使用入口显示 `not_connected`。

验收记录组件、时间、目标、规则、代理组/节点、连接链路和出口 IP，且不携带业务凭据。入口不可用、认证失败或内核异常时必须错误或拒绝，不得回退直连。
