"""Sun Path — cast shadows (representation). Implemented in Phase 3.

Flat-ground projection of each building's silhouette along the sun direction is
the required baseline; terrain-draped shadows are the stretch with fallback.
Until Phase 3 this returns no shadows so arcs/points ship on their own.
"""

from __future__ import annotations

from typing import Any

from .framework import SiteContext
from .solar import DayTrack


def cast_shadows(
    ctx: SiteContext,
    tracks: list[DayTrack],
    params: dict[str, Any],
    centre: tuple[float, float, float],
    radius: float,
    theta: float,
) -> list[tuple[str, str, list[list[tuple[float, float, float]]]]]:
    """Return [(layer_suffix, color_key, polygons)]; empty until Phase 3."""
    return []
