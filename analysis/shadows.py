"""Sun Path — cast building shadows (the differentiator), representation half.

DATA stays in :mod:`analysis.solar` (sun azimuth/altitude); this is pure
geometry. Two readings:

* **Flat-ground (required baseline).** Each building is treated as a prism on
  the horizontal ground plane at the site datum; its shadow for a sun position
  is the convex hull of the footprint and the footprint translated by the
  ground-projection of the roof (offset = height / tan(altitude), pointing away
  from the sun). Exact for convex footprints (most blocks); a slight
  over-approximation for concave/L-plans — the same honest trade the massing
  makes, and noted in the output.

* **Terrain-draped (stretch, with fallback).** When requested and a DEM grid is
  present, the flat shadow outline is additionally DRAPED onto the terrain
  (each outline vertex lifted to the sampled ground height) on a separate
  ``shadow_draped_*`` layer set. This is a drape of the flat outline, not a true
  ray-vs-terrain intersection — documented as such. If it can't be built
  (no grid) the flat baseline simply stands alone.

Free-tier discipline: shadows scale as buildings x sun-positions, so on large
sites only the tallest ``SHADOW_MAX_BUILDINGS`` (the dominant shadow casters)
are cast and the cap is reported — never a silent drop.
"""

from __future__ import annotations

import math
from typing import Any

from pyproj.enums import TransformDirection

from .framework import Building, SiteContext
from .solar import sun_at

# Sun below this altitude casts effectively unbounded shadows; skip + report.
MIN_SHADOW_ALTITUDE_DEG = 3.0

# Cap on shadow casters per site (tallest kept). Bounds file size + memory on
# the stress case while keeping the meaningful (tall) shadows. Measured: at 3000
# casters Shoreditch peaked 275MB (less than half the 512MB tier), so the cap is
# set by FILE SIZE, not memory — 6000 leaves typical neighbourhood sites
# (a few thousand buildings) fully cast and only caps a true megasite, which is
# reported. Uncapped full shadows are the natural Pro-tier feature (see v7 cycle).
SHADOW_MAX_BUILDINGS = 6000

# Which key dates get shadows: the solstices bound the year's shadow range.
_SHADOW_DATE_KEYS = ("summer_solstice", "winter_solstice")


def _parse_hour(t: str) -> float | None:
    """'09:00' / '9' -> 9.0 local solar hours, or None if unparseable."""
    try:
        if ":" in t:
            h, m = t.split(":", 1)
            return int(h) + int(m) / 60.0
        return float(t)
    except (ValueError, TypeError):
        return None


def _convex_hull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone-chain convex hull (CCW, no repeated endpoint)."""
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _shadow_offset(height: float, az_deg: float, alt_deg: float,
                   theta: float) -> tuple[float, float]:
    """Ground offset (dx, dy) of a roof point of ``height`` away from the sun."""
    bearing = math.radians(az_deg) + theta          # true az -> grid bearing
    reach = height / math.tan(math.radians(alt_deg))  # shadow length on flat
    return (-reach * math.sin(bearing), -reach * math.cos(bearing))


def _select_buildings(buildings: list[Building]) -> tuple[list[Building], int]:
    """Keep the tallest SHADOW_MAX_BUILDINGS; return (kept, skipped_count)."""
    if len(buildings) <= SHADOW_MAX_BUILDINGS:
        return buildings, 0
    ordered = sorted(buildings, key=lambda b: b.height, reverse=True)
    return ordered[:SHADOW_MAX_BUILDINGS], len(buildings) - SHADOW_MAX_BUILDINGS


def cast_shadows(
    ctx: SiteContext,
    tracks,
    params: dict[str, Any],
    centre: tuple[float, float, float],
    radius: float,
    theta: float,
) -> tuple[list[tuple[str, str, list[list[tuple[float, float, float]]]]],
           dict[str, Any]]:
    """Return (shadow_layers, meta).

    ``shadow_layers`` is ``[(layer_suffix, color_key, polygons)]``; ``meta``
    reports the caster count, any cap that engaged (so the drop is never
    silent), positions skipped for low sun, and whether draping was applied.
    """
    buildings = ctx.buildings or []
    meta: dict[str, Any] = {
        "shadow_casters": 0, "shadow_skipped_capped": 0,
        "shadow_building_cap": SHADOW_MAX_BUILDINGS,
        "shadow_positions_skipped_low_sun": 0, "draped": False,
    }
    if not buildings:
        return [], meta
    times = [h for h in (_parse_hour(t) for t in params.get("shadow_times") or [])
             if h is not None]
    if not times:
        return [], meta

    lon, lat = ctx.centroid
    datum = ctx.datum
    casters, skipped = _select_buildings(buildings)
    draped = bool(params.get("draped_shadows")) and ctx.grid is not None
    meta["shadow_casters"] = len(casters)
    meta["shadow_skipped_capped"] = skipped
    meta["draped"] = draped
    low_sun = 0

    inv = None
    if draped:
        def inv(x: float, y: float) -> tuple[float, float]:  # noqa: E306
            return ctx.transformer.transform(
                x, y, direction=TransformDirection.INVERSE)

    out: list[tuple[str, str, list[list[tuple[float, float, float]]]]] = []
    track_by_key = {t.key: t for t in tracks}
    for date_key in _SHADOW_DATE_KEYS:
        tr = track_by_key.get(date_key)
        if tr is None:
            continue
        season = "summer" if "summer" in date_key else "winter"
        for hour in times:
            s = sun_at(lat, lon, tr.date, hour)
            if s.altitude < MIN_SHADOW_ALTITUDE_DEG:
                low_sun += 1
                continue
            hhmm = f"{int(hour):02d}{int(round((hour % 1) * 60)):02d}"
            flat_polys: list[list[tuple[float, float, float]]] = []
            draped_polys: list[list[tuple[float, float, float]]] = []
            for b in casters:
                dx, dy = _shadow_offset(b.height, s.azimuth, s.altitude, theta)
                base_pts = b.pts
                hull = _convex_hull(base_pts + [(x + dx, y + dy)
                                                for x, y in base_pts])
                if len(hull) < 3:
                    continue
                flat_polys.append([(x, y, datum) for x, y in hull])
                if draped and inv is not None:
                    drp = []
                    for x, y in hull:
                        glon, glat = inv(x, y)
                        drp.append((x, y, ctx.grid.sample(glon, glat) + 0.05))
                    draped_polys.append(drp)
            if flat_polys:
                out.append((f"{season}_{hhmm}", season, flat_polys))
            if draped_polys:
                out.append((f"draped_{season}_{hhmm}", season, draped_polys))
    meta["shadow_positions_skipped_low_sun"] = low_sun
    return out, meta
