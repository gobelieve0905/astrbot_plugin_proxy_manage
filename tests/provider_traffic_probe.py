"""Credential-free Provider transport probe for the server AstrBot container.

Run with the server's AstrBot Python, from this repository checkout.  The
probe never reads configured Provider credentials or invokes a model API.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import urllib.request
from pathlib import Path

from proxy_manager.compat.lease import ComponentLease
from proxy_manager.compat.provider_registry import PROVIDER_ADAPTER_MAP
from proxy_manager.compat.registry import CompatibilityManager

ECHO_URL = "https://httpbin.org/ip"
ENTRY = "http://127.0.0.1:17890"
DATA = Path("/astrbot/data/plugin_data/astrbot_plugin_proxy_manage")
SKIP = {"openai_chat_completion", "openai_responses", "openai_embedding", "vllm_rerank"}


def connections(control: str, secret: str) -> list[dict]:
    request = urllib.request.Request(
        control + "/connections", headers={"Authorization": "Bearer " + secret}
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response).get("connections", [])


def config_for(provider_type: str) -> dict:
    return {
        "id": "transport-probe",
        "type": provider_type,
        "key": ["placeholder"],
        "api_key": "placeholder",
        "embedding_api_key": "placeholder",
        "rerank_api_key": "placeholder",
        "model": "probe",
        "embedding_model": "probe",
        "rerank_model": "probe",
        "api_base": "https://httpbin.org",
        "embedding_api_base": "https://httpbin.org/v1",
        "rerank_api_base": "https://httpbin.org",
        "gemini_tts_api_key": "placeholder",
        "gemini_tts_api_base": "https://httpbin.org",
        "azure_tts_subscription_key": "A" * 32,
        "fishaudio-tts-reference-id": "a" * 32,
        "timeout": 10,
    }


async def request_for(provider_type: str, instance) -> tuple[str, str]:
    """Use the HTTP transport owned by this real Provider instance."""

    client = getattr(instance, "client", None)
    if provider_type == "azure_tts":
        async with instance.provider as native:
            response = await native.client.get(ECHO_URL)
            return str(response.status_code), response.json().get("origin", "")
    if provider_type == "fishaudio_tts_api":
        try:
            await instance.get_audio("probe")
        except Exception as exc:
            return type(exc).__name__, "explicit refusal"
        return "unexpected success", ""
    if provider_type in {"nvidia_embedding", "ollama_embedding"}:
        try:
            await instance.get_embeddings(["probe"])
        except Exception as exc:
            return type(exc).__name__, "explicit refusal"
        return "unexpected success", ""
    if provider_type == "googlegenai_chat_completion":
        client = instance._http_client
    elif provider_type in {"gemini_embedding", "gemini_tts"}:
        client = instance.client._api_client._async_httpx_client
    elif hasattr(client, "_client"):
        client = client._client
    if provider_type in {"bailian_rerank", "tei_rerank", "vllm_rerank"}:
        async with client.get(ECHO_URL) as response:
            return str(response.status), (await response.json()).get("origin", "")
    if client is None:
        raise RuntimeError("Provider did not create a request client")
    response = await client.get(ECHO_URL, timeout=10)
    return str(response.status_code), response.json().get("origin", "")


async def probe(provider_type: str, control: str, secret: str) -> dict:
    spec = PROVIDER_ADAPTER_MAP[provider_type]
    if spec.proxy_mode == "unverified":
        return {"type": provider_type, "result": "UNKNOWN", "reason": "adapter unverified"}
    try:
        importlib.import_module(spec.module_name)
        from astrbot.core.provider.register import provider_cls_map

        base = provider_cls_map[provider_type].cls_type
        manager = CompatibilityManager(None, ComponentLease("probe", ENTRY))
        wrapped = manager._provider_wrapper(provider_type, base)
        instance = wrapped(config_for(provider_type), {})
    except Exception as exc:
        return {"type": provider_type, "result": "UNKNOWN", "reason": "construction: " + type(exc).__name__}

    before = {item.get("id") for item in connections(control, secret)}
    seen: dict[str, dict] = {}

    async def capture():
        for _ in range(100):
            for item in connections(control, secret):
                if item.get("id") not in before and item.get("metadata", {}).get("host") == "httpbin.org":
                    seen[item["id"]] = item
            await asyncio.sleep(0.05)

    task = asyncio.create_task(capture())
    try:
        status, exit_value = await request_for(provider_type, instance)
    except Exception as exc:
        status, exit_value = type(exc).__name__, "explicit refusal"
    await task
    client = getattr(instance, "client", None)
    try:
        if client is not None and hasattr(client, "close"):
            await client.close()
        elif client is not None and hasattr(client, "aclose"):
            await client.aclose()
        if getattr(instance, "_http_client", None) is not None:
            await instance._http_client.aclose()
    except Exception:
        pass
    matched = next(iter(seen.values()), None)
    if matched is None:
        return {"type": provider_type, "result": "UNKNOWN", "entry": ENTRY, "reason": status, "connection": "not observed"}
    verified = bool(matched.get("rule") and matched.get("chains") and exit_value)
    return {
        "type": provider_type,
        "result": "PASS" if verified else "UNKNOWN",
        "entry": ENTRY,
        "rule": matched.get("rule", ""),
        "chain": matched.get("chains", []),
        "exit": exit_value,
        "response": status,
        "connection_id": matched.get("id", ""),
    }


async def main():
    document = json.loads((DATA / "runtime-application.json").read_text())["document"]
    control = "http://" + document["external-controller"]
    secret = document["secret"]
    names = sys.argv[1:] or [name for name in PROVIDER_ADAPTER_MAP if name not in SKIP]
    for name in names:
        if name in PROVIDER_ADAPTER_MAP:
            print(json.dumps(await probe(name, control, secret), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
