"""Scoped transports for audited official Provider paths (AstrBot 4.28.2).

Functions receive private globals on the generated subclass. Neither upstream
module globals nor third-party SDK defaults are patched for other callers.
"""

from __future__ import annotations

import copy
import functools
import inspect
import types
from urllib.parse import unquote, urlsplit


def require_entry(lease):
    if not lease.http_proxy or not lease.http_proxy.strip():
        raise ValueError("Provider stable HTTP entry is required")
    return lease.http_proxy


def clone_function(function, replacements):
    if not isinstance(function, types.FunctionType):
        raise ValueError("Official Provider method fingerprint changed")
    namespace = dict(function.__globals__)
    namespace.update(replacements)
    cloned = types.FunctionType(
        function.__code__, namespace, function.__name__,
        function.__defaults__, function.__closure__,
    )
    cloned.__kwdefaults__ = copy.copy(function.__kwdefaults__)
    return functools.update_wrapper(cloned, function)


class ProxySession:
    """aiohttp facade: every request, including redirected/secondary media, uses entry.

    Loopback is also sent to the local core, which already owns its explicit
    internal DIRECT rules. This prevents a loopback-to-public redirect bypass.
    """

    def __init__(self, session, lease):
        self._session = session
        self._entry = require_entry(lease)

    def request(self, method, url, **kwargs):
        kwargs["proxy"] = self._entry
        return self._session.request(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)

    def options(self, url, **kwargs):
        return self.request("OPTIONS", url, **kwargs)

    async def close(self):
        await self._session.close()

    async def __aenter__(self):
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._session.__aexit__(*args)

    def __getattr__(self, name):
        return getattr(self._session, name)


class ScopedAiohttp:
    def __init__(self, module, lease):
        self._module = module
        self._lease = lease

    def ClientSession(self, *args, **kwargs):
        require_entry(self._lease)
        kwargs["trust_env"] = False
        return ProxySession(self._module.ClientSession(*args, **kwargs), self._lease)

    def __getattr__(self, name):
        return getattr(self._module, name)


def scoped_media_resolver(resolver, lease):
    resolve = resolver._resolve_path
    materialize = resolve.__globals__["_materialize_media_ref"]
    download = materialize.__globals__["download_file"]
    scoped_download = clone_function(download, {
        "aiohttp": ScopedAiohttp(download.__globals__["aiohttp"], lease),
    })

    async def download_verified(*args, **kwargs):
        kwargs["allow_insecure_ssl_fallback"] = False
        return await scoped_download(*args, **kwargs)

    scoped_materialize = clone_function(materialize, {"download_file": download_verified})
    return type("ProxyManagedMediaResolver", (resolver,), {
        "_resolve_path": clone_function(resolve, {"_materialize_media_ref": scoped_materialize}),
    })


class ScopedDashscopeCall:
    """DashScope 1.27.4 accepts a per-call requests session, not proxies kwargs."""

    def __init__(self, sdk_class, lease):
        self._sdk_class = sdk_class
        self._lease = lease

    def call(self, *args, **kwargs):
        import requests

        entry = require_entry(self._lease)
        with requests.Session() as session:
            session.trust_env = False
            session.proxies = {"http": entry, "https": entry}
            kwargs["session"] = session
            return self._sdk_class.call(*args, **kwargs)


def scoped_xinference_client(client, lease):
    """Cover SDK constructor auth HTTP and the separate model-handle session."""
    entry = require_entry(lease)

    class ProxyManagedXinferenceClient(client):
        def _check_cluster_authenticated(self):
            import requests

            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    self.base_url + "/v1/cluster/auth",
                    proxies={"http": entry, "https": entry}, timeout=20,
                )
                if response.status_code == 404:
                    self._cluster_authed = False
                    return
                response.raise_for_status()
                self._cluster_authed = bool(response.json()["auth"])

        def __init__(self, *args, **kwargs):
            # Upstream creates the aiohttp session before synchronous auth.
            # Close it on an auth failure rather than leaking an unowned session.
            try:
                super().__init__(*args, **kwargs)
            except Exception:
                import asyncio
                if getattr(self, "session", None) is not None:
                    asyncio.get_running_loop().create_task(self.session.close())
                    self.session = None
                raise
            self.session = ProxySession(self.session, lease)

        async def get_model(self, *args, **kwargs):
            model = await super().get_model(*args, **kwargs)
            model.session = ProxySession(model.session, lease)
            return model

    return ProxyManagedXinferenceClient


def scoped_websocket_module(module, lease):
    parts = urlsplit(require_entry(lease))
    if parts.scheme.lower() != "http" or not parts.hostname:
        raise ValueError("DashScope WebSocket requires the stable HTTP entry")
    proxy = {
        "http_proxy_host": parts.hostname,
        "http_proxy_port": parts.port or 80,
        "proxy_type": "http",
    }
    if parts.username is not None:
        proxy["http_proxy_auth"] = (unquote(parts.username), unquote(parts.password or ""))
    base_app = module.WebSocketApp

    class ProxyManagedWebSocketApp(base_app):
        def run_forever(self, *args, **kwargs):
            kwargs.update(proxy)
            return super().run_forever(*args, **kwargs)

    return types.SimpleNamespace(WebSocketApp=ProxyManagedWebSocketApp)


def scoped_dashscope_synthesizer(synthesizer, lease):
    connect_name = "_SpeechSynthesizer__connect"
    connect = getattr(synthesizer, connect_name)
    websocket = scoped_websocket_module(connect.__globals__["websocket"], lease)
    return type("ProxyManagedDashscopeSynthesizer", (synthesizer,), {
        connect_name: clone_function(connect, {"websocket": websocket}),
    })


# Only these reviewed methods receive private SDK/client factories.
AIOHTTP_METHODS = {
    "gsv_tts_selfhost": ("initialize",),
    "gsvi_tts_api": ("get_audio",),
    "minimax_tts_api": ("_call_tts_stream",),
    "nvidia_rerank": ("_get_client",),
    "volcengine_tts": ("get_audio",),
    "dashscope_tts": ("_download_audio_from_url",),
}


def build_transport_provider(provider_type, base, lease):
    entry = require_entry(lease)
    attrs = {"_proxy_manager_base": base, "_proxy_manager_lease": lease}
    for name in AIOHTTP_METHODS.get(provider_type, ()):
        method = getattr(base, name)
        attrs[name] = clone_function(method, {
            "aiohttp": ScopedAiohttp(method.__globals__["aiohttp"], lease),
        })
    if provider_type == "dashscope_embedding":
        method = base.get_embeddings
        attrs["get_embeddings"] = clone_function(method, {
            name: ScopedDashscopeCall(method.__globals__[name], lease)
            for name in ("TextEmbedding", "MultiModalEmbedding")
        })
    if provider_type == "dashscope_tts":
        method = base._call_qwen_tts
        sdk = method.__globals__["MultiModalConversation"]
        attrs["_call_qwen_tts"] = clone_function(method, {
            "MultiModalConversation": ScopedDashscopeCall(sdk, lease) if sdk else None,
        })
        method = base._synthesize_with_cosyvoice
        synthesizer = method.__globals__["SpeechSynthesizer"]
        attrs["_synthesize_with_cosyvoice"] = clone_function(method, {
            "SpeechSynthesizer": scoped_dashscope_synthesizer(synthesizer, lease),
        })
    if provider_type == "openai_whisper_api":
        method = base.__init__
        sdk = method.__globals__["AsyncOpenAI"]

        def openai_client(*args, **kwargs):
            import httpx

            client = httpx.AsyncClient(proxy=entry, trust_env=False)
            try:
                return sdk(*args, **{**kwargs, "http_client": client})
            except Exception:
                import asyncio
                asyncio.get_running_loop().create_task(client.aclose())
                raise

        attrs["__init__"] = clone_function(method, {"AsyncOpenAI": openai_client})
    if provider_type in {"xinference_rerank", "xinference_stt"}:
        method = base.initialize
        attrs["initialize"] = clone_function(method, {
            "Client": scoped_xinference_client(method.__globals__["Client"], lease),
        })
    if provider_type in {"openai_whisper_api", "xinference_stt", "sensevoice_stt_selfhost", "openai_whisper_selfhost"}:
        method = base.get_text
        attrs["get_text"] = clone_function(method, {
            "MediaResolver": scoped_media_resolver(method.__globals__["MediaResolver"], lease),
        })

    transport_base = type("ProxyManagedTransport_" + provider_type, (base,), attrs)

    class ProxyManagedProvider(transport_base):
        def __init__(self, provider_config, provider_settings):
            config = copy.deepcopy(provider_config)
            config["proxy"] = entry
            super().__init__(config, provider_settings)
            if provider_type == "edge_tts":
                self.proxy = entry

        async def terminate(self):
            try:
                terminate = getattr(super(), "terminate", None)
                if terminate:
                    result = terminate()
                    if inspect.isawaitable(result):
                        await result
            finally:
                # Xinference client.close() does not close its model handle.
                model = getattr(self, "model", None)
                if provider_type == "xinference_rerank" and model is not None:
                    await model.close()

    ProxyManagedProvider.__name__ = "ProxyManagedProvider_" + provider_type
    ProxyManagedProvider.__qualname__ = ProxyManagedProvider.__name__
    return ProxyManagedProvider
