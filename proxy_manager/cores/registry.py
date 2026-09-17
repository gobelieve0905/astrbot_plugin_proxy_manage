from __future__ import annotations

from ..domain.constants import HTTP_PROTOCOLS
from .base import UnsupportedAdapter

_ADAPTERS=None


def _load():
    global _ADAPTERS
    if _ADAPTERS is None:
        from .mihomo import MihomoAdapter
        from .sing_box import SingBoxAdapter
        _ADAPTERS={'mihomo':MihomoAdapter(),'sing-box':SingBoxAdapter()}
    return _ADAPTERS


def all_adapters() -> dict: return dict(_load())


def adapters_for(protocol: str) -> list[str]:
    protocol=str(protocol or '').lower()
    if protocol=='socks': protocol='socks5'
    return [key for key,value in _load().items() if protocol in value.capabilities().get('protocols',set())]


def default_executor(protocol: str) -> str:
    protocol=str(protocol or '').lower()
    if protocol in HTTP_PROTOCOLS: return 'direct-http'
    matched=adapters_for(protocol)
    return matched[0] if matched else ''


def current_adapter(state: dict|None=None):
    requested='mihomo'
    if isinstance(state,dict): requested=str((state.get('control') or {}).get('adapter') or 'mihomo')
    return _load().get(requested) or UnsupportedAdapter(requested)
