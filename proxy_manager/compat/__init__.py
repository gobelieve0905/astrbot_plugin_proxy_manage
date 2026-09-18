"""Versioned AstrBot component compatibility layer."""

from .lease import ComponentLease
from .registry import CompatibilityManager, CompatibilityReport, SUPPORTED_ASTRBOT

__all__ = [
    "ComponentLease",
    "CompatibilityManager",
    "CompatibilityReport",
    "SUPPORTED_ASTRBOT",
]
