"""Tests for the wind DATA half (analysis.wind_data) — pure, no network.

Checks the sector binning and rose aggregation against hand-constructed samples,
plus the fallback's invariants. Run: ``python test_wind.py``.
"""

from __future__ import annotations

from analysis.wind_data import (
    SECTORS,
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


if __name__ == "__main__":
    test_sector_boundaries()
    test_aggregate_frequency_and_calm()
    test_aggregate_skips_nulls()
    test_fallback_invariants()
    print("all wind-data tests passed")
