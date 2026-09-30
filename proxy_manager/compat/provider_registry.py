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
PROVIDER_ADAPTER_MAP = {
    item.provider_type: ProviderAdapterSpec(
        item.provider_type,
        item.module_name,
        "session" if item.provider_type in SESSION_PROXY_TYPES else
        "config" if item.provider_type in CONFIG_PROXY_TYPES else "unverified",
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
        "notes": "不得携带业务凭据；请求窗口内同时采集四段证据" if spec.proxy_mode != "unverified" else "源码尚无已验证的独立代理路径，不安装包装器",
    }
