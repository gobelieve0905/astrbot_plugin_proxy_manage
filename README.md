# 代理管理中心

当前版本：0.4.1

代理管理中心是 AstrBot 的统一出站流量控制面。插件负责准备和监督可插拔代理内核，管理订阅、节点、代理组、分流规则，并通过请求级证据核对实际出口。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [使用指南](USER_GUIDE.md) | 安装、首次接入、日常操作、支持矩阵和故障处理 |
| [更新日志](CHANGELOG.md) | 按版本和日期记录用户可见变化 |
| [产品定义与架构约束](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/main/docs/PRODUCT_DEFINITION.md) | 产品边界、内核抽象、流量范围、安全模型和最终验收标准 |
| [UI 开发与验收规范](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/main/docs/UI_CHECKLIST.md) | 页面生命周期、脱敏、响应式和验收清单 |
| [UI 问题复盘](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/main/docs/UI_ISSUE_SUMMARY.md) | 已知页面故障模式和修复流程 |

## 当前能力

- 自管 Mihomo、sing-box 和 Xray 内核：固定官方制品、SHA-256 校验、安装、启停、切换、更新、卸载和失败恢复。
- 订阅导入与刷新：支持单条或批量导入、节点差异、流量/到期信息、失效引用和订阅级忽略记录。
- 节点、代理组和分流规则管理：支持筛选、测速、`select`、`url-test`、`fallback`、规则优先级和配置预览。
- AstrBot 出站接入：维护全局代理入口、已验证的官方兼容层和公开的第三方插件/MCP 接入声明。
- 机器人平台兼容层：当前仅验证 AstrBot 4.28.1/4.28.2 下的飞书/Lark 与 Telegram；其他平台 SDK 没有完成官方兼容层和真实流量验证，不作兼容承诺。
- 运行核对与安全边界：区分已接管、明确直连、未接入和无法判定；配置失败时使用失败关闭和可信回滚，不静默回落直连。
- 真实流量回归矩阵：覆盖 AstrBot 全局 HTTP、Provider、飞书/Telegram 的 HTTP、WebSocket、媒体和轮询，以及第三方插件、stdio MCP、私网 MCP；每个用例记录入口、规则、节点链路和出口证据。

## 首次使用

1. 在 AstrBot 插件页面打开本插件的管理页面。
2. 在“内核资源管理”选择当前平台的固定制品，安装并启用至少一个内核；插件不会自动下载内核。
3. 在“运行控制”启动或切换内核，确认状态为已连接。
4. 导入订阅，检查节点协议、地区、支持状态、流量和到期信息，再确认写入。
5. 配置代理组和分流规则，保存后在“内核管理”点击“应用代理配置”。
6. 使用实际出站验证，只有入口、规则、节点链路和出口 IP 证据完整时才会显示“出口已确认”。

真实流量回归矩阵及服务器执行记录格式见 [TRAFFIC_REGRESSION_MATRIX.md](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/main/docs/TRAFFIC_REGRESSION_MATRIX.md)。矩阵未完成服务器验收前，相关入口保持“无法判定”。

30 类可接入 Provider 的无凭据客户端入口核验记录见 [Provider 请求记录](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/develop/docs/PROVIDER_REQUESTS_2026-09-30.md)；该记录证明流量进入代理管理中心，不等于模型 API 或五种路由策略全部通过。

## 支持范围与限制

| 类别 | 当前支持 |
| --- | --- |
| 已验证节点 | AnyTLS、HTTP、HTTPS、SOCKS5/SOCKS5H |
| 保留但未验证 | SS、VMess、VLESS、Trojan、Hysteria2、TUIC 及其他内核专用协议 |
| 内核 | 插件自管 Mihomo 1.19.31、sing-box 1.14.1、Xray 26.3.27；另含固定版本 1.19.30、1.13.2、26.6.27 |
| 制品来源 | 固定官方 URL、SHA-256 校验和离线上传；不使用 `latest` 或未经验证的镜像 |
| 机器人平台 SDK | 已验证飞书/Lark（`lark-oapi` 1.7.3）和 Telegram（`python-telegram-bot` 22.8）；其他平台 SDK 尚未验证 |
| 模型 Provider | AstrBot 4.28.2 内置 Provider 全部进入显式注册表；源码确认支持独立代理的类型按客户端注入稳定入口，其他类型保持原类并标记“未验证”，不会全局 monkey patch；每类实际请求仍需无业务凭据验证 |
| 不自动接入 | 裸 socket、显式 `trust_env=false`、未声明的第三方插件和未受信的独立容器 |

未知或暂未验证的协议会保留原始信息并明确标记，不会静默写入运行配置。Xray 的代理组控制能力有限，页面会按实际能力提示。没有内核时仍可整理订阅、节点、代理组和规则，但原生协议测速、配置应用和出口验证会显示缺少的运行条件。

## 安全边界

控制接口和统一代理入口只监听插件运行环境内的受控地址并使用随机凭据。节点连接参数、订阅令牌、控制密钥和带认证代理入口不会通过页面状态、审计记录或普通错误信息返回。插件不提供公网代理、不开放公网控制端口，也不执行订阅提供的脚本。

详细产品目标、开发约束、真实流量接入要求和最终验收标准以[产品定义与架构约束](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/main/docs/PRODUCT_DEFINITION.md)为准；具体操作以[使用指南](USER_GUIDE.md)为准。
