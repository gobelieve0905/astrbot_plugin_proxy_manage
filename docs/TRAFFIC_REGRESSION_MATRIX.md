# 真实流量回归矩阵

版本：`0.3.23`
建立日期：`2026-09-28`
执行环境：服务器 AstrBot 实例；本机只维护矩阵、代码和提交，不执行插件流量测试。

这份矩阵用于记录真实请求是否经过代理管理中心。页面配置、兼容层注册、控制 API 可达和节点列表存在，都不能替代矩阵中的请求证据。每个用例都必须同时记录入口、规则、节点链路和出口；缺少任一项时结果只能是 `UNKNOWN`，不能记为通过。

## 记录约定

每个矩阵单元格是一个独立用例 ID。服务器执行时，为每个 ID 增加一条记录，字段固定如下：

| 字段 | 要求 |
| --- | --- |
| `case_id` | 与矩阵单元格完全一致 |
| `at` | 服务器时间，格式 `YYYY-MM-DD HH:MM:SS Asia/Shanghai` |
| `component` / `transport` | 组件和真实传输：`http`、`websocket`、`media-upload`、`media-download`、`polling` 或 `mcp` |
| `entry` | 入口证据：组件实际使用的稳定 HTTP/SOCKS 地址、适配器代理字段或 MCP 注入环境；只记录脱敏后的 scheme、host、port |
| `rule` | 规则证据：命中的规则类型、精确参数、目标组或 `DIRECT`/`REJECT` |
| `chain` | 节点链路证据：代理组、当前选择、节点名称/稳定 ID、内核连接记录；直连必须出现 `DIRECT` |
| `exit` | 出口证据：同一次请求返回的出口 IP 或明确拒绝错误，以及对应连接/请求关联标识 |
| `result` | `PASS`、`FAIL`、`UNKNOWN` 或 `BLOCKED` |
| `evidence_ref` | 审计事件、内核连接快照、请求时间窗口或服务器日志的脱敏引用 |
| `notes` | 重启、媒体方向、失败原因、已知限制；不得放 Token、订阅地址或认证代理 URL |

出口 IP 必须来自实际请求响应或等价的无业务凭据回显服务；不能使用节点配置中的地址、旧健康记录或本机公网查询替代。拒绝和失败关闭没有出口 IP 时，`exit` 记录拒绝错误和“无直连回落”的连接证据，结果使用 `BLOCKED`。

## 用例矩阵

策略列含义：`D` 为明确 `DIRECT`，`N` 为指定节点，`A` 为自动组，`R` 为明确拒绝，`F` 为自动组全部成员不可用时的失败关闭。每个入口都执行五种策略；切换策略后必须重新应用配置，并在同一服务器实例重新发起请求。

| 入口 / 传输 | D | N | A | R | F |
| --- | --- | --- | --- | --- | --- |
| AstrBot 全局 HTTP/HTTPS | `AB-HTTP-D` | `AB-HTTP-N` | `AB-HTTP-A` | `AB-HTTP-R` | `AB-HTTP-F` |
| Provider HTTP 请求 | `PROVIDER-D` | `PROVIDER-N` | `PROVIDER-A` | `PROVIDER-R` | `PROVIDER-F` |
| 飞书 HTTP API | `FEISHU-HTTP-D` | `FEISHU-HTTP-N` | `FEISHU-HTTP-A` | `FEISHU-HTTP-R` | `FEISHU-HTTP-F` |
| 飞书 WebSocket | `FEISHU-WS-D` | `FEISHU-WS-N` | `FEISHU-WS-A` | `FEISHU-WS-R` | `FEISHU-WS-F` |
| 飞书媒体上传/下载 | `FEISHU-MEDIA-D` | `FEISHU-MEDIA-N` | `FEISHU-MEDIA-A` | `FEISHU-MEDIA-R` | `FEISHU-MEDIA-F` |
| Telegram 轮询 / Bot API | `TG-POLL-D` | `TG-POLL-N` | `TG-POLL-A` | `TG-POLL-R` | `TG-POLL-F` |
| Telegram 媒体上传/下载 | `TG-MEDIA-D` | `TG-MEDIA-N` | `TG-MEDIA-A` | `TG-MEDIA-R` | `TG-MEDIA-F` |
| 已声明第三方插件 HTTP | `PLUGIN-D` | `PLUGIN-N` | `PLUGIN-A` | `PLUGIN-R` | `PLUGIN-F` |
| stdio MCP 外部请求 | `MCP-STDIO-D` | `MCP-STDIO-N` | `MCP-STDIO-A` | `MCP-STDIO-R` | `MCP-STDIO-F` |
| 私网 MCP 外部请求 | `MCP-PRIVATE-D` | `MCP-PRIVATE-N` | `MCP-PRIVATE-A` | `MCP-PRIVATE-R` | `MCP-PRIVATE-F` |

矩阵共 50 个用例。下表是执行记录账本；执行人只填写证据摘要和结果，不改变用例 ID 或列含义。

| 用例 ID | 入口证据 | 规则证据 | 节点链路证据 | 出口证据 | 结果 | 证据引用 |
| --- | --- | --- | --- | --- | --- | --- |
| `AB-HTTP-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `PROVIDER-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `FEISHU-HTTP-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `FEISHU-WS-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `FEISHU-MEDIA-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `TG-POLL-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `TG-MEDIA-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `PLUGIN-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `MCP-STDIO-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `MCP-PRIVATE-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |

账本中的 `{D,N,A,R,F}` 是五条独立记录的压缩写法，不能作为最终验收记录。服务器验收应将其展开为 50 行，或提交等价的 JSON/CSV，确保每个具体 ID 都有四段证据。

## 执行顺序

1. 在服务器执行既有 `verify.sh`，确认 AstrBot、插件自管内核、飞书适配器和 Telegram 进程健康；不要发送业务消息。
2. 选择一个已验证节点、一个自动组和一个可控的失败关闭组，记录配置修订号。`DIRECT`、指定节点和自动组分别应用并等待运行核对完成。
3. 对 AstrBot 全局 HTTP 和 Provider 使用无业务凭据 HTTPS 回显请求；对飞书和 Telegram 只使用测试实例的健康接口、WebSocket 握手、媒体测试文件或等价的最小请求。
4. 对已声明第三方插件执行其声明的 HTTP 请求；stdio MCP 重启子进程后执行无业务凭据请求；私网 MCP 重建或重启容器后执行同一请求。
5. 对每个请求同时采集入口、规则、连接链路和出口。内核连接记录必须在请求时间窗口内产生，不能复用前一次验证。
6. 切换到 `REJECT`，确认请求被明确阻断且没有出口连接；再使自动组所有成员不可用，确认 `fail-closed` 阻断请求且没有直连连接。
7. 恢复上一份已验证配置，复查 AstrBot 和相关适配器的状态，确认失败关闭或可信回滚没有留下静默直连。

## 通过门槛

- 50 个用例全部有具体记录；每条记录四段证据齐全。
- `D` 用例的规则和链路必须包含 `DIRECT`，出口是直连出口；不能只看 `no_proxy`。
- `N` 用例必须同时出现目标代理组和指定节点；出口 IP 必须与同次请求链路关联。
- `A` 用例必须记录自动组实际选择和选优时间；不能用配置中的首个成员代替。
- `R` 与 `F` 用例必须是 `BLOCKED`，并证明没有代理节点连接或直连回落。
- 任意组件的 HTTP、WebSocket、媒体、轮询或 MCP 传输缺少证据时，该组件保持“无法判定”，不能汇总为“已接管”。
- 失败关闭、插件热重载、AstrBot 重启后重新检查配置修订和流量状态；出现配置漂移、恢复失败或直连回落即不通过。

当前提交只建立矩阵和记录规范；服务器未完成 50 个真实用例前，不在页面、README 或 CHANGELOG 中宣称矩阵已通过.
