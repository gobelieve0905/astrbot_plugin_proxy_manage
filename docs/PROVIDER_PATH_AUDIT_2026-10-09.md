# AstrBot 4.28.2 Provider 路径审计

审计日期：2026-10-09。上游源码来自服务器运行镜像中的 AstrBot 4.28.2；SDK 版本只记录服务器实际已安装的版本。该审计识别普通请求的构造路径，不等同于真实 Provider 业务成功或出口验证。

## 新增显式入口

| `provider_type` | 普通请求与附属传输 | 适配状态 |
| --- | --- | --- |
| `dashscope_embedding` | DashScope 1.27.4 的文本及多模态文本 SDK 调用逐次传入 `requests.Session`，固定 stable entry 且关闭环境读取 | 普通 SDK HTTP 调用使用稳定入口 |
| `dashscope_tts` | Qwen SDK HTTP、CosyVoice `SpeechSynthesizer` WebSocket 与返回音频 URL 下载 | 三条外部传输路径均使用稳定入口 |
| `edge_tts` | `edge_tts.Communicate(proxy=...)` 显式接收入口 | 当前服务器未安装 SDK；依赖可用时使用稳定入口 |
| `gsv_tts_selfhost` | 初始化建立会话及其后模型权重、合成 GET | 已显式注入 |
| `gsvi_tts_api` | 合成 POST 与返回 `audio_url` 的二次 GET | 两个 HTTP 请求均显式注入 |
| `minimax_tts_api` | SSE 合成会话的 POST | 显式注入 |
| `nvidia_rerank` | 排序时惰性创建会话以及关闭后的重建 | 显式注入 |
| `volcengine_tts` | TTS HTTP POST | 显式注入 |
| `openai_whisper_api` | OpenAI AsyncClient 使用 `httpx.AsyncClient(proxy=..., trust_env=False)`；MediaResolver 外部音频下载也使用入口 | 显式注入 |
| `xinference_rerank` | Xinference Client 构造时同步鉴权、异步查询，以及 `get_model()` 单独创建的 rerank session | 初始化请求和模型句柄均显式注入 |
| `xinference_stt` | Xinference Client 构造时同步鉴权、异步查询、转写 POST 和 MediaResolver 外部输入下载 | 显式注入 |

适配器只为精确列出的官方 Provider 方法克隆函数 globals 并替换该 Provider 私有客户端工厂，不修改 AstrBot 上游模块全局值。Provider 方法指纹或 SDK 版本不符时不安装相应包装器。稳定入口缺失时客户端构造失败关闭。MediaResolver 的远程下载禁用其原有的证书校验失败后不验证 TLS 的回退。

## 部分路径

| `provider_type` | 普通路径 | 限制 |
| --- | --- | --- |
| `sensevoice_stt_selfhost` | 本地推理不访问远端；输入 `MediaResolver` 下载使用入口 | 外部模型下载与 SDK 下载尚未确认；维持 `partial` 状态 |
| `openai_whisper_selfhost` | 本地推理不访问远端；输入 `MediaResolver` 下载使用入口 | Whisper 模型下载路径尚未确认；维持 `partial` 状态 |
| `genie_tts` | `genie.tts` 为本地推理 | 服务器无可选 `genie_tts` 依赖，模型与角色资源可能的下载路径未确认；保持原类 |

对所有 14 类原未验证 Provider，未把“可能继承进程环境代理”当成接入证据。30 类原有请求级记录仍独立于本次新增适配。目标是显式给 Provider 客户端设置稳定入口；新增适配不依赖业务凭据或业务成功验证。

## 运行版本

- AstrBot：4.28.2
- DashScope：1.27.4
- websocket-client：1.9.2（CosyVoice WebSocket 的 HTTP proxy 参数接口）
- xinference-client：3.2.0
- aiohttp：3.14.3
- `edge-tts`、`funasr`、`whisper`、`genie_tts`：服务器环境未安装；对应真实路径不据此宣称通过。
