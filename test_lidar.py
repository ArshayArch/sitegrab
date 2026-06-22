"""Offline unit tests for fetch_lidar — the geometry/sampling logic, no network.

The WCS fetch itself is exercised live during the build benches; here we pin the
parts that must stay correct without a network: the coverage-envelope gate, the
polygon rasteriser, and the per-footprint height/percentile + nodata handling.
"""

from __future__ import annotations

import numpy as np

from fetch_lidar import (
    LidarHeights,
    _overlaps_england,
    _points_in_poly,
    fetch_lidar_heights,
)


def test_envelope_gate() -> None:
    # England (Shoreditch BNG ~533k, 182k) overlaps; France/Scotland-north do not.
    assert _overlaps_england(533000, 182000, 534000, 183000)
    assert not _overlaps_england(600000, 700000, 601000, 701000)   # too far N
    assert not _overlaps_england(-10000, 100000, -9000, 101000)    # W of origin


def test_non_england_skips_without_fetch() -> None:
    # Paris: returns immediately, available False, no exception.
    lid, info = fetch_lidar_heights(48.85, 2.34, 48.86, 2.35)
    assert lid is None
    assert info["available"] is False
    assert "estimated" in info["reason"].lower()


def test_points_in_poly_square() -> None:
    # Unit square [0,2]x[0,2]; centre in, far corner out.
    gx, gy = np.meshgrid(np.array([0.5, 1.0, 3.0]), np.array([1.0]))
    inside = _points_in_poly(gx, gy, [0, 2, 2, 0], [0, 0, 2, 2])
    assert inside.tolist() == [[True, True, False]]


def _synthetic(height_block: float):
    """A 10x10 m window of height-above-ground: 0 m everywhere except a central
    4x4 block at ``height_block``; LidarHeights over BNG (0,0,10,10) at 1 m."""
    hgt = np.zeros((10, 10), dtype=np.float32)
    hgt[3:7, 3:7] = height_block
    return LidarHeights(hgt, (0.0, 0.0, 10.0, 10.0), 1.0)


def test_height_for_reads_block(monkeypatch) -> None:
    lid = _synthetic(12.0)
    # Footprint covering the raised block, given as lon/lat -> patch the
    # transformer to be identity so we can use BNG coords directly.
    lid._tf = type("I", (), {"transform": staticmethod(lambda x, y: (x, y))})()
    h, n = lid.height_for([(3, 3), (7, 3), (7, 7), (3, 7)])
    assert n >= LidarHeights.MIN_VALID_PX
    assert abs(h - 12.0) < 0.5            # percentile of a flat block == block


def test_height_for_nodata_falls_back() -> None:
    lid = _synthetic(10.0)
    lid.hgt[:] = np.nan                    # whole window nodata
    lid._tf = type("I", (), {"transform": staticmethod(lambda x, y: (x, y))})()
    h, n = lid.height_for([(3, 3), (7, 3), (7, 7), (3, 7)])
    assert h is None and n == 0


def test_height_for_outside_window() -> None:
    lid = _synthetic(10.0)
    lid._tf = type("I", (), {"transform": staticmethod(lambda x, y: (x, y))})()
    h, n = lid.height_for([(50, 50), (60, 50), (60, 60), (50, 60)])
    assert h is None


# --- the per-building resolver + sanity check (build_rhino.resolve_height) -----
import build_rhino as rh  # noqa: E402

# A ~10x10 m (100 m^2) square footprint in metres.
_SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_resolve_osm_tag_always_wins() -> None:
    # A real OSM height beats any LiDAR value (never regress v5).
    h, p = rh.resolve_height({"building": "yes", "height": "30"}, _SQUARE, 1, 5.0)
    assert p == rh.PROV_OSM and h == 30.0


def test_resolve_uses_sane_lidar_over_estimate() -> None:
    h, p = rh.resolve_height({"building": "yes"}, _SQUARE, 1, 9.4)
    assert p == rh.PROV_LIDAR and h == 9.4


def test_resolve_rejects_too_low_lidar() -> None:
    h, p = rh.resolve_height({"building": "house"}, _SQUARE, 1, 0.4)
    assert p == rh.PROV_ESTIMATED       # 0.4 m is not a standing building


def test_resolve_rejects_slender_lidar() -> None:
    # 80 m on a 40 m^2 footprint: slenderness 80/6.3 = 12.7 -> rejected.
    tiny = [(0.0, 0.0), (6.3, 0.0), (6.3, 6.3), (0.0, 6.3)]
    h, p = rh.resolve_height({"building": "yes"}, tiny, 1, 80.0)
    assert p == rh.PROV_ESTIMATED


def test_resolve_rejects_tall_on_tiny_footprint() -> None:
    # 30 m reading on a 36 m^2 shed footprint = a tree/aerial, not a tower.
    shed = [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (0.0, 6.0)]
    h, p = rh.resolve_height({"building": "shed"}, shed, 1, 30.0)
    assert p == rh.PROV_ESTIMATED


def test_resolve_none_lidar_falls_back() -> None:
    h, p = rh.resolve_height({"building": "office"}, _SQUARE, 1, None)
    assert p == rh.PROV_ESTIMATED and h > 0


# --- the LiDAR memory governor (build_combined.lidar_budget_px) ----------------
from build_combined import (  # noqa: E402
    LIDAR_MAX_PX,
    LIDAR_MIN_PX,
    lidar_budget_px,
)


def test_governor_small_site_full_resolution() -> None:
    # A neighbourhood-scale site gets the full raster cap.
    assert lidar_budget_px(2000) == LIDAR_MAX_PX


def test_governor_megasite_skips() -> None:
    # The canonical Shoreditch megasite (13k buildings) is skipped (0 = fall
    # back to estimates rather than risk the 512 MB tier).
    assert lidar_budget_px(13023) == 0


def test_governor_is_monotonic_nonincreasing() -> None:
    # More buildings -> never a bigger LiDAR budget.
    vals = [lidar_budget_px(nb) for nb in range(1000, 14000, 1000)]
    assert all(b >= a for a, b in zip(vals[1:], vals[:-1]))
    # And whenever it's non-zero it's a usable size.
    assert all(v == 0 or v >= LIDAR_MIN_PX for v in vals)


# --- the provenance/fallback header (main._provenance_headers) -----------------
import json  # noqa: E402
import urllib.parse as _ul  # noqa: E402

from main import _provenance_headers  # noqa: E402


def _payload(stats: dict) -> dict:
    h = _provenance_headers(stats)
    return json.loads(_ul.unquote(h["X-SiteGrab-Heights"]))


def test_header_lidar_available_regime() -> None:
    p = _payload({"prov_osm": 199, "prov_lidar": 4431, "prov_estimated": 40,
                  "lidar": {"available": True, "reason": "England EA LiDAR."}})
    assert p["lidar_available"] and not p["lidar_governed"]
    assert p["prov_lidar"] == 4431


def test_header_governed_skip_regime() -> None:
    # Megasite: LiDAR skipped by the governor -> UI must be able to SAY so.
    p = _payload({"prov_osm": 4275, "prov_lidar": 0, "prov_estimated": 8749,
                  "lidar": {"available": False, "governed": True,
                            "reason": "LiDAR skipped to stay within budget."}})
    assert not p["lidar_available"] and p["lidar_governed"]


def test_header_no_coverage_regime() -> None:
    # Outside England: not available, NOT governed -> the third UI branch.
    p = _payload({"prov_osm": 415, "prov_lidar": 0, "prov_estimated": 1067,
                  "lidar": {"available": False,
                            "reason": "Outside England LiDAR coverage."}})
    assert not p["lidar_available"] and not p["lidar_governed"]


def test_header_absent_for_non_combined() -> None:
    # rhino/dxf-only requests build no combined model -> no provenance header.
    assert _provenance_headers(None) == {}
