# Provider 无业务凭据请求记录

执行日期：2026-09-30，服务器 AstrBot 4.28.2，插件 0.4.1，提交 `d13e2deb7b2cf63f53f65bc58584d3efdb9bb33f`。使用插件部署后的 AstrBot 容器和现有 Mihomo 内核，没有发送模型提示、音频、图片或业务 Token。测试用配置副本只包含占位密钥，请求由各 Provider 实例实际创建的 HTTP 客户端发出，目标为 `https://httpbin.org/ip`。

| Provider | 入口 | 规则 | 节点链路 | 出口或拒绝 | 同次连接 ID | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| `openai_chat_completion` | 稳定 HTTP `127.0.0.1:17890`；OpenAI SDK 内部 HTTP 客户端 | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `091f9b2d-1c58-40bc-9d13-c4f356ecbb29` | `PASS`（客户端入口） |
| `openai_responses` | 稳定 HTTP `127.0.0.1:17890`；Responses SDK 内部 HTTP 客户端 | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `97b243bc-49a3-4140-bfc0-8ca72c18287e` | `PASS`（客户端入口） |
| `openai_embedding` | 稳定 HTTP `127.0.0.1:17890`；Embedding SDK 内部 HTTP 客户端 | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `0e479022-2ed5-4bb8-ba2e-1f6f45d72294` | `PASS`（客户端入口） |
| `vllm_rerank` | 稳定 HTTP `127.0.0.1:17890`；Rerank `aiohttp` session | `Match` | `DIRECT` | HTTP 200，回显出口 `43.138.195.178` | `620766b2-6110-45ab-aaaa-335236beb352` | `PASS`（客户端入口） |

这四条只证明客户端经稳定入口和当前明确 `DIRECT` 规则出站；没有调用需业务凭据的模型 API，也没有执行指定节点、自动组、拒绝和失败关闭策略。随后使用同一探针对另外 26 个可接入类型执行无凭据请求，均观察到同次 Mihomo 连接：

`anthropic_chat_completion`、`azure_tts`、`bailian_rerank`、`elevenlabs_tts_api`、`fishaudio_tts_api`、`gemini_embedding`、`googlegenai_chat_completion`、`gemini_tts`、`groq_chat_completion`、`kimi_code_chat_completion`、`longcat_chat_completion`、`mimo_stt_api`、`mimo_tts_api`、`minimax_token_plan`、`mirarouter_chat_completion`、`nvidia_embedding`、`aihubmix_chat_completion`、`ollama_embedding`、`openai_tts_api`、`openrouter_chat_completion`、`ssycloud_chat_completion`、`tei_rerank`、`xai_chat_completion`、`xiaomi_chat_completion`、`xiaomi_token_plan`、`zhipu_chat_completion`。

下表每行的入口均为 `http://127.0.0.1:17890`，内核记录均为 `Match -> DIRECT`；出口为 `43.138.195.178`，除标有“明确拒绝”的类型外均由同次 `httpbin.org/ip` 响应回显。

| Provider | 同次 Mihomo 连接 ID | 出口/响应 |
| --- | --- | --- |
| `anthropic_chat_completion` | `217ffa37-e621-4dcb-ab63-19f4f32183ec` | 出口回显，200 |
| `azure_tts` | `375dff1a-130d-414a-b079-48692f6fad2b` | 出口回显，200 |
| `bailian_rerank` | `2548b0de-2aec-48f5-a13d-585d167d1a0a` | 出口回显，200 |
| `elevenlabs_tts_api` | `afa87cf2-518f-4b06-a069-3c8274c28ddc` | 出口回显，200 |
| `fishaudio_tts_api` | `a0dba6af-89b6-4d52-ac96-9ddede2f7902` | 明确拒绝 |
| `gemini_embedding` | `1f1d6237-6a7b-42ca-8984-90487b04959e` | 出口回显，200 |
| `googlegenai_chat_completion` | `4db2880c-9290-49f8-ad02-4bf07e0c1b5e` | 出口回显，200 |
| `gemini_tts` | `6796e0c4-ed1b-4797-bab7-679f986267de` | 出口回显，200 |
| `groq_chat_completion` | `3c3ade07-d66e-4a46-8ac4-13805948364b` | 出口回显，200 |
| `kimi_code_chat_completion` | `c9a9e390-0efa-4c0f-8db8-037e74fcff6f` | 出口回显，200 |
| `longcat_chat_completion` | `af31f13a-f585-4427-8df8-46efbe52459f` | 出口回显，200 |
| `mimo_stt_api` | `84c240e2-d26c-40b0-a652-aad9db0a8591` | 出口回显，200 |
| `mimo_tts_api` | `ce254059-9582-4c9a-922e-8f4c254098a1` | 出口回显，200 |
| `minimax_token_plan` | `27a97a05-6a18-463d-9e12-8d4d6a5be00d` | 出口回显，200 |
| `mirarouter_chat_completion` | `60460cd4-4c48-40a8-941a-922d3379b062` | 出口回显，200 |
| `nvidia_embedding` | `63f9f225-1bdb-4f1e-bb31-4a4f4be72ef8` | 明确拒绝，404 |
| `aihubmix_chat_completion` | `7818563d-d458-43fa-b19f-d93d19c90c27` | 出口回显，200 |
| `ollama_embedding` | `b50d2442-3673-4e44-9edf-963c998614da` | 明确拒绝，404 |
| `openai_tts_api` | `7d4727b8-6cbb-42f0-8896-75b71a69fa67` | 出口回显，200 |
| `openrouter_chat_completion` | `cded6674-7875-44ad-b38d-32e44520a7a9` | 出口回显，200 |
| `ssycloud_chat_completion` | `9bfd4e63-0759-433f-be67-682778e9c8e2` | 出口回显，200 |
| `tei_rerank` | `fefe7e01-3044-4050-8a46-11e9d4483a35` | 出口回显，200 |
| `xai_chat_completion` | `3310c982-48d5-4e30-b2d2-5d1f8015aad7` | 出口回显，200 |
| `xiaomi_chat_completion` | `f9796a40-4514-4c49-85c5-ac44cfea3479` | 出口回显，200 |
| `xiaomi_token_plan` | `5640d975-870b-4fba-ba8e-7615be0ea65d` | 出口回显，200 |
| `zhipu_chat_completion` | `3ba6da3a-7e7b-43f1-90db-4bce467971c2` | 出口回显，200 |

因此，当前 30 个有独立代理路径的 Provider 都已证明“流量受代理管理中心控制”。回显端点返回 `404`、鉴权拒绝或其他非业务错误时，只要同一次请求有稳定入口、规则、连接链路和出口/明确拒绝，仍记为传输 `PASS`。`docs/TRAFFIC_REGRESSION_MATRIX.md` 的指定节点、自动组、拒绝和失败关闭策略仍为 `UNKNOWN`。14 个没有确认独立代理路径的内置类型保持原类和 `UNKNOWN`。

服务器重启后 `verify.sh` 通过，代理管理插件加载；同次重启日志中另有三个旧插件因 `astrbot_version: >=4.28.0,<4.28.2` 与当前 4.28.2 不兼容而未加载，这些报错不来自代理管理插件。
