# Provider 无业务凭据请求记录

执行日期：2026-09-30，服务器 AstrBot 4.28.2，插件 0.4.1，提交 `d13e2deb7b2cf63f53f65bc58584d3efdb9bb33f`。使用插件部署后的 AstrBot 容器和现有 Mihomo 内核，没有发送模型提示、音频、图片或业务 Token。测试用配置副本只包含占位密钥，请求由各 Provider 实例实际创建的 HTTP 客户端发出，目标为 `https://httpbin.org/ip`。

| Provider | 入口 | 规则 | 节点链路 | 出口或拒绝 | 同次连接 ID | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| `openai_chat_completion` | 稳定 HTTP `127.0.0.1:17890`；OpenAI SDK 内部 HTTP 客户端 | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `091f9b2d-1c58-40bc-9d13-c4f356ecbb29` | `PASS`（客户端入口） |
| `openai_responses` | 稳定 HTTP `127.0.0.1:17890`；Responses SDK 内部 HTTP 客户端 | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `97b243bc-49a3-4140-bfc0-8ca72c18287e` | `PASS`（客户端入口） |
| `openai_embedding` | 稳定 HTTP `127.0.0.1:17890`；Embedding SDK 内部 HTTP 客户端 | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `0e479022-2ed5-4bb8-ba2e-1f6f45d72294` | `PASS`（客户端入口） |
| `vllm_rerank` | 稳定 HTTP `127.0.0.1:17890`；Rerank `aiohttp` session | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `620766b2-6110-45ab-aaaa-335236beb352` | `PASS`（客户端入口） |

这四条只证明客户端经稳定入口和当前明确 `DIRECT` 规则出站；没有调用需业务凭据的模型 API，也没有执行指定节点、自动组、拒绝和失败关闭策略。`docs/TRAFFIC_REGRESSION_MATRIX.md` 的五项 Provider 策略用例因此仍为 `UNKNOWN`。其余 26 个已安装适配器仅经过源码路径和构造级检查，未获得请求级证据；14 个没有确认独立代理路径的内置类型保持原类和 `UNKNOWN`。后续 Provider 类型必须逐个补真实请求与同窗口四段证据后，才能提升验证状态。

服务器重启后 `verify.sh` 通过，代理管理插件加载；同次重启日志中另有三个旧插件因 `astrbot_version: >=4.28.0,<4.28.2` 与当前 4.28.2 不兼容而未加载，这些报错不来自代理管理插件。
