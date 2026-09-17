"""AstrBot 代理管理中心入口。实现位于 proxy_manager 包，保持市场插件加载约定。"""
from __future__ import annotations

import httpx
from astrbot.api.star import StarTools, register

if __package__:
    from .proxy_manager.domain.constants import (
        ADVANCED_SCHEMES, CONFIGURED, DIRECT, MATCHES, MODES, SUPPORTED_PROTOCOLS, TEMPLATES,
    )
    from .proxy_manager.domain.identity import (
        canonical_connection, identity_material, infer_protocol, protocol_support, region_of, suspected_notice,
    )
    from .proxy_manager.domain.security import (
        ident, redact_config, redact_diagnostics, restore_config, safe_error, safe_host, safe_proxy_endpoint, safe_url,
    )
    from .proxy_manager.plugin import ProxyManager as _ProxyManager
else:
    from proxy_manager.domain.constants import (
        ADVANCED_SCHEMES, CONFIGURED, DIRECT, MATCHES, MODES, SUPPORTED_PROTOCOLS, TEMPLATES,
    )
    from proxy_manager.domain.identity import (
        canonical_connection, identity_material, infer_protocol, protocol_support, region_of, suspected_notice,
    )
    from proxy_manager.domain.security import (
        ident, redact_config, redact_diagnostics, restore_config, safe_error, safe_host, safe_proxy_endpoint, safe_url,
    )
    from proxy_manager.plugin import ProxyManager as _ProxyManager


@register('astrbot_plugin_proxy_manage','gobelieve','AstrBot 统一出站流量控制面','0.3.1')
class ProxyManager(_ProxyManager):
    pass
