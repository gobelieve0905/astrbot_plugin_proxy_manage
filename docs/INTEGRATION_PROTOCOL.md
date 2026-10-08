# 代理管理中心插件与 MCP 接入协议

版本：`astrbot.proxy-manager/v1`

适用版本：代理管理中心 0.4.2

## 目标与选择

本协议中的“插件”指通过 AstrBot 插件市场、GitHub 或 ZIP 安装到同一 AstrBot 实例的其他 Star 插件，覆盖插件自身的 HTTP 请求、SDK、下载、WebSocket、联网工具和子进程出站。普通 Python HTTP 客户端只是实现细节。

这些插件或 MCP 只有在其用户明确选择“由代理管理中心管理出站流量”后，才应使用本协议。协议是 opt-in 合同，不是全局 monkey patch：代理管理中心发现声明后只做审计，不会替市场插件修改配置、注入环境变量、重启进程或转发凭据。未采用协议的市场插件不会因为安装了代理管理中心就自动完成适配。

组件负责让实际外部请求使用所声明的入口。代理管理中心负责提供稳定入口、记录声明和运行配置、核对内核请求证据，并在入口或内核不可用时保持失败关闭。只有同一次请求同时具有入口、规则、代理链路和出口证据，页面才会显示“已接管”或“明确直连”。

## 声明格式

AstrBot 市场插件在自己的安装目录放置 `proxy_manager_integration.json`；MCP 在 AstrBot `mcp_server.json` 对应服务器对象内放置 `proxy_manager`。声明只能包含下列五个字段：

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

`astrbot-environment` 是市场插件的标准模式，也适用于 AstrBot 子进程 MCP。插件在创建 HTTP/WebSocket 客户端前验证 `HTTP_PROXY`、`HTTPS_PROXY`（及小写别名）均指向稳定入口，或通过 AstrBot 注册插件实例向代理管理中心申请 lease。入口缺失、冲突或内核停止必须返回错误，不能改用系统代理、旧代理或直连。`trust_env=false` 客户端必须显式设置代理；裸 socket 或不支持代理的连接池仍属于需适配项。

`private-network` 适用于明确受信的独立容器。组件由管理员注入代理中心生成的私网 HTTP 入口和认证信息，入口只在受信容器网络可达，不得用 `ports` 发布到宿主机或公网。凭据只存在运行时密钥配置中。代理中心核对私网主机、端口和认证是否匹配；认证不匹配或设置外部 `NO_PROXY` 不算已配置。

`manual` 适用于组件自行管理代理入口。代理中心只登记能力和风险，不提供自动注入，不把声明标成已接管。组件必须在自己的文档中说明入口、凭据存储、失败关闭和真实流量验证方法。

## AstrBot 市场插件接入

市场插件应在自己的 `_conf_schema.json` 提供默认关闭的布尔配置项，例如 `manage_egress`。插件应把 `proxy_manager_integration.json` 与 `main.py`、`metadata.yaml` 一同打包；声明文件不能被该插件的 `export-ignore` 排除。声明中的 `restart: "process"` 对进程内插件意味着重建客户端或重载该插件，只有确需重启宿主时才使用 `astrbot`。

通过 AstrBot 的 `Context.get_registered_star("astrbot_plugin_proxy_manage")` 获取代理中心实例，调用公开方法 `get_proxy_manager_lease(plugin_id, enabled=True, protocols=[...])`。`plugin_id` 使用请求方 `metadata.yaml` 的 `name`，代理中心通过已注册元数据映射安装目录，不要求目录名与插件名相同。调用只向已加载、已启用、声明有效的插件返回 lease：`http_proxy`、`https_proxy`、`no_proxy`、`restart`、`revision` 和 `status: "configured"`；不返回认证凭据，也不声称该插件流量已经过验证。当前桥接仅提供 HTTP、HTTPS 和 HTTP 代理承载的 WebSocket；TCP/UDP 声明仍需专用适配。

开关关闭时插件沿用原行为且不申请 lease；开关开启后，代理中心缺失/停用、声明无效或内核不可用都必须失败关闭。声明是作者的能力承诺，不是用户授权。桥接利用 AstrBot 的 Python Star 运行时，插件的 Node.js/Go 等子进程由该 Star 显式传入代理环境；各客户端均需独立配置，不能假定子进程自动遵守环境。无需导入代理中心内部 Python 包。

伪代码示例（AstrBot 插件代码）：

```python
if config.get("manage_egress", False):
    manager = context.get_registered_star("astrbot_plugin_proxy_manage")
    if not manager or not manager.activated or not manager.star_cls:
        raise RuntimeError("代理管理中心未启用")
    lease = manager.star_cls.get_proxy_manager_lease(
        plugin_id="my_market_plugin",  # 请求方 metadata.yaml 的 name
        enabled=True,
        protocols=["http", "https"],
    )
    # 用 lease 配置插件自身所有声明范围内的客户端与子进程。
```

插件实例方法是 AstrBot 运行时公开桥接；`proxy_manager_integration.json` 是静态兼容声明。应在发起受管理请求前重新发现实例并申请 lease，避免缓存旧插件对象。启动加载顺序导致代理中心尚未就绪时，插件可保持加载但暂停受管理请求，后续重新发现，不自动直连。`revision` 表示内核已应用策略修订；变化后按 `restart` 要求重建客户端。已有连接在重载/停用后也必须关闭或保持失败关闭。

## Python 客户端实现示例

Python 插件可使用 `proxy_manager.traffic.integration.managed_http_proxy` 作为 opt-in 检查：

```python
# lease 由 AstrBot 市场插件接入章节的公开桥接取得。
proxy = lease["https_proxy"]
client = httpx.AsyncClient(proxy=proxy)
```

插件开关关闭时不申请 lease；开关开启时声明无效或入口缺失会抛出错误。调用方应把错误展示为入口不可用并停止本次请求，不能捕获后静默直连。非 Python 组件遵循相同语义。

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
