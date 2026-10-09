from __future__ import annotations

import asyncio
import copy
import importlib
import importlib.metadata
from dataclasses import dataclass, field
from typing import Any

from .lark import build_proxy_adapter as build_lark_adapter
from .lark import install_sdk_patch as install_lark_sdk_patch
from .lease import ComponentLease
from .telegram import build_proxy_adapter as build_telegram_adapter
from .provider_registry import PROVIDER_ADAPTER_MAP, PROVIDER_ADAPTERS, request_sample
from .provider_transport import build_transport_provider

LIVE_COMPONENT_RELOAD_TIMEOUT = 8.0

SUPPORTED_ASTRBOT = "4.28.2"
SUPPORTED_ASTRBOT_VERSIONS = frozenset({"4.28.1", "4.28.2"})
SUPPORTED_SDK_VERSIONS = {
    "lark-oapi": "1.7.3",
    "python-telegram-bot": "22.8",
    "websockets": "15.0.1",
}
SUPPORTED_PLATFORM_TYPES = ("lark", "telegram")
PLATFORM_REQUIREMENTS = {
    "lark": (("lark-oapi", "1.7.3"), ("websockets", "15.0.1")),
    "telegram": (("python-telegram-bot", "22.8"),),
}
PLATFORM_MODULES = {
    "lark": ("astrbot.core.platform.sources.lark.lark_adapter", "LarkPlatformAdapter"),
    "telegram": ("astrbot.core.platform.sources.telegram.tg_adapter", "TelegramPlatformAdapter"),
}
PLATFORM_PROTOCOLS = {
    "lark": ["http", "https", "websocket", "media"],
    "telegram": ["http", "https", "polling", "media"],
}
SUPPORTED_PROVIDER_TYPES = frozenset(
    name for name, spec in PROVIDER_ADAPTER_MAP.items() if spec.proxy_mode not in {"unverified", "partial"}
)
PROVIDER_MODULES = {item.provider_type: item.module_name for item in PROVIDER_ADAPTERS}


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
    if report.astrbot not in SUPPORTED_ASTRBOT_VERSIONS:
        mismatches.append(f"AstrBot {report.astrbot}")
    if mismatches:
        report.state = "unsupported"
        report.message = "当前运行时未匹配已验证指纹：" + ", ".join(mismatches)
    for platform_type, requirements in PLATFORM_REQUIREMENTS.items():
        issues = [f"{name} {report.sdk_versions[name]}（需要 {expected}）"
                  for name, expected in requirements if report.sdk_versions[name] != expected]
        report.platforms[platform_type] = {
            "state": "unsupported" if mismatches or issues else "not_installed",
            "protocols": list(PLATFORM_PROTOCOLS[platform_type]),
            "message": report.message if mismatches else (
                "平台 SDK 未匹配已验证指纹：" + ", ".join(issues) if issues else "平台依赖匹配，尚未安装兼容层"),
        }
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
        for platform_type in SUPPORTED_PLATFORM_TYPES:
            item = {"protocols": list(PLATFORM_PROTOCOLS[platform_type])}
            self.report.platforms[platform_type] = item
            restore_sdk = None
            try:
                issues = self._dependency_issues(PLATFORM_REQUIREMENTS[platform_type])
                if issues:
                    item.update(state="unsupported", message="平台 SDK 未匹配已验证指纹：" + ", ".join(issues))
                    continue
                from astrbot.core.platform.register import platform_cls_map

                module_name, class_name = PLATFORM_MODULES[platform_type]
                module = importlib.import_module(module_name)
                base = getattr(module, class_name)
                base = getattr(base, "_proxy_manager_base", base)
                lease = self.lease.for_component("platform:" + platform_type)
                wrapped = (build_lark_adapter if platform_type == "lark" else build_telegram_adapter)(base, lease)
                if platform_type == "lark":
                    restore_sdk = install_lark_sdk_patch(lease)
                current = platform_cls_map.get(platform_type)
                self._original_platforms[platform_type] = getattr(current, "_proxy_manager_base", current)
                platform_cls_map[platform_type] = wrapped
                if restore_sdk:
                    self._restore_lark = restore_sdk
                item.update(state="installed", message="该平台已安装显式代理兼容层；各传输路径仍需请求级验证")
                self.report.patched_paths.append("astrbot.core.platform.register.platform_cls_map[" + platform_type + "]")
                self.report.patched_paths.extend([
                    "lark_oapi.ws.client._ws_connect_kwargs", "lark_oapi.core.http.transport.Transport",
                ] if platform_type == "lark" else [
                    "telegram.ext.ApplicationBuilder.proxy", "telegram.ext.ApplicationBuilder.get_updates_proxy",
                ])
            except Exception as exc:
                if restore_sdk:
                    restore_sdk()
                item.update(state="unsupported", message="平台模块或方法指纹不可用：" + type(exc).__name__ + "；保持原类")

    def _dependency_issues(self, requirements):
        issues = []
        for name, expected in requirements:
            actual = _package_version(name)
            self.report.sdk_versions[name] = actual
            if actual != expected:
                issues.append(f"{name} {actual}（需要 {expected}）")
        return issues

    def _provider_wrapper(self, provider_type: str, base):
        lease = self.lease.for_component("provider:" + provider_type)
        spec = PROVIDER_ADAPTER_MAP[provider_type]

        if spec.proxy_mode in {"transport", "partial"}:
            return build_transport_provider(provider_type, base, lease)

        if spec.proxy_mode == "session":
            class ProxySession:
                def __init__(self, session):
                    self._session = session

                def post(self, *args, **kwargs):
                    if lease.http_proxy:
                        kwargs["proxy"] = lease.http_proxy
                    return self._session.post(*args, **kwargs)

                def get(self, *args, **kwargs):
                    if lease.http_proxy:
                        kwargs["proxy"] = lease.http_proxy
                    return self._session.get(*args, **kwargs)

                async def close(self):
                    await self._session.close()

                def __getattr__(self, name):
                    return getattr(self._session, name)

            class ProxyManagedSessionProvider(base):
                _proxy_manager_base = base

                def __init__(self, provider_config, provider_settings):
                    config = copy.deepcopy(provider_config)
                    if lease.http_proxy:
                        config["proxy"] = lease.http_proxy
                    super().__init__(config, provider_settings)
                    self.client = ProxySession(self.client)

            ProxyManagedSessionProvider.__name__ = "ProxyManagedProvider_" + provider_type
            ProxyManagedSessionProvider.__qualname__ = ProxyManagedSessionProvider.__name__
            return ProxyManagedSessionProvider

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
        for provider_type, module_name in PROVIDER_MODULES.items():
            try:
                self._install_provider(provider_type, module_name)
            except Exception as exc:
                self.report.providers[provider_type] = {
                    "state": "unsupported", "coverage": PROVIDER_ADAPTER_MAP[provider_type].coverage,
                    "message": "Provider 适配不可用：" + type(exc).__name__ + "；保持原类",
                }

    def _install_provider(self, provider_type, module_name):
        from astrbot.core.provider.register import provider_cls_map

        spec = PROVIDER_ADAPTER_MAP[provider_type]
        if spec.proxy_mode == "unverified":
            self.report.providers[provider_type] = {
                "state": "unknown",
                "proxy_mode": spec.proxy_mode,
                "verification": spec.verification,
                "sample": request_sample(provider_type),
                "coverage": spec.coverage,
                "message": spec.coverage + "；保持原类，不宣称外部下载已接入",
            }
            return
        mismatches = self._dependency_issues(spec.requirements)
        if mismatches:
            self.report.providers[provider_type] = {
                "state": "unsupported", "coverage": spec.coverage,
                "message": "Provider SDK 未匹配审计版本：" + ", ".join(mismatches),
            }
            return
        try:
            importlib.import_module(module_name)
        except (ImportError, ModuleNotFoundError) as exc:
            self.report.providers[provider_type] = {
                "state": "unsupported",
                "message": f"官方 Provider 模块不可用：{type(exc).__name__}",
            }
            return
        metadata = provider_cls_map.get(provider_type)
        if metadata is None or not getattr(metadata, "cls_type", None):
            self.report.providers[provider_type] = {
                "state": "unsupported",
                "message": "Provider 注册表未提供可替换的类",
            }
            return
        base = getattr(metadata.cls_type, "_proxy_manager_base", metadata.cls_type)
        try:
            wrapped = self._provider_wrapper(provider_type, base)
        except (AttributeError, KeyError, ValueError, TypeError):
            self.report.providers[provider_type] = {
                "state": "unsupported", "coverage": spec.coverage,
                "message": "Provider 方法指纹与审计源码不匹配；保持原类",
            }
            return
        self._original_provider_classes[provider_type] = base
        metadata.cls_type = wrapped
        self.report.providers[provider_type] = {
            "state": "partial" if spec.proxy_mode == "partial" else "installed",
            "proxy_mode": spec.proxy_mode,
            "verification": spec.verification,
            "coverage": spec.coverage,
            "sample": request_sample(provider_type),
            "message": spec.coverage + "；已注入稳定入口的路径仍需请求级证据，未覆盖路径不作承诺",
        }

    async def install(self) -> CompatibilityReport:
        if self._installed:
            return self.report
        if self.report.state == "unsupported":
            return self.report
        try:
            self._install_platforms()
            self._install_providers()
            self._installed = True
            await self._reload_live_components()
            items = [*self.report.platforms.values(), *self.report.providers.values()]
            installed = sum(item.get("state") in {"installed", "partial"} for item in items)
            blocked = sum(item.get("state") == "unsupported" or item.get("reload_state") in {"failed", "timeout"}
                          for item in items)
            self.report.state = ("partial" if blocked else "installed") if installed else "failed"
            self.report.message = f"已为 {installed} 个组件安装兼容层；{blocked} 个组件依赖不匹配或重载待处理，请查看各组件诊断"
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
                if self.report.platforms.get(config.get("type"), {}).get("state") == "installed":
                    instance_info = getattr(platform_manager, "_inst_map", {}).get(config.get("id"))
                    instance = instance_info.get("inst") if isinstance(instance_info, dict) else None
                    current_lease = getattr(instance, "_proxy_manager_lease", None)
                    desired_lease = self.lease.for_component("platform:" + config["type"])
                    if current_lease and (
                        current_lease.http_proxy == desired_lease.http_proxy
                        and current_lease.socks_proxy == desired_lease.socks_proxy
                    ):
                        continue
                    try:
                        await asyncio.wait_for(
                            platform_manager.reload(config),
                            timeout=LIVE_COMPONENT_RELOAD_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        self.report.platforms[config["type"]].update(
                            reload_state="timeout", message="代理兼容层已安装；平台重载超时，需平台自行重连或重启 AstrBot")
                    except Exception as exc:
                        self.report.platforms[config["type"]].update(
                            reload_state="failed", message="代理兼容层已安装；平台重载失败：" + type(exc).__name__ + "，需检查平台或重启 AstrBot")
        provider_manager = getattr(self.context, "provider_manager", None)
        if provider_manager is not None and getattr(provider_manager, "provider_insts", None):
            configs = getattr(provider_manager, "providers_config", [])
            for config in configs:
                if self.report.providers.get(config.get("type"), {}).get("state") in {"installed", "partial"}:
                    try:
                        await asyncio.wait_for(
                            provider_manager.reload(config),
                            timeout=LIVE_COMPONENT_RELOAD_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        self.report.providers[config["type"]].update(
                            reload_state="timeout", message="Provider 兼容层已安装；实例重载超时，需重启后生效")
                    except Exception as exc:
                        self.report.providers[config["type"]].update(
                            reload_state="failed", message="Provider 兼容层已安装；实例重载失败：" + type(exc).__name__ + "，需检查 Provider 或重启 AstrBot")

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
