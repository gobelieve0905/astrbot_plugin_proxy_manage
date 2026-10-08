# 代理管理中心插件与 MCP 接入协议

版本：`astrbot.proxy-manager/v1`

适用版本：代理管理中心 0.4.2

## 目标与选择

本协议中的“插件”指通过 AstrBot 插件市场、GitHub 或 ZIP 安装到同一 AstrBot 实例的其他 Star 插件，覆盖插件自身的 HTTP 请求、SDK、下载、WebSocket、联网工具和子进程出站。普通 Python HTTP 客户端只是实现细节。

这些插件或 MCP 只有在其用户明确选择“由代理管理中心管理出站流量”后，才应使用本协议。协议是 opt-in 合同，不是全局 monkey patch：代理管理中心发现声明后只做审计，不会自动修改配置、注入环境变量、重启进程或转发凭据。用户在“插件与 MCP”面板显式保存并应用组件策略后，可申请专用入口；用户显式点击“接入 MCP”时可以备份并更新该 stdio MCP 的代理环境。未采用协议的市场插件不会因为安装了代理管理中心就自动完成适配。

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

对于只有 HTTP/HTTPS 请求的 Python 插件，可直接使用代理中心公开的请求级 helper，避免重复实现 lease 校验、入口切换和客户端关闭：

```python
manager = context.get_registered_star("astrbot_plugin_proxy_manage")
if not manager or not manager.activated or not manager.star_cls:
    raise RuntimeError("代理管理中心未启用")

async with manager.star_cls.open_managed_http_client(
    "my_market_plugin", enabled=config.get("manage_egress", False), timeout=30
) as client:
    response = await client.get(url)
```

helper 每次创建客户端前重新申请 lease，固定使用 lease 的 HTTP 入口、`trust_env=False` 和失败关闭；调用方不应捕获入口错误后改用直连，也不能覆盖 `proxy`、`trust_env`、`transport` 或 `mounts`。插件仍需把自己的所有 HTTP/HTTPS 请求集中到这个边界；WebSocket、文件下载器或独立子进程按同一 lease 语义显式配置。

以上代码只放在开关开启的请求分支；开关关闭时保留原请求路径。helper 不是用于包住整个插件生命周期的连接池：每次操作进入 `async with`，退出即关闭客户端，下一次操作重新发现代理中心并获得新入口。这样普通插件只需新增五字段声明、默认关闭的开关和替换统一请求处的客户端创建；大量高频请求、第三方 SDK 或多个网络出口可继续使用 lease 接口自行管理连接池及 revision。无需安装额外 Python 包或导入代理中心内部模块。

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

### 组件策略扩展

`v1` 声明五字段保持不变；新 lease 额外返回 `component_id` 与 `target`。用户在代理管理中心保存并应用组件策略后，lease 的 `http_proxy`/`https_proxy` 返回该组件稳定专用入口，不能再硬编码 `17890` 或用全局环境替代 lease。未分配组件策略的旧接入继续使用公共稳定入口，但没有组件级代理组保证。显式停用、有失效代理组、尚未应用当前修订或内核不可用时必须拒绝 lease，不回退公共入口。

规范化 `component_routes` 保存 `id`、`kind`、`target`、`enabled`、内部稳定 `port` 与入口 `scope`。端口由代理中心分配并保留，页面不能指定或重新分配；删除策略转换为拒绝入口，防止旧客户端使用的端口被其他组件复用。所有组件规则先于域名分流，使用专用入站身份匹配出口。普通 AstrBot 全局入口保持不变。

市场插件的 `astrbot-environment` 模式及 stdio MCP 当前支持完整声明为 HTTP/HTTPS/WebSocket 的组件路由；私网 MCP 的 `private-network` 模式生成带随机认证的专用入口，使用已有私网运行时凭据，管理员负责注入。manual、同机独立进程、远端 MCP 与 TCP/UDP 会列在面板中但显示需适配，不生成虚假的生效状态。专用入口地址本身不作为请求级接管证明。

stdio MCP 的“接入 MCP”是用户显式授权动作，即使 `auto_apply: false` 也可执行；发现声明本身从不触发该动作。操作将 HTTP/HTTPS/ALL_PROXY 及大小写别名写为专用 HTTP 入口，NO_PROXY 及小写别名仅保留回环地址，其他环境与业务参数不变。先保存私有备份，再原子替换 `mcp_server.json`，不会自动重启 MCP。进程重启后仍需实际请求验证；在此之前进程可能继续使用旧环境。

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
