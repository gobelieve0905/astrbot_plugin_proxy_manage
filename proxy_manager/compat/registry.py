from __future__ import annotations

import copy
import importlib
import importlib.metadata
from dataclasses import dataclass, field
from typing import Any

from .lark import build_proxy_adapter as build_lark_adapter
from .lark import install_sdk_patch as install_lark_sdk_patch
from .lease import ComponentLease
from .telegram import build_proxy_adapter as build_telegram_adapter

SUPPORTED_ASTRBOT = "4.28.1"
SUPPORTED_SDK_VERSIONS = {
    "lark-oapi": "1.7.3",
    "python-telegram-bot": "22.8",
    "websockets": "15.0.1",
}
SUPPORTED_PROVIDER_TYPES = {
    "openai_chat_completion",
    "openai_responses",
    "openai_embedding",
    "vllm_rerank",
}
PROVIDER_MODULES = {
    "openai_chat_completion": "astrbot.core.provider.sources.openai_source",
    "openai_responses": "astrbot.core.provider.sources.openai_responses_source",
    "openai_embedding": "astrbot.core.provider.sources.openai_embedding_source",
    "vllm_rerank": "astrbot.core.provider.sources.vllm_rerank_source",
}


@dataclass
class CompatibilityReport:
    astrbot: str = ""
    sdk_versions: dict[str, str] = field(default_factory=dict)
    state: str = "not_installed"
    message: str = "尚未安装官方兼容层"
    platforms: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    patched_paths: list[str] = field(default_factory=list)

    def as_public_dict(self) -> dict:
        return {
            "astrbot": self.astrbot,
            "sdk_versions": dict(self.sdk_versions),
            "state": self.state,
            "message": self.message,
            "platforms": copy.deepcopy(self.platforms),
            "providers": copy.deepcopy(self.providers),
            "patched_paths": list(self.patched_paths),
        }


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _astrbot_version() -> str:
    try:
        module = importlib.import_module("astrbot")
    except ImportError:
        return "unknown"
    return str(getattr(module, "__version__", "unknown"))


def inspect_runtime() -> CompatibilityReport:
    report = CompatibilityReport(
        astrbot=_astrbot_version(),
        sdk_versions={name: _package_version(name) for name in SUPPORTED_SDK_VERSIONS},
    )
    mismatches = []
    if report.astrbot != SUPPORTED_ASTRBOT:
        mismatches.append(f"AstrBot {report.astrbot}")
    for name, expected in SUPPORTED_SDK_VERSIONS.items():
        actual = report.sdk_versions.get(name, "unknown")
        if actual != expected:
            mismatches.append(f"{name} {actual}")
    if mismatches:
        report.state = "unsupported"
        report.message = "当前运行时未匹配已验证指纹：" + ", ".join(mismatches)
    return report


class CompatibilityManager:
    """Owns runtime registration changes and restores them on unload."""

    def __init__(self, context, lease: ComponentLease):
        self.context = context
        self.lease = lease
        self.report = inspect_runtime()
        self._original_platforms: dict[str, object] = {}
        self._original_provider_classes: dict[str, object] = {}
        self._restore_lark = None
        self._installed = False

    def _install_platforms(self):
        from astrbot.core.platform.register import platform_cls_map

        lark_module = importlib.import_module(
            "astrbot.core.platform.sources.lark.lark_adapter"
        )
        telegram_module = importlib.import_module(
            "astrbot.core.platform.sources.telegram.tg_adapter"
        )
        for platform_type in ("lark", "telegram"):
            current = platform_cls_map.get(platform_type)
            self._original_platforms[platform_type] = getattr(
                current, "_proxy_manager_base", current
            )

        lark_lease = self.lease.for_component("platform:lark")
        self._restore_lark = install_lark_sdk_patch(lark_lease)
        platform_cls_map["lark"] = build_lark_adapter(
            lark_module.LarkPlatformAdapter,
            lark_lease,
        )
        platform_cls_map["telegram"] = build_telegram_adapter(
            telegram_module.TelegramPlatformAdapter,
            self.lease.for_component("platform:telegram"),
        )
        self.report.platforms = {
            "lark": {
                "state": "installed",
                "protocols": ["http", "https", "websocket", "media"],
                "message": "飞书 HTTP、WebSocket 和媒体路径已安装显式代理兼容层",
            },
            "telegram": {
                "state": "installed",
                "protocols": ["http", "https", "polling", "media"],
                "message": "Telegram Bot API、轮询和媒体路径已安装显式代理兼容层",
            },
        }
        self.report.patched_paths.extend(
            [
                "astrbot.core.platform.register.platform_cls_map[lark]",
                "astrbot.core.platform.register.platform_cls_map[telegram]",
                "lark_oapi.ws.client._ws_connect_kwargs",
                "lark_oapi.core.http.transport.Transport",
                "telegram.ext.ApplicationBuilder.proxy",
                "telegram.ext.ApplicationBuilder.get_updates_proxy",
            ]
        )

    def _provider_wrapper(self, provider_type: str, base):
        lease = self.lease.for_component("provider:" + provider_type)

        if provider_type == "vllm_rerank":
            class ProxySession:
                def __init__(self, session):
                    self._session = session

                def post(self, *args, **kwargs):
                    if lease.http_proxy:
                        kwargs.setdefault("proxy", lease.http_proxy)
                    return self._session.post(*args, **kwargs)

                async def close(self):
                    await self._session.close()

                def __getattr__(self, name):
                    return getattr(self._session, name)

            class ProxyManagedRerankProvider(base):
                _proxy_manager_base = base

                def __init__(self, provider_config, provider_settings):
                    config = copy.deepcopy(provider_config)
                    if lease.http_proxy:
                        config["proxy"] = lease.http_proxy
                    super().__init__(config, provider_settings)
                    self.client = ProxySession(self.client)

            ProxyManagedRerankProvider.__name__ = "ProxyManagedProvider_vllm_rerank"
            ProxyManagedRerankProvider.__qualname__ = ProxyManagedRerankProvider.__name__
            return ProxyManagedRerankProvider

        class ProxyManagedProvider(base):
            _proxy_manager_base = base

            def __init__(self, provider_config, provider_settings):
                config = copy.deepcopy(provider_config)
                if lease.http_proxy:
                    config["proxy"] = lease.http_proxy
                super().__init__(config, provider_settings)

        ProxyManagedProvider.__name__ = "ProxyManagedProvider_" + provider_type
        ProxyManagedProvider.__qualname__ = ProxyManagedProvider.__name__
        return ProxyManagedProvider

    def _install_providers(self):
        from astrbot.core.provider.register import provider_cls_map

        for provider_type, module_name in PROVIDER_MODULES.items():
            try:
                importlib.import_module(module_name)
            except (ImportError, ModuleNotFoundError) as exc:
                self.report.providers[provider_type] = {
                    "state": "unsupported",
                    "message": f"官方 Provider 模块不可用：{type(exc).__name__}",
                }
                continue
            metadata = provider_cls_map.get(provider_type)
            if metadata is None or not getattr(metadata, "cls_type", None):
                self.report.providers[provider_type] = {
                    "state": "unsupported",
                    "message": "Provider 注册表未提供可替换的类",
                }
                continue
            base = getattr(metadata.cls_type, "_proxy_manager_base", metadata.cls_type)
            self._original_provider_classes[provider_type] = base
            metadata.cls_type = self._provider_wrapper(provider_type, base)
            self.report.providers[provider_type] = {
                "state": "installed",
                "message": "Provider 配置副本已注入插件稳定 HTTP 入口",
            }

    async def install(self) -> CompatibilityReport:
        if self._installed:
            return self.report
        if self.report.state == "unsupported":
            return self.report
        try:
            self._install_platforms()
            self._install_providers()
            self.report.state = "installed"
            self.report.message = "AstrBot 4.28.1 官方平台与支持 Provider 兼容层已安装"
            self._installed = True
            await self._reload_live_components()
        except Exception as exc:
            self.report.state = "failed"
            self.report.message = f"兼容层安装失败：{type(exc).__name__}"
            self.restore()
        return self.report

    async def _reload_live_components(self):
        platform_manager = getattr(self.context, "platform_manager", None)
        if platform_manager is not None and getattr(platform_manager, "platform_insts", None):
            configs = getattr(platform_manager, "platforms_config", [])
            for config in configs:
                if config.get("type") in {"lark", "telegram"}:
                    await platform_manager.reload(config)
        provider_manager = getattr(self.context, "provider_manager", None)
        if provider_manager is not None and getattr(provider_manager, "provider_insts", None):
            configs = getattr(provider_manager, "providers_config", [])
            for config in configs:
                if config.get("type") in SUPPORTED_PROVIDER_TYPES:
                    await provider_manager.reload(config)

    def restore(self) -> None:
        try:
            from astrbot.core.platform.register import platform_cls_map

            for name, original in self._original_platforms.items():
                current = platform_cls_map.get(name)
                if current is not None and getattr(current, "_proxy_manager_lease", None):
                    if original is None:
                        platform_cls_map.pop(name, None)
                    else:
                        platform_cls_map[name] = original
        except (ImportError, AttributeError):
            pass
        try:
            from astrbot.core.provider.register import provider_cls_map

            for name, original in self._original_provider_classes.items():
                metadata = provider_cls_map.get(name)
                if metadata is not None and getattr(metadata.cls_type, "__name__", "").startswith(
                    "ProxyManagedProvider_"
                ):
                    metadata.cls_type = original
        except (ImportError, AttributeError):
            pass
        if self._restore_lark:
            self._restore_lark()
            try:
                ws_client = importlib.import_module("lark_oapi.ws.client")
                if isinstance(getattr(ws_client, "_proxy_manager_patch", None), dict):
                    ws_client._proxy_manager_patch = None
            except ImportError:
                pass
            self._restore_lark = None
        self._installed = False

    def as_public_dict(self) -> dict:
        return self.report.as_public_dict()
