"""Explicit AstrBot Provider adapters and credential-free request samples.

The registry is deliberately explicit.  A Provider type added by AstrBot is
left untouched until it has an adapter entry here; runtime discovery must not
turn into a blanket monkey patch.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderAdapterSpec:
    provider_type: str
    module_name: str
    proxy_mode: str = "config"
    verification: str = "request_pending"
    coverage: str = "普通 API 请求；辅助路径按请求证据另行核验"
    requirements: tuple[tuple[str, str], ...] = ()


def _spec(provider_type: str, module_name: str, *, proxy_mode: str = "unverified") -> ProviderAdapterSpec:
    return ProviderAdapterSpec(provider_type, module_name, proxy_mode)


# AstrBot 4.28.2's built-in provider registry.  Keep this list versioned and
# explicit so a future upstream Provider remains unverified until reviewed.
PROVIDER_ADAPTERS: tuple[ProviderAdapterSpec, ...] = (
    _spec("anthropic_chat_completion", "astrbot.core.provider.sources.anthropic_source"),
    _spec("azure_tts", "astrbot.core.provider.sources.azure_tts_source"),
    _spec("bailian_rerank", "astrbot.core.provider.sources.bailian_rerank_source"),
    _spec("dashscope_embedding", "astrbot.core.provider.sources.dashscope_embedding_source"),
    _spec("dashscope_tts", "astrbot.core.provider.sources.dashscope_tts"),
    _spec("edge_tts", "astrbot.core.provider.sources.edge_tts_source"),
    _spec("elevenlabs_tts_api", "astrbot.core.provider.sources.elevenlabs_tts_source"),
    _spec("fishaudio_tts_api", "astrbot.core.provider.sources.fishaudio_tts_api_source"),
    _spec("gemini_embedding", "astrbot.core.provider.sources.gemini_embedding_source"),
    _spec("googlegenai_chat_completion", "astrbot.core.provider.sources.gemini_source"),
    _spec("gemini_tts", "astrbot.core.provider.sources.gemini_tts_source"),
    _spec("genie_tts", "astrbot.core.provider.sources.genie_tts"),
    _spec("groq_chat_completion", "astrbot.core.provider.sources.groq_source"),
    _spec("gsv_tts_selfhost", "astrbot.core.provider.sources.gsv_selfhosted_source"),
    _spec("gsvi_tts_api", "astrbot.core.provider.sources.gsvi_tts_source"),
    _spec("kimi_code_chat_completion", "astrbot.core.provider.sources.kimi_code_source"),
    _spec("longcat_chat_completion", "astrbot.core.provider.sources.longcat_source"),
    _spec("mimo_stt_api", "astrbot.core.provider.sources.mimo_stt_api_source"),
    _spec("mimo_tts_api", "astrbot.core.provider.sources.mimo_tts_api_source"),
    _spec("minimax_token_plan", "astrbot.core.provider.sources.minimax_token_plan_source"),
    _spec("minimax_tts_api", "astrbot.core.provider.sources.minimax_tts_api_source"),
    _spec("mirarouter_chat_completion", "astrbot.core.provider.sources.mirarouter_source"),
    _spec("nvidia_embedding", "astrbot.core.provider.sources.nvidia_embedding_source"),
    _spec("nvidia_rerank", "astrbot.core.provider.sources.nvidia_rerank_source"),
    _spec("aihubmix_chat_completion", "astrbot.core.provider.sources.oai_aihubmix_source"),
    _spec("ollama_embedding", "astrbot.core.provider.sources.ollama_embedding_source"),
    _spec("openai_embedding", "astrbot.core.provider.sources.openai_embedding_source"),
    _spec("openai_responses", "astrbot.core.provider.sources.openai_responses_source"),
    _spec("openai_chat_completion", "astrbot.core.provider.sources.openai_source"),
    _spec("openai_tts_api", "astrbot.core.provider.sources.openai_tts_api_source"),
    _spec("openrouter_chat_completion", "astrbot.core.provider.sources.openrouter_source"),
    _spec("sensevoice_stt_selfhost", "astrbot.core.provider.sources.sensevoice_selfhosted_source"),
    _spec("ssycloud_chat_completion", "astrbot.core.provider.sources.ssycloud_source"),
    _spec("tei_rerank", "astrbot.core.provider.sources.tei_rerank_source"),
    _spec("vllm_rerank", "astrbot.core.provider.sources.vllm_rerank_source"),
    _spec("volcengine_tts", "astrbot.core.provider.sources.volcengine_tts"),
    _spec("openai_whisper_api", "astrbot.core.provider.sources.whisper_api_source"),
    _spec("openai_whisper_selfhost", "astrbot.core.provider.sources.whisper_selfhosted_source"),
    _spec("xai_chat_completion", "astrbot.core.provider.sources.xai_source"),
    _spec("xiaomi_chat_completion", "astrbot.core.provider.sources.xiaomi_source"),
    _spec("xiaomi_token_plan", "astrbot.core.provider.sources.xiaomi_token_plan_source"),
    _spec("xinference_rerank", "astrbot.core.provider.sources.xinference_rerank_source"),
    _spec("xinference_stt", "astrbot.core.provider.sources.xinference_stt_provider"),
    _spec("zhipu_chat_completion", "astrbot.core.provider.sources.zhipu_source"),
)

CONFIG_PROXY_TYPES = frozenset({
    "anthropic_chat_completion", "azure_tts", "elevenlabs_tts_api",
    "fishaudio_tts_api", "gemini_embedding", "googlegenai_chat_completion",
    "gemini_tts", "groq_chat_completion", "kimi_code_chat_completion",
    "longcat_chat_completion", "mimo_stt_api", "mimo_tts_api",
    "minimax_token_plan", "mirarouter_chat_completion", "nvidia_embedding",
    "aihubmix_chat_completion", "ollama_embedding", "openai_embedding",
    "openai_responses", "openai_chat_completion", "openai_tts_api",
    "openrouter_chat_completion", "ssycloud_chat_completion",
    "xai_chat_completion", "xiaomi_chat_completion", "xiaomi_token_plan",
    "zhipu_chat_completion",
})
SESSION_PROXY_TYPES = frozenset({
    "bailian_rerank", "tei_rerank", "vllm_rerank",
})
TRANSPORT_COVERAGE = {
    "dashscope_embedding": "原生 SDK 文本与多模态文本 Embedding 请求",
    "edge_tts": "Communicate 显式 proxy；服务器缺少 edge-tts，实际 WebSocket 待验证",
    "gsv_tts_selfhost": "初始化、权重设置与语音 GET；内部地址也由内核 DIRECT 规则处理",
    "gsvi_tts_api": "语音 POST 与返回 audio_url 的二次下载",
    "minimax_tts_api": "语音 POST 与 SSE 流",
    "nvidia_rerank": "惰性创建及关闭后重建的排序 POST 客户端",
    "volcengine_tts": "语音 POST",
    "openai_whisper_api": "OpenAI 转写客户端与 MediaResolver 外部音频下载",
    "xinference_rerank": "SDK 初始化同步鉴权、模型查询与独立模型句柄排序请求",
    "xinference_stt": "SDK 初始化同步鉴权、模型查询、转写与外部音频下载",
    "dashscope_tts": "仅 Qwen HTTP 与返回音频下载；CosyVoice WebSocket 尚未显式接入/验证",
    "sensevoice_stt_selfhost": "本地推理；外部输入媒体通过稳定入口下载，模型下载尚未覆盖",
    "openai_whisper_selfhost": "本地推理；外部输入媒体通过稳定入口下载，模型下载尚未覆盖",
}
LOCAL_COVERAGE = {
    "genie_tts": "普通请求调用本地 genie.tts；可选依赖缺失，模型/资源下载未验证",
}
SDK_REQUIREMENTS = {
    "dashscope_embedding": (("dashscope", "1.27.4"),),
    "dashscope_tts": (("dashscope", "1.27.4"),),
    "xinference_rerank": (("xinference-client", "3.2.0"),),
    "xinference_stt": (("xinference-client", "3.2.0"),),
}
PROVIDER_ADAPTER_MAP = {
    item.provider_type: ProviderAdapterSpec(
        item.provider_type,
        item.module_name,
        "session" if item.provider_type in SESSION_PROXY_TYPES else
        "config" if item.provider_type in CONFIG_PROXY_TYPES else
        "partial" if item.provider_type in {"dashscope_tts", "sensevoice_stt_selfhost", "openai_whisper_selfhost"} else
        "transport" if item.provider_type in TRANSPORT_COVERAGE else "unverified",
        coverage=TRANSPORT_COVERAGE.get(item.provider_type, LOCAL_COVERAGE.get(
            item.provider_type, "普通 API 请求；辅助路径按请求证据另行核验")),
        requirements=SDK_REQUIREMENTS.get(item.provider_type, ()),
    )
    for item in PROVIDER_ADAPTERS
}


def request_sample(provider_type: str) -> dict[str, object]:
    """Return a safe request record template without business credentials."""

    spec = PROVIDER_ADAPTER_MAP.get(provider_type)
    if spec is None:
        return {
            "provider_type": provider_type,
            "result": "UNKNOWN",
            "entry": "unverified",
            "rule": "unverified",
            "chain": "unverified",
            "exit": "unverified",
            "notes": "Provider 不在显式注册表中，未自动改写",
        }
    return {
        "provider_type": provider_type,
        "result": "UNKNOWN",
        "entry": "pending stable HTTP entry" if spec.proxy_mode != "unverified" else "unverified",
        "rule": "pending rule match",
        "chain": "pending connection snapshot",
        "exit": "pending credential-free echo or explicit refusal",
        "notes": spec.coverage + "；不得携带业务凭据；请求证据独立于适配器安装状态",
    }
