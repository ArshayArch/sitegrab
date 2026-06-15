"""Sun Path — the DATA half. CALCULATED solar geometry, no geometry libraries.

Data source: solar position is **computed**, not fetched, with ``astral`` (pure
Python, free, keyless — no API, no billing, no rate limit). Given a latitude,
longitude and date it returns the sun's azimuth (degrees clockwise from TRUE
north) and altitude (degrees above the horizon) — plain numbers the
representation half (:mod:`analysis.sunpath`) turns into arcs, points and
shadows. This module imports no ``rhino3dm``/``ezdxf`` and is unit-testable
against the exact solstice-noon identity (see ``test_solar.py``).

Honesty: ``astral``'s sun position is good to a small fraction of a degree —
at a 250 m arc radius a 0.1° error is sub-centimetre — so the geometry is, for
site-analysis purposes, exact. Hour points are in **local solar (sundial)
time**, derived from longitude (mean solar time = UTC + lon/15 h); this differs
from civil/clock time by the timezone offset and the equation of time, and is
the right frame for a sun-path diagram (12:00 ≈ sun on the meridian).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from astral import Observer
from astral.sun import azimuth, elevation, sun

# Earth's axial tilt — used only to document/verify the noon identity, never in
# the position computation itself (astral does that exactly).
AXIAL_TILT_DEG = 23.44

# Arc sampling resolution (minutes of solar time). 10 min over a ~16 h summer
# day is ~96 points — a smooth curve, trivially light.
ARC_STEP_MIN = 10

# Hour points marked along each arc (local solar time, inclusive).
HOUR_START = 5
HOUR_END = 21


@dataclass
class SunSample:
    """The sun at one instant: a direction on the sky dome."""

    hour: float           # local solar time (decimal hours), for labels
    azimuth: float        # degrees, clockwise from TRUE north
    altitude: float       # degrees above the horizon (negative = below)


@dataclass
class DayTrack:
    """The sun's path across one key date at the site."""

    key: str              # "summer_solstice" | "winter_solstice" | "equinox"
    label: str            # human label, e.g. "Summer solstice"
    date: dt.date
    noon_altitude: float  # sun altitude at solar noon (the verification handle)
    day_length_h: float   # sunrise->sunset, hours (0 for polar night)
    arc: list[SunSample]      # fine samples, altitude > 0 (sunrise..sunset)
    hourly: list[SunSample]   # whole-hour marks, altitude > 0


def _mean_solar_offset_h(lon: float) -> float:
    """Hours to add to UTC for local mean (sundial) solar time at ``lon``."""
    return lon / 15.0


def _utc_at_solar_hour(date: dt.date, lon: float, solar_hour: float) -> dt.datetime:
    """UTC instant of a given local-solar-time hour on ``date``."""
    base = dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc)
    return base + dt.timedelta(hours=solar_hour - _mean_solar_offset_h(lon))


def sun_position(lat: float, lon: float, when_utc: dt.datetime) -> SunSample:
    """Azimuth/altitude of the sun at a UTC instant, with its local solar hour."""
    obs = Observer(latitude=lat, longitude=lon)
    alt = elevation(obs, when_utc)
    az = azimuth(obs, when_utc)
    solar_hour = (when_utc.hour + when_utc.minute / 60.0 + when_utc.second / 3600.0
                  + _mean_solar_offset_h(lon)) % 24.0
    return SunSample(hour=solar_hour, azimuth=az, altitude=alt)


def sun_at(lat: float, lon: float, date: dt.date, solar_hour: float) -> SunSample:
    """Sun azimuth/altitude at a given LOCAL SOLAR hour on ``date`` at the site.

    The frame the cast-shadow sampler uses (e.g. 09:00, 12:00, 15:00), kept
    consistent with the hourly arc marks.
    """
    return sun_position(lat, lon, _utc_at_solar_hour(date, lon, solar_hour))


def key_dates(lat: float, year: int) -> list[tuple[str, str, dt.date]]:
    """Hemisphere-aware (key, label, date) for the three sun-path key dates.

    "Summer"/"winter" name the high-/low-sun solstice at THIS latitude, so the
    labels stay true south of the equator (June is the low sun there).
    """
    june = dt.date(year, 6, 21)
    december = dt.date(year, 12, 21)
    equinox = dt.date(year, 3, 20)
    if lat >= 0:
        summer, winter = june, december
    else:
        summer, winter = december, june
    return [
        ("summer_solstice", "Summer solstice", summer),
        ("equinox", "Equinox", equinox),
        ("winter_solstice", "Winter solstice", winter),
    ]


def day_track(lat: float, lon: float, key: str, label: str, date: dt.date) -> DayTrack:
    """Compute the full sun path for one key date at the site."""
    obs = Observer(latitude=lat, longitude=lon)

    # Solar noon altitude — the clean verification handle (90 - |lat| ± tilt).
    try:
        events = sun(obs, date=date)
        noon = events["noon"]
        sunrise, sunset = events["sunrise"], events["sunset"]
        day_length_h = (sunset - sunrise).total_seconds() / 3600.0
        # Far-east/-west longitudes can return sunrise and sunset on opposite
        # sides of the UTC calendar date, inverting the difference; wrap it.
        if day_length_h < 0.0:
            day_length_h += 24.0
    except ValueError:
        # Polar day/night: astral raises when the sun never crosses the horizon.
        noon = _utc_at_solar_hour(date, lon, 12.0)
        day_length_h = 0.0
    noon_alt = elevation(obs, noon)

    # Arc: sample local solar time across the day; keep points above the horizon.
    arc: list[SunSample] = []
    minute = HOUR_START * 60
    while minute <= HOUR_END * 60:
        when = _utc_at_solar_hour(date, lon, minute / 60.0)
        s = sun_position(lat, lon, when)
        if s.altitude > 0.0:
            arc.append(s)
        minute += ARC_STEP_MIN

    # Whole-hour marks (local solar time), above the horizon.
    hourly: list[SunSample] = []
    for h in range(HOUR_START, HOUR_END + 1):
        when = _utc_at_solar_hour(date, lon, float(h))
        s = sun_position(lat, lon, when)
        if s.altitude > 0.0:
            hourly.append(SunSample(hour=float(h), azimuth=s.azimuth,
                                    altitude=s.altitude))

    return DayTrack(key=key, label=label, date=date, noon_altitude=noon_alt,
                    day_length_h=day_length_h, arc=arc, hourly=hourly)


def sun_tracks(lat: float, lon: float, year: int | None = None) -> list[DayTrack]:
    """The three key-date sun paths at the site (summer, equinox, winter)."""
    if year is None:
        year = dt.date.today().year
    return [day_track(lat, lon, key, label, date)
            for key, label, date in key_dates(lat, year)]


if __name__ == "__main__":
    # Sanity check against the exact identity: solar-noon altitude is
    # 90 - |lat| at equinox, +/- the axial tilt at the solstices.
    for lat, lon, place in [(51.48, 0.0, "Greenwich"),
                            (25.08, 55.14, "Dubai"),
                            (-33.87, 151.21, "Sydney")]:
        print(f"\n{place}  (lat {lat})")
        for tr in sun_tracks(lat, lon, 2026):
            expect = 90 - abs(lat) + {
                "summer_solstice": AXIAL_TILT_DEG,
                "equinox": 0.0,
                "winter_solstice": -AXIAL_TILT_DEG,
            }[tr.key]
            print(f"  {tr.label:16} noon_alt={tr.noon_altitude:5.1f}  "
                  f"expect~{expect:5.1f}  daylen={tr.day_length_h:4.1f}h  "
                  f"hours={len(tr.hourly)} arcpts={len(tr.arc)}")
