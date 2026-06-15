"""Tests for cast shadows (analysis.shadows) on a synthetic building.

No network: one 10x10 m box, shadows checked for the two invariants that make a
shadow study trustworthy — direction (away from the sun) and length (longer when
the sun is low). Run: ``python test_shadows.py``.
"""

from __future__ import annotations

import math

from fetch_core import get_transformer

from analysis.framework import Building, SiteContext
from analysis.shadows import _shadow_offset, cast_shadows
from analysis.solar import sun_at, sun_tracks

# A site near Bristol; one box centred on the projected centroid.
S, W, N, E = 51.452, -2.633, 51.468, -2.605


def _ctx_with_box() -> SiteContext:
    tr, epsg = get_transformer(S, W, N, E)
    lon, lat = (W + E) / 2, (S + N) / 2
    cx, cy = tr.transform(lon, lat)
    box = [(cx - 5, cy - 5), (cx + 5, cy - 5), (cx + 5, cy + 5), (cx - 5, cy + 5)]
    ctx = SiteContext(south=S, west=W, north=N, east=E, transformer=tr,
                      epsg=epsg, display_name="box test")
    ctx.buildings = [Building(pts=box, height=10.0, base=0.0,
                              height_estimated=True)]
    return ctx


def test_shadow_points_away_from_sun() -> None:
    """At solar noon (sun due south, N hemisphere) the shadow falls NORTH."""
    ctx = _ctx_with_box()
    tracks = sun_tracks((S + N) / 2, (W + E) / 2)
    polys, meta = cast_shadows(ctx, tracks, {"shadow_times": ["12:00"]},
                               (0, 0, 0), 100.0, 0.0)
    flat = [p for suffix, _, p in polys if not suffix.startswith("draped")]
    assert flat, "no flat shadows produced"
    cx = (W + E) / 2
    tr = ctx.transformer
    box_cy = tr.transform(cx, (S + N) / 2)[1]
    # Every summer-noon shadow polygon should reach north of the footprint.
    for poly in flat[0]:
        assert max(y for _, y, _ in poly) > box_cy + 5, "shadow not cast north"


def test_lower_sun_casts_longer_shadow() -> None:
    lat, lon = (S + N) / 2, (W + E) / 2
    summer_noon = sun_at(lat, lon, __import__("datetime").date(2026, 6, 21), 12.0)
    winter_morning = sun_at(lat, lon, __import__("datetime").date(2026, 12, 21), 9.0)
    assert winter_morning.altitude < summer_noon.altitude
    hi = _shadow_offset(10.0, summer_noon.azimuth, summer_noon.altitude, 0.0)
    lo = _shadow_offset(10.0, winter_morning.azimuth, winter_morning.altitude, 0.0)
    assert math.hypot(*lo) > math.hypot(*hi), "low sun did not cast a longer shadow"
    # A 10 m box at ~14 deg winter sun casts a shadow well over its own height.
    assert math.hypot(*lo) > 10.0


def test_high_sun_short_shadow_length_matches_formula() -> None:
    # shadow length = height / tan(altitude); check the offset magnitude.
    off = _shadow_offset(10.0, 180.0, 45.0, 0.0)
    assert abs(math.hypot(*off) - 10.0) < 1e-6  # tan(45)=1 -> length == height


if __name__ == "__main__":
    test_shadow_points_away_from_sun()
    test_lower_sun_casts_longer_shadow()
    test_high_sun_short_shadow_length_matches_formula()
    print("all shadow tests passed")
