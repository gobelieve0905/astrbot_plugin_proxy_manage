# 代理管理中心

当前版本：0.4.4

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

- 简洁概览：核心计数、真实配置状态、全局代理接入和可展开的分流预览；实际出站证据保留在服务器诊断快照与审计中。

- 自管 Mihomo、sing-box 和 Xray 内核：固定官方制品、SHA-256 校验、安装、启停、切换、更新、卸载和失败恢复。
- 订阅导入与刷新：支持单条或批量导入、节点差异、流量/到期信息、失效引用和订阅级忽略记录；预览与刷新显式使用稳定 HTTP 入口，入口不可用时失败，不回退直连。
- 节点、代理组和分流规则管理：支持筛选、测速、`select`、`url-test`、`fallback`、规则优先级和配置预览。
- AstrBot 出站接入：维护全局代理入口，并说明官方兼容层和第三方插件/MCP 在进入统一入口后的网址规则边界。
- 机器人平台兼容层：已适配 AstrBot 4.28.1/4.28.2 下的飞书/Lark 与 Telegram；两者的兼容层范围和真实传输验证状态见下表，其他平台 SDK 不作兼容承诺。
- 删除节点或订阅后保留空代理组和原出口规则；失效目标允许保存待修复配置，但阻止应用，避免原代理流量隐含转为直连。
- 运行核对与安全边界：区分已接管、明确直连、未接入和无法判定；配置失败时使用失败关闭和可信回滚，不静默回落直连。
- 真实流量回归矩阵：覆盖 AstrBot 全局 HTTP、Provider、飞书/Telegram 的 HTTP、WebSocket、媒体和轮询，以及 AstrBot 核心、Provider 和平台 SDK；第三方插件/MCP 只按统一入口和网址规则处理。

## 首次使用

1. 在 AstrBot 插件页面打开本插件的管理页面。
2. 插件会检测系统、CPU 架构和自管内核；没有通过校验的内核时，概览和“内核管理”显示安装引导。选择固定制品并点击“受限直连安装所选版本”，或离线上传官方制品，校验成功后点击“启用”；插件不会自动下载内核。首次安装通道只供固定内核制品使用，业务联网保持失败关闭。
3. 在“运行控制”启动或切换内核，确认状态为已连接。
4. 先应用允许订阅目标访问的规则（没有节点时可使用受管理 DIRECT），再导入订阅，检查节点协议、地区、支持状态、流量和到期信息，再确认写入。
5. 配置代理组和分流规则，保存后在“内核管理”点击“应用代理配置”。
6. 在概览页展开“分流预览”，输入域名并查询命中的规则、代理组和节点；需要实际出站证据时，查看服务器诊断快照和“审计历史”中的验证事件。

真实流量回归矩阵及服务器执行记录格式见 [TRAFFIC_REGRESSION_MATRIX.md](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/main/docs/TRAFFIC_REGRESSION_MATRIX.md)。矩阵未完成服务器验收前，相关入口保持“无法判定”。

AstrBot 4.28.2 内置 44 类 Provider 的逐项适配范围如下。表中的“显示名称”就是 AstrBot“新建模型提供商”窗口中的名称；同一显示名称可能随 AstrBot 版本变化，判断适配状态以旁边的内部 `provider_type` 为准。30 类有既有请求级证据；另外 11 类的已确认客户端路径已显式配置为使用稳定入口，不要求真实业务请求或 API 凭据才能接入。入口配置不等于业务 API 成功，也不等于指定节点、自动组、拒绝和失败关闭已逐类验收。

## Provider 适配范围

### 已有请求级传输证据（30 类）

| AstrBot 显示名称 | 内部 `provider_type` | 传输验证结果 |
| --- | --- | --- |
| `Anthropic`（界面显示 `Anthropic Compatible`） | `anthropic_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| `Azure TTS` | `azure_tts` | 已验证：稳定入口、规则、连接链路和出口 |
| 阿里云百炼重排序 | `bailian_rerank` | 已验证：稳定入口、规则、连接链路和出口 |
| ElevenLabs TTS(API) | `elevenlabs_tts_api` | 已验证：稳定入口、规则、连接链路和出口 |
| FishAudio TTS(API) | `fishaudio_tts_api` | 已验证：稳定入口、规则、连接链路和明确拒绝 |
| Gemini Embedding | `gemini_embedding` | 已验证：稳定入口、规则、连接链路和出口 |
| `Google Gemini`（界面显示 `Gemini Compatible`） | `googlegenai_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| Gemini TTS | `gemini_tts` | 已验证：稳定入口、规则、连接链路和出口 |
| Groq | `groq_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| Kimi Coding Plan | `kimi_code_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| LongCat | `longcat_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| MiMo STT(API) | `mimo_stt_api` | 已验证：稳定入口、规则、连接链路和出口 |
| MiMo TTS(API) | `mimo_tts_api` | 已验证：稳定入口、规则、连接链路和出口 |
| MiniMax Token Plan | `minimax_token_plan` | 已验证：稳定入口、规则、连接链路和出口 |
| MiraRouter | `mirarouter_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| NVIDIA Embedding | `nvidia_embedding` | 已验证：稳定入口、规则、连接链路和明确拒绝 |
| AIHubMix | `aihubmix_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| Ollama Embedding | `ollama_embedding` | 已验证：稳定入口、规则、连接链路和明确拒绝 |
| OpenAI Embedding | `openai_embedding` | 已验证：稳定入口、规则、连接链路和出口 |
| OpenAI Responses、DeepSeek Responses、xAI（截图中的兼容模板） | `openai_responses` | 已验证：稳定入口、规则、连接链路和出口 |
| OpenAI Compatible、Kimi（内部模板名 `Moonshot`）、MiniMax、DeepSeek、NVIDIA、Azure OpenAI、Ollama、LM Studio、Gemini OpenAI API、302.AI、SiliconFlow、PPIO、TokenPony、Compshare、ModelScope、FastGPT | `openai_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| OpenAI TTS(API) | `openai_tts_api` | 已验证：稳定入口、规则、连接链路和出口 |
| OpenRouter | `openrouter_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| SSYCloud(胜算云) | `ssycloud_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| TEI Rerank | `tei_rerank` | 已验证：稳定入口、规则、连接链路和出口 |
| vLLM Rerank | `vllm_rerank` | 已验证：稳定入口、规则、连接链路和出口 |
| xAI（专用 Provider 类型，当前选择器未单独显示） | `xai_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| Xiaomi | `xiaomi_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |
| Xiaomi Token Plan | `xiaomi_token_plan` | 已验证：稳定入口、规则、连接链路和出口 |
| Zhipu | `zhipu_chat_completion` | 已验证：稳定入口、规则、连接链路和出口 |

### 新增显式使用稳定入口（11 类）

| AstrBot 显示名称 | 内部 `provider_type` | 当前状态 |
| --- | --- | --- |
| DashScope Embedding | `dashscope_embedding` | DashScope SDK 的文本/多模态文本调用传入实例级 HTTP Session |
| 阿里云百炼 TTS(API) | `dashscope_tts` | Qwen HTTP、CosyVoice WebSocket 和返回音频下载使用稳定入口 |
| Edge TTS | `edge_tts` | `Communicate` 显式传入稳定入口；当前服务器缺少 SDK，安装该 Provider 依赖后生效 |
| GSV TTS(Local) | `gsv_tts_selfhost` | 会话初始化和后续 GET 使用稳定入口 |
| GSVI TTS(API) | `gsvi_tts_api` | 合成 POST 和返回音频二次下载均走稳定入口 |
| MiniMax TTS(API) | `minimax_tts_api` | SSE 合成会话显式走稳定入口 |
| NVIDIA Rerank | `nvidia_rerank` | 惰性创建/重建的客户端显式走稳定入口 |
| 火山引擎_TTS(API) | `volcengine_tts` | 合成 POST 显式走稳定入口 |
| Whisper(API) | `openai_whisper_api` | OpenAI 转写客户端及外部音频下载显式走稳定入口 |
| Xinference Rerank / STT | `xinference_rerank`, `xinference_stt` | SDK 初始化鉴权、主会话、排序模型句柄和 STT 输入下载使用稳定入口 |

### 部分路径接入（2 类）

| AstrBot 显示名称 | 内部 `provider_type` | 已接入范围与限制 |
| --- | --- | --- |
| SenseVoice(Local) | `sensevoice_stt_selfhost` | 本地推理不产生外部请求；输入媒体下载使用稳定入口，模型下载不在覆盖范围 |
| Whisper(Local) | `openai_whisper_selfhost` | 本地推理不产生外部请求；输入媒体下载使用稳定入口，模型下载不在覆盖范围 |

### 仍未适配（1 类）

| AstrBot 显示名称 | 内部 `provider_type` | 当前状态 |
| --- | --- | --- |
| Genie TTS | `genie_tts` | 本地 `genie.tts` 推理；可选依赖缺失，模型和角色资源下载路径无法确认 |

未知 Provider 不在以上 44 类中，保持“未验证”，不会被全局 monkey patch。新增适配的 SDK 版本或 Provider 方法签名与审计不匹配时，兼容层保持原类并报告不支持。

## 机器人平台适配范围

| 平台 | 兼容层与版本 | 已适配范围 | 真实传输验证状态 |
| --- | --- | --- | --- |
| 飞书 / Lark | AstrBot 4.28.1/4.28.2；`lark-oapi` 1.7.3 | HTTP API、WebSocket、媒体上传/下载 | WebSocket 已确认进入稳定入口并产生内核连接，媒体上传/下载有接管记录；HTTP 的同次出口证据不足，五种策略尚未全部验收 |
| Telegram | AstrBot 4.28.1/4.28.2；`python-telegram-bot` 22.8 | Bot API 轮询、媒体上传/下载 | 已有官方兼容层；轮询、媒体及指定节点/自动组/拒绝/失败关闭尚未完成逐项真实验证 |
| 其他平台 SDK | 未提供官方兼容层 | 不承诺自动接入 | 未验证 |

## 支持范围与限制

| 类别 | 当前支持 |
| --- | --- |
| 已验证节点 | AnyTLS、HTTP、HTTPS、SOCKS5/SOCKS5H |
| 保留但未验证 | SS、VMess、VLESS、Trojan、Hysteria2、TUIC 及其他内核专用协议 |
| 内核 | 插件自管 Mihomo 1.19.31、sing-box 1.14.1、Xray 26.3.27；另含固定版本 1.19.30、1.13.2、26.6.27 |
| 制品来源 | 固定官方 URL、SHA-256 校验和离线上传；不使用 `latest` 或未经验证的镜像 |
| 机器人平台 SDK | 已适配飞书/Lark（`lark-oapi` 1.7.3）和 Telegram（`python-telegram-bot` 22.8）；各传输验证状态见上表，其他平台 SDK 不作兼容承诺 |
| 模型 Provider | 44 类全部进入显式注册表；30 类有既有请求级证据、11 类新增路径已显式使用稳定入口、2 类部分接入、1 类未适配；未知类型保持原类，逐项名单见上表 |
| 不自动接入 | 未显式使用统一入口的 `trust_env=False` 客户端、指向其他入口的自带代理、裸 socket 及独立网络；[具体边界见使用指南](USER_GUIDE.md#第三方插件与-mcp-的流量边界) |

未知或暂未验证的协议会保留原始信息并明确标记，不会静默写入运行配置。Xray 的代理组控制能力有限，页面会按实际能力提示。没有内核时仍可整理订阅、节点、代理组和规则，但原生协议测速、配置应用和出口验证会显示缺少的运行条件。

## 安全边界

控制接口和统一代理入口只监听插件运行环境内的受控地址并使用随机凭据。节点连接参数、订阅令牌、控制密钥和带认证代理入口不会通过页面状态、审计记录或普通错误信息返回。插件不提供公网代理、不开放公网控制端口，也不执行订阅提供的脚本。

详细产品目标、开发约束、真实流量接入要求和最终验收标准以[产品定义与架构约束](https://github.com/gobelieve0905/astrbot_plugin_proxy_manage/blob/main/docs/PRODUCT_DEFINITION.md)为准；具体操作以[使用指南](USER_GUIDE.md)为准。
