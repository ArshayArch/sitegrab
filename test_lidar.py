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
    """A 10x10 m window: flat DTM at 5 m, DSM = DTM + height over a central
    4x4 block, returns a LidarHeights over BNG (0,0,10,10) at 1 m."""
    dtm = np.full((10, 10), 5.0, dtype=np.float32)
    dsm = dtm.copy()
    dsm[3:7, 3:7] += height_block
    return LidarHeights(dsm, dtm, (0.0, 0.0, 10.0, 10.0), 1.0)


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
    lid.dsm[:] = -3.4e38                   # whole window nodata
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
