"""Versioned AstrBot component compatibility layer."""

from .lease import ComponentLease
from .registry import (
    CompatibilityManager,
    CompatibilityReport,
    SUPPORTED_ASTRBOT,
    SUPPORTED_ASTRBOT_VERSIONS,
)

__all__ = [
    "ComponentLease",
    "CompatibilityManager",
    "CompatibilityReport",
    "SUPPORTED_ASTRBOT",
    "SUPPORTED_ASTRBOT_VERSIONS",
]
