# 真实流量回归矩阵

版本：`0.4.3`
建立日期：`2026-09-28`
本次修订：`2026-10-09`
执行环境：服务器 AstrBot 实例；本机只维护矩阵、代码和提交，不执行插件流量测试。

这份矩阵用于记录真实请求是否经过代理管理中心。页面配置、兼容层注册、控制 API 可达和节点列表存在，都不能替代矩阵中的请求证据。每个用例都必须同时记录入口、规则、节点链路和出口；缺少任一项时结果只能是 `UNKNOWN`，不能记为通过。

## 记录约定

每个矩阵单元格是一个独立用例 ID。服务器执行时，为每个 ID 增加一条记录，字段固定如下：

| 字段 | 要求 |
| --- | --- |
| `case_id` | 与矩阵单元格完全一致 |
| `at` | 服务器时间，格式 `YYYY-MM-DD HH:MM:SS Asia/Shanghai` |
| `component` / `transport` | 组件和真实传输：`http`、`websocket`、`media-upload`、`media-download`、`polling` 或 `mcp` |
| `entry` | 入口证据：请求实际使用的稳定 HTTP/SOCKS 地址或适配器代理字段；只记录脱敏后的 scheme、host、port。插件/MCP 只有在使用这些入口时作为请求来源背景记录，不单独建立协议入口。 |
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

矩阵共 35 个用例。插件和 MCP 不作为独立入口；其请求若进入 AstrBot 全局、Provider 或平台适配器使用的统一入口，按实际传输类型记录在对应行，若绕过入口则记录为旁路风险，不计入通过用例。

下表是执行记录账本；执行人只填写证据摘要和结果，不改变用例 ID 或列含义。

| 用例 ID | 入口证据 | 规则证据 | 节点链路证据 | 出口证据 | 结果 | 证据引用 |
| --- | --- | --- | --- | --- | --- | --- |
| `AB-HTTP-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `PROVIDER-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `FEISHU-HTTP-{D,N,A,R,F}` | D：统一入口已观测；N/A/R/F：待执行 | D：`MATCH`；其余待执行 | D：`DIRECT`；其余待执行 | D：无飞书响应出口 IP | `UNKNOWN` | 见下方 2026-09-29 记录 |
| `FEISHU-WS-{D,N,A,R,F}` | D：`127.0.0.1:17890` | D：`Match` | D：`DIRECT` | 无出口 IP 回显 | `UNKNOWN` | 见下方 2026-09-29 记录 |
| `FEISHU-MEDIA-{D,N,A,R,F}` | D：入站文件下载已观测；N/A/R/F：待执行 | D：`MATCH`；其余待执行 | D：`DIRECT`；其余待执行 | D：文件事件已进入附件处理；上传未执行 | `UNKNOWN` | 见下方 2026-09-29 记录 |
| `TG-POLL-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |
| `TG-MEDIA-{D,N,A,R,F}` | 待执行 | 待执行 | 待执行 | 待执行 | `UNKNOWN` | 待执行 |

账本中的 `{D,N,A,R,F}` 是五条独立记录的压缩写法，不能作为最终验收记录。服务器验收应将其展开为 35 行，或提交等价的 JSON/CSV，确保每个具体 ID 都有四段证据。

## 执行顺序

1. 在服务器执行既有 `verify.sh`，确认 AstrBot、插件自管内核、飞书适配器和 Telegram 进程健康；不要发送业务消息。
2. 选择一个已验证节点、一个自动组和一个可控的失败关闭组，记录配置修订号。`DIRECT`、指定节点和自动组分别应用并等待运行核对完成。
3. 对 AstrBot 全局 HTTP 和 Provider 使用无业务凭据 HTTPS 回显请求；对飞书和 Telegram 只使用测试实例的健康接口、WebSocket 握手、媒体测试文件或等价的最小请求。
4. 如需验证第三方插件或 MCP，将其使用统一入口的实际请求归入对应的 HTTP、WebSocket 或媒体用例；另记录 `trust_env=false`、自带代理、`NO_PROXY`、原生 socket、QUIC、独立进程/容器等旁路情况，不将其误记为已接管。
5. 对每个请求同时采集入口、规则、连接链路和出口。内核连接记录必须在请求时间窗口内产生，不能复用前一次验证。
6. 切换到 `REJECT`，确认请求被明确阻断且没有出口连接；再使自动组所有成员不可用，确认 `fail-closed` 阻断请求且没有直连连接。
7. 恢复上一份已验证配置，复查 AstrBot 和相关适配器的状态，确认失败关闭或可信回滚没有留下静默直连。

## 通过门槛

- 35 个用例全部有具体记录；每条记录四段证据齐全。
- `D` 用例的规则和链路必须包含 `DIRECT`，出口是直连出口；不能只看 `no_proxy`。
- `N` 用例必须同时出现目标代理组和指定节点；出口 IP 必须与同次请求链路关联。
- `A` 用例必须记录自动组实际选择和选优时间；不能用配置中的首个成员代替。
- `R` 与 `F` 用例必须是 `BLOCKED`，并证明没有代理节点连接或直连回落。
- 任意统一入口中的 HTTP、WebSocket、媒体或轮询传输缺少证据时，该入口保持“无法判定”，不能汇总为“已接管”；绕过统一入口的插件/MCP 请求必须标为旁路风险。
- 失败关闭、插件热重载、AstrBot 重启后重新检查配置修订和流量状态；出现配置漂移、恢复失败或直连回落即不通过。

当前提交只建立矩阵和记录规范；服务器未完成 35 个真实用例前，不在页面、README 或 CHANGELOG 中宣称矩阵已通过。

## 2026-09-29 飞书全链路核验记录

执行环境：服务器 AstrBot 容器，AstrBot `4.28.2`，插件 `0.4.1`。时间均为 `Asia/Shanghai`。本次没有发送业务消息。

| 用例 | 入口 | 规则 | 节点链路 | 出口证据 | 结果 | 证据引用 |
| --- | --- | --- | --- | --- | --- | --- |
| `FEISHU-HTTP-D` | 插件稳定入口 `http://127.0.0.1:17890`；通用 HTTPS 探测 `https://open.feishu.cn` 成功 | `MATCH` | `DIRECT` | HTTP `200`，但响应没有出口 IP，无法与出口地址关联 | `UNKNOWN` | 插件 verify-outbound 记录；同窗口 Mihomo `/connections` |
| `FEISHU-WS-D` | Lark 适配器实际连接 `127.0.0.1:17890` | `Match` | `DIRECT` | Mihomo 连接记录显示远端 `183.60.232.39:443`，但没有代理出口 IP 回显；无节点链路 | `UNKNOWN` | Mihomo connection id `60915cfa-8e3e-4a15-9a92-87ead08e7d14`，`e75d5845-bf53-4a60-8bd4-c9b1b3644d33`；host `msg-frontier.feishu.cn`；开始时间 `11:34:35`、`11:34:47` |
| `FEISHU-MEDIA-D`（下载） | `127.0.0.1:17890`；Lark 收到 `[ComponentType.File]` | `Match` | `DIRECT` | 文件名 `ad-name-aggregated.xlsx` 进入附件输入，说明下载内容已交给 AstrBot 处理 | `PASS`（接管） | 服务器日志 `14:40:29`；Mihomo 连接 `16632bd3-c9f6-439f-834c-34ba1762c02d`、`8c09eafb-01ab-4037-9c90-c98714b1bec2`、`6407f6f7-88f9-4c78-9c65-458b3a923015`，目标 `open.feishu.cn:443` |
| `FEISHU-MEDIA-D`（上传） | `127.0.0.1:17890`；Lark `CreateFile` + `CreateMessage` | `Match` | `DIRECT` | API 返回成功，消息 ID `om_x100b64825cddb8a8b30447540a05506`；测试文件已发送到指定 open_id | `PASS`（接管） | 实时监听连接 `c7ac9da6-f7c5-48b1-ac8a-a549b225f964`、`3cb40421-7f1d-4379-82af-fb1fc32a62ef`、`3668a679-3e09-42aa-b928-25917ee6e7fc`；目标 `open.feishu.cn:443`；时间 `14:56:32` |

### 飞书规则组代理节点核验（自动组）

服务器当前规则将 `open.feishu.cn` 匹配到 `Domain`，将 `*.feishu.cn` 匹配到 `DomainSuffix`，目标组为 `group-1790439179977`（`url-test` 自动组）。在插件重载并重新建立连接后，用户发送消息并收到机器人回复；实时 Mihomo 记录如下：

| 用例 | 目标 | 入口 | 规则 | 实际节点链路 | 结果 | 证据引用 |
| --- | --- | --- | --- | --- | --- | --- |
| `FEISHU-HTTP-A` | `open.feishu.cn:443` | `127.0.0.1:17890` | `Domain` | `node-sub-035507450c67-7abab1eeef148c01 -> group-1790439179977` | `PASS` | 连接 `0973a082-3b4f-4554-b390-5ed26a219951`、`b1f4a285-2f4b-4d1a-98e7-1a7c30321ffb`；时间 `15:07:46` |
| `FEISHU-WS-A` | `msg-frontier.feishu.cn:443` | `127.0.0.1:17890` | `DomainSuffix` | `node-sub-035507450c67-7abab1eeef148c01 -> group-1790439179977` | `PASS` | 连接 `269bed0c-7eeb-4c98-b49f-1acc7bb46fa9`、`638f69bd-2deb-4e6e-af6f-78c582457a49`；时间 `15:07:28`、`15:07:29`；日志 `15:07:15` 收到消息、`15:07:21` 开始回复、`15:07:47` 流式卡片输出 |

### 本次结论

- 飞书 WebSocket 已确认进入插件管理的 Mihomo 入口，并产生真实 `msg-frontier.feishu.cn:443` 连接；当前策略是 `MATCH -> DIRECT`，因此没有代理节点链路。
- 飞书 HTTP 只确认了统一入口和规则命中，缺少同次请求的出口 IP；不能据此宣称 HTTP 全链路通过。
- 飞书媒体上传/下载没有执行，保持 `UNKNOWN`。
- 指定节点、自动组、拒绝和失败关闭策略尚未对飞书三类传输执行，不能宣称用户已经能对这些流量逐项精准控制。
