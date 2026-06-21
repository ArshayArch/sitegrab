"""Wind Analysis — the DATA half. A MEASURED wind rose, no geometry libraries.

Data source: **Open-Meteo Historical Weather (ERA5 reanalysis) archive API** —
free, keyless, no billing, no rate-limit registration. Given a lat/long it
returns hourly ``wind_speed_10m`` (m/s) and ``wind_direction_10m`` (degrees,
meteorological convention = the direction the wind blows FROM) over a multi-year
window, which we aggregate into a wind rose: per-sector frequency, mean and max
speed. This module imports no ``rhino3dm``/``ezdxf`` and is unit-testable.

HONESTY. This is climatology, not live wind and NOT a flow simulation. The rose
is a real, measured statistical distribution of where wind comes from and how
hard, aggregated from reanalysis over several years — the right, honest basis
for an *indicative* wind diagram. The representation half turns it into arrows;
it never implies CFD.

Wind direction is METEOROLOGICAL: 0deg = FROM north, 90deg = FROM east. A
"prevailing" sector is the compass direction the wind most often comes from.

Fallback: if the API can't be reached after retries, a coarse latitude-band
global-circulation climatology is returned, clearly flagged as generic and
NOT site-specific, so the diagram degrades honestly rather than failing blank.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from fetch_core import UA

# 16-point compass: fine enough to read a prevailing direction, coarse enough
# to stay legible as arrows on a plan.
SECTORS = 16
SECTOR_DEG = 360.0 / SECTORS
COMPASS_16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

# Hours with speed below this are "calm" — counted separately, not binned into a
# direction (a near-zero wind has no meaningful direction).
CALM_MS = 0.5

# Aggregation window: full calendar years ending at the last COMPLETE year, so we
# never hit the archive's few-day lag near 'today'. Three years balances a stable
# climatology against a small, fast JSON payload (~3 x 8760 hourly rows).
WINDOW_YEARS = 3

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


@dataclass
class WindRose:
    """A measured directional wind distribution at a site.

    All per-sector lists are length :data:`SECTORS`, indexed N, NNE, NE, ... so
    ``labels[i]`` names the direction the wind comes FROM. ``freq`` sums to ~1.0
    over the non-calm hours.
    """

    labels: tuple[str, ...]
    freq: list[float]        # fraction of non-calm hours from this sector
    mean_speed: list[float]  # mean speed (m/s) of hours from this sector
    max_speed: list[float]   # peak gust-free hourly speed (m/s) from this sector
    calm_fraction: float     # fraction of all hours below CALM_MS
    hours: int               # total hours aggregated
    source: str              # human description of the data origin
    period: str              # e.g. "2023-2025"
    is_fallback: bool        # True if the coarse generic climatology was used

    @property
    def prevailing_index(self) -> int:
        """Sector the wind most often comes FROM (max frequency)."""
        return max(range(SECTORS), key=lambda i: self.freq[i])

    @property
    def strength(self) -> list[float]:
        """Per-sector arrow weight = frequency x mean speed (how much wind,
        how hard, from each direction) — normalised to its own max = 1.0."""
        raw = [self.freq[i] * self.mean_speed[i] for i in range(SECTORS)]
        peak = max(raw) or 1.0
        return [r / peak for r in raw]


def _sector_of(direction_deg: float) -> int:
    """Compass sector index (0=N) for a meteorological direction in degrees."""
    return int((direction_deg % 360.0 + SECTOR_DEG / 2) // SECTOR_DEG) % SECTORS


def _aggregate(dirs: list, speeds: list) -> tuple[list[float], list[float],
                                                  list[float], float, int]:
    """Bin parallel (direction, speed) hourly samples into the 16-sector rose."""
    counts = [0] * SECTORS
    sums = [0.0] * SECTORS
    maxs = [0.0] * SECTORS
    calm = 0
    total = 0
    for d, sp in zip(dirs, speeds):
        if d is None or sp is None:
            continue
        total += 1
        if sp < CALM_MS:
            calm += 1
            continue
        i = _sector_of(float(d))
        counts[i] += 1
        sums[i] += sp
        if sp > maxs[i]:
            maxs[i] = sp
    binned = total - calm
    freq = [c / binned if binned else 0.0 for c in counts]
    mean = [sums[i] / counts[i] if counts[i] else 0.0 for i in range(SECTORS)]
    calm_frac = calm / total if total else 0.0
    return freq, mean, maxs, calm_frac, total


def _fetch_archive(lat: float, lon: float, start: dt.date,
                   end: dt.date) -> dict:
    """GET the Open-Meteo archive with exponential backoff (Overpass-style)."""
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    })
    last: str | None = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(f"{ARCHIVE_URL}?{q}", headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode()
            data = json.loads(txt)
            if "hourly" in data:
                return data
            last = data.get("reason", "no hourly block in response")
        except Exception as ex:  # noqa: BLE001 - any network error is retryable
            last = str(ex)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Open-Meteo wind archive unavailable after retries: {last}")


def _fallback_rose(lat: float) -> WindRose:
    """Coarse, generic latitude-band climatology when the API is unreachable.

    A crude reflection of the global circulation cells (trade easterlies in the
    tropics, prevailing westerlies in the mid-latitudes, polar easterlies high)
    — emphatically NOT site-specific. Flagged so the diagram says so out loud.
    """
    a = abs(lat)
    if a < 30:                       # trade winds: wind FROM the east
        prevailing, label = "ENE" if lat >= 0 else "ESE", "trade easterlies"
        speed = 6.0
    elif a < 60:                     # mid-latitude prevailing westerlies
        prevailing, label = "WSW" if lat >= 0 else "WNW", "prevailing westerlies"
        speed = 7.0
    else:                            # polar easterlies
        prevailing, label = "ENE", "polar easterlies"
        speed = 5.0

    p = COMPASS_16.index(prevailing)
    # A simple lobed distribution: most weight at the prevailing sector, tapering
    # to the neighbours, a little background everywhere.
    weights = [0.04] * SECTORS
    for off, w in ((0, 0.34), (1, 0.16), (-1, 0.16), (2, 0.06), (-2, 0.06)):
        weights[(p + off) % SECTORS] += w
    tot = sum(weights)
    freq = [w / tot for w in weights]
    mean = [speed * (0.6 + 0.6 * (freq[i] / max(freq))) for i in range(SECTORS)]
    return WindRose(
        labels=COMPASS_16, freq=freq, mean_speed=mean,
        max_speed=[m * 2.2 for m in mean], calm_fraction=0.05, hours=0,
        source=f"GENERIC latitude-band climatology ({label}) — Open-Meteo was "
               f"unreachable; NOT site-specific, indicative pattern only.",
        period="n/a (fallback)", is_fallback=True)


def fetch_wind_rose(lat: float, lon: float,
                    years: int = WINDOW_YEARS) -> WindRose:
    """Measured 16-sector wind rose for the site, with an honest fallback.

    Aggregates ``years`` full calendar years of hourly Open-Meteo reanalysis
    wind into per-sector frequency / mean / max speed. On network failure returns
    :func:`_fallback_rose`, flagged ``is_fallback=True``.
    """
    end_year = dt.date.today().year - 1
    start = dt.date(end_year - years + 1, 1, 1)
    end = dt.date(end_year, 12, 31)
    try:
        data = _fetch_archive(lat, lon, start, end)
    except RuntimeError:
        return _fallback_rose(lat)

    hourly = data["hourly"]
    dirs = hourly.get("wind_direction_10m", [])
    speeds = hourly.get("wind_speed_10m", [])
    freq, mean, maxs, calm_frac, total = _aggregate(dirs, speeds)
    if total == 0:                    # API returned but empty -> degrade honestly
        return _fallback_rose(lat)
    return WindRose(
        labels=COMPASS_16, freq=freq, mean_speed=mean, max_speed=maxs,
        calm_fraction=calm_frac, hours=total,
        source="Open-Meteo Historical Weather archive (ERA5 reanalysis), "
               "10 m wind — free, keyless. Aggregated to a 16-sector rose.",
        period=f"{start.year}-{end.year}", is_fallback=False)


if __name__ == "__main__":
    # Sanity check: known windy coastal mid-latitude sites should show a
    # westerly prevailing direction; a trade-wind site should show easterly.
    for lat, lon, place, expect in [
        (55.20, -6.50, "Causeway Coast, N.Ireland", "W-ish"),
        (51.50, -0.12, "London", "W/SW-ish"),
        (21.31, -157.86, "Honolulu (trades)", "E/NE-ish"),
    ]:
        rose = fetch_wind_rose(lat, lon)
        p = rose.prevailing_index
        top3 = sorted(range(SECTORS), key=lambda i: rose.freq[i], reverse=True)[:3]
        print(f"\n{place}  (expect {expect})  [{rose.period}]"
              f"{'  FALLBACK' if rose.is_fallback else ''}")
        print(f"  prevailing FROM {rose.labels[p]} "
              f"({rose.freq[p]*100:.0f}% of non-calm hours, "
              f"mean {rose.mean_speed[p]:.1f} m/s)")
        print(f"  top sectors: "
              + ", ".join(f"{rose.labels[i]} {rose.freq[i]*100:.0f}%"
                          for i in top3)
              + f"   calm {rose.calm_fraction*100:.0f}%   hours {rose.hours}")
