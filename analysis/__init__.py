"""SiteGrab site-analysis framework package.

Re-exports the framework contract and the registry. Analysis modules
self-register on import; importing this package imports and registers them, so
``analysis.list_specs()`` returns the frontend's menu and ``analysis.get(key)``
resolves a module.
"""

from __future__ import annotations

from .framework import (
    BUILDINGS,
    LOCATION,
    TERRAIN,
    AnalysisResult,
    AnalysisSpec,
    Building,
    Param,
    SiteContext,
    get,
    list_specs,
    register,
)

# Modules self-register on import.
from . import sunpath  # noqa: E402,F401  (imported for the registration side effect)

__all__ = [
    "AnalysisResult", "AnalysisSpec", "Building", "Param", "SiteContext",
    "LOCATION", "BUILDINGS", "TERRAIN",
    "get", "list_specs", "register",
]
