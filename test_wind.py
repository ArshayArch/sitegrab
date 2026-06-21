"""Tests for the wind DATA half (analysis.wind_data) — pure, no network.

Checks the sector binning and rose aggregation against hand-constructed samples,
plus the fallback's invariants. Run: ``python test_wind.py``.
"""

from __future__ import annotations

from fetch_core import get_transformer

from analysis.framework import Building, SiteContext
from analysis.wind import (
    CHANNEL_MAX_WIDTH_M,
    _flow_vec,
    build_geometry,
)
from analysis.wind_data import (
    COMPASS_16,
    SECTORS,
    WindRose,
    _aggregate,
    _fallback_rose,
    _sector_of,
)


def test_sector_boundaries() -> None:
    assert _sector_of(0.0) == 0      # due N
    assert _sector_of(360.0) == 0
    assert _sector_of(11.24) == 0    # within half a sector of N
    assert _sector_of(11.26) == 1    # just past -> NNE
    assert _sector_of(90.0) == 4     # E
    assert _sector_of(180.0) == 8    # S
    assert _sector_of(270.0) == 12   # W


def test_aggregate_frequency_and_calm() -> None:
    # 6 hours from due west (270), 2 from due east (90), 2 calm.
    dirs = [270] * 6 + [90] * 2 + [0, 0]
    speeds = [8.0] * 6 + [4.0] * 2 + [0.1, 0.2]
    freq, mean, maxs, calm_frac, total = _aggregate(dirs, speeds)
    assert total == 10
    assert abs(calm_frac - 0.2) < 1e-9          # 2 of 10 hours calm
    assert abs(sum(freq) - 1.0) < 1e-9          # freq over the 8 non-calm hours
    w, e = _sector_of(270), _sector_of(90)
    assert abs(freq[w] - 6 / 8) < 1e-9
    assert abs(freq[e] - 2 / 8) < 1e-9
    assert abs(mean[w] - 8.0) < 1e-9
    assert maxs[w] == 8.0


def test_aggregate_skips_nulls() -> None:
    dirs = [270, None, 270]
    speeds = [5.0, 5.0, None]
    freq, mean, maxs, calm_frac, total = _aggregate(dirs, speeds)
    assert total == 1                            # only the first row is complete


def test_fallback_invariants() -> None:
    for lat in (10.0, 45.0, -45.0, 75.0):
        r = _fallback_rose(lat)
        assert r.is_fallback
        assert len(r.freq) == SECTORS
        assert abs(sum(r.freq) - 1.0) < 1e-9
        assert max(r.strength) == 1.0            # normalised


# --- representation half (geometry), all synthetic / no network ---------------
S, W, N, E = 51.50, -0.13, 51.51, -0.11


def _forced_rose(direction: str) -> WindRose:
    freq = [0.0] * SECTORS
    freq[COMPASS_16.index(direction)] = 1.0
    return WindRose(labels=COMPASS_16, freq=freq, mean_speed=[8.0] * SECTORS,
                    max_speed=[12.0] * SECTORS, calm_fraction=0.0, hours=1,
                    source="synthetic", period="test", is_fallback=False)


def _ctx(buildings: list[Building]) -> SiteContext:
    tr, epsg = get_transformer(S, W, N, E)
    ctx = SiteContext(south=S, west=W, north=N, east=E, transformer=tr,
                      epsg=epsg, display_name="wind test")
    ctx.buildings = buildings
    return ctx


def _two_blocks(slot_m: float) -> list[Building]:
    tr, _ = get_transformer(S, W, N, E)
    cx, cy = tr.transform((W + E) / 2, (S + N) / 2)
    h = slot_m / 2
    north = [(cx - 60, cy + h), (cx + 60, cy + h),
             (cx + 60, cy + h + 40), (cx - 60, cy + h + 40)]
    south = [(cx - 60, cy - h - 40), (cx + 60, cy - h - 40),
             (cx + 60, cy - h), (cx - 60, cy - h)]
    return [Building(north, 20.0, 0.0, True), Building(south, 20.0, 0.0, True)]


def test_channel_flagged_in_narrow_slot() -> None:
    built = build_geometry(_ctx(_two_blocks(10.0)), _forced_rose("W"),
                           {"show_secondary": False})
    assert built.meta["prevailing_dir"] == "W"
    assert built.meta["channels"] >= 1, "narrow slot not flagged as a channel"


def test_no_channel_when_gap_too_wide() -> None:
    # A gap far wider than CHANNEL_MAX_WIDTH_M is a plaza, not a funnel.
    built = build_geometry(_ctx(_two_blocks(CHANNEL_MAX_WIDTH_M + 40)),
                           _forced_rose("W"), {"show_secondary": False})
    assert built.meta["channels"] == 0, "an over-wide gap was wrongly channelled"


def test_arrow_stops_at_facade() -> None:
    # One solid block dead-centre; a west wind's arrows must not punch through to
    # the far (east) side — every prevailing arrow ends at/short of the east wall.
    tr, _ = get_transformer(S, W, N, E)
    cx, cy = tr.transform((W + E) / 2, (S + N) / 2)
    block = [(cx - 50, cy - 50), (cx + 50, cy - 50),
             (cx + 50, cy + 50), (cx - 50, cy + 50)]
    built = build_geometry(_ctx([Building(block, 30.0, 0.0, True)]),
                           _forced_rose("W"), {"show_secondary": False})
    fx, fy = _flow_vec(COMPASS_16.index("W") * 22.5, 0.0)  # ~ +x (eastward)
    blocked = 0
    for ar in built.arrows:
        if ar.layer != "arrows_prevailing":
            continue
        sx, sy, _ = ar.pts[0]
        ex, ey, _ = ar.pts[1]
        # Arrows seeded within the block's N-S span must stop before the east wall.
        if cy - 50 < sy < cy + 50 and sx < cx - 50:
            assert ex <= cx - 50 + 2.0, "arrow passed through a solid facade"
            blocked += 1
    assert blocked > 0, "no arrow tested against the central block"


if __name__ == "__main__":
    test_sector_boundaries()
    test_aggregate_frequency_and_calm()
    test_aggregate_skips_nulls()
    test_fallback_invariants()
    test_channel_flagged_in_narrow_slot()
    test_no_channel_when_gap_too_wide()
    test_arrow_stops_at_facade()
    print("all wind tests passed")
