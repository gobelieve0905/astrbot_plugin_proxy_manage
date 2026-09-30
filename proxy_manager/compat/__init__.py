"""Versioned AstrBot component compatibility layer."""

from .lease import ComponentLease
from .registry import (
    SUPPORTED_ASTRBOT,
    SUPPORTED_ASTRBOT_VERSIONS,
    SUPPORTED_PLATFORM_TYPES,
    CompatibilityManager,
    CompatibilityReport,
)

__all__ = [
    "ComponentLease",
    "CompatibilityManager",
    "CompatibilityReport",
    "SUPPORTED_ASTRBOT",
    "SUPPORTED_ASTRBOT_VERSIONS",
    "SUPPORTED_PLATFORM_TYPES",
]
