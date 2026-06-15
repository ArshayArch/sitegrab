"""Tests for the Sun Path DATA layer (analysis.solar).

The data half is pure numbers, so it is checked against the exact astronomical
identity with zero geometry: solar-noon altitude is ``90 - |lat|`` at the
equinox and ``+/- 23.44`` at the solstices. Run: ``python test_solar.py``.
"""

from __future__ import annotations

from analysis.solar import AXIAL_TILT_DEG, key_dates, sun_tracks

# astral's sun position is good to a fraction of a degree; the noon identity
# itself uses a fixed mean tilt, so allow a degree of slack.
TOL = 1.0

CASES = [
    (51.48, 0.0, "Greenwich (N)"),
    (25.08, 55.14, "Dubai (N, near-tropical)"),
    (-33.87, 151.21, "Sydney (S)"),
    (1.35, 103.82, "Singapore (equatorial)"),
]


def test_noon_altitude_identity() -> None:
    # Exact identity: solar-noon altitude = 90 - |lat - declination|. The
    # simplified "90 - lat +/- 23.5" overshoots past 90 near the equator (the
    # sun passes north of the zenith), so use the general form. Declination at
    # the local summer solstice points toward the site's own hemisphere.
    for lat, lon, name in CASES:
        hemi = 1.0 if lat >= 0 else -1.0
        for tr in sun_tracks(lat, lon, 2026):
            decl = {"summer_solstice": hemi * AXIAL_TILT_DEG, "equinox": 0.0,
                    "winter_solstice": -hemi * AXIAL_TILT_DEG}[tr.key]
            expect = 90.0 - abs(lat - decl)
            assert abs(tr.noon_altitude - expect) < TOL, (
                f"{name} {tr.key}: noon_alt {tr.noon_altitude:.2f} "
                f"!= {expect:.2f}")


def test_summer_higher_and_longer_than_winter() -> None:
    """Summer sun must be higher at noon and the day longer than winter — the
    single most important sanity check for a sun-path output."""
    for lat, lon, name in CASES:
        tracks = {t.key: t for t in sun_tracks(lat, lon, 2026)}
        s, w = tracks["summer_solstice"], tracks["winter_solstice"]
        assert s.noon_altitude > w.noon_altitude, f"{name}: summer not higher"
        assert s.day_length_h > w.day_length_h, f"{name}: summer not longer"
        assert 0.0 <= s.day_length_h <= 24.0, f"{name}: bad summer day length"
        assert 0.0 <= w.day_length_h <= 24.0, f"{name}: bad winter day length"


def test_hemisphere_aware_labels() -> None:
    """'Summer' must name the June solstice north of the equator and December
    south of it — so the label is true at the site."""
    north = dict((k, d) for k, _, d in key_dates(51.0, 2026))
    south = dict((k, d) for k, _, d in key_dates(-33.0, 2026))
    assert north["summer_solstice"].month == 6
    assert north["winter_solstice"].month == 12
    assert south["summer_solstice"].month == 12
    assert south["winter_solstice"].month == 6


def test_azimuth_in_range_and_arc_above_horizon() -> None:
    for lat, lon, name in CASES:
        for tr in sun_tracks(lat, lon, 2026):
            for s in tr.arc:
                assert 0.0 <= s.azimuth <= 360.0, f"{name}: az out of range"
                assert s.altitude > 0.0, f"{name}: arc point below horizon"
            for h in tr.hourly:
                assert s.altitude > 0.0


if __name__ == "__main__":
    test_noon_altitude_identity()
    test_summer_higher_and_longer_than_winter()
    test_hemisphere_aware_labels()
    test_azimuth_in_range_and_arc_above_horizon()
    print("all solar tests passed")
