"""SiteGrab shared pipeline core.

Geocoding (Nominatim) + UTM zone detection + Overpass fetch with mirror
failover and exponential backoff. Mirror ordering is history-aware: a mirror
that failed recently is tried second, see ``_order_mirrors``. Shared by both
the Rhino and DXF writers.

The Nominatim bounding-box ordering and the UTM formula are subtle and easy
to get wrong; they are correct as written here and must not be changed.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from typing import Any

from pyproj import Transformer

from errors import OverpassTimeoutError

UA: dict[str, str] = {
    "User-Agent": "sitegrab/1.0 (architectural site modelling; contact: kathpaliaarshay@gmail.com)"
}

# Reject bounding boxes larger than this (km) on either side. A city-scale fetch
# (e.g. all of London) pulls hundreds of MB of OSM geometry and blows past the
# 512MB free-tier memory budget, so we cap at a neighbourhood/district footprint.
# This applies to the *name-search* path, where the user has not deliberately
# chosen a footprint.
MAX_BBOX_KM: float = 5.0

# Absolute safety ceiling for the *explicit draw-on-map* path. The user drew the
# box deliberately, so we don't enforce the neighbourhood cap — but a whole-country
# box would still exhaust the 512MB free tier and crash the server, so we keep a
# hard ceiling here.
MAX_BBOX_KM_EXPLICIT: float = 15.0


def bbox_dimensions_km(s: float, w: float, n: float, e: float) -> tuple[float, float]:
    """Approximate the (width, height) of a WGS84 bounding box in kilometres."""
    mean_lat = math.radians((s + n) / 2)
    width = abs(e - w) * 111.32 * math.cos(mean_lat)
    height = abs(n - s) * 111.32
    return width, height

# Public Overpass mirrors. Swap this list for a self-hosted / paid endpoint
# to take load off the free public servers (see README, data-source etiquette).
OVERPASS: list[str] = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Fail-fast budget. The old code allowed 6 attempts x 180s + growing backoff
# (>1000s worst case), so a stuck Overpass hung the whole request indefinitely.
# We now cap the TOTAL wall-clock time across all mirrors, retries and backoff:
# if the data isn't in hand within OVERPASS_TOTAL_TIMEOUT seconds we raise
# OverpassTimeoutError and return a clean error instead of hanging. Each attempt
# is also capped so a single slow mirror can't eat the whole budget.
OVERPASS_TOTAL_TIMEOUT: float = 30.0
OVERPASS_PER_ATTEMPT_TIMEOUT: float = 12.0

# In-process mirror health, keyed by endpoint URL. Reset on every restart
# (Render redeploys wipe it) — that's an acceptable simplification for a
# free-tier tool, see REALISM_AND_GAPS.md v10.3. Each entry holds at most
# ``last_failure_at`` (monotonic seconds) and ``last_latency`` (seconds of the
# most recent successful response). No locking: worst case under concurrent
# requests is a slightly stale ordering decision, never a crash.
_mirror_state: dict[str, dict[str, float]] = {}

# How long a mirror stays deprioritized after a failure/timeout before it's
# considered "healthy again" and re-enters normal (latency-based) ordering.
MIRROR_FAILURE_COOLDOWN: float = 150.0


def _record_mirror_result(
    endpoint: str, success: bool, latency: float | None = None
) -> None:
    state = _mirror_state.setdefault(endpoint, {})
    if success:
        state.pop("last_failure_at", None)
        if latency is not None:
            state["last_latency"] = latency
    else:
        state["last_failure_at"] = time.monotonic()


def _order_mirrors() -> list[str]:
    """Decide which mirror to try first, based on recent in-process history.

    A mirror that failed within ``MIRROR_FAILURE_COOLDOWN`` seconds is pushed
    to the back. Among the rest, the one with the faster last successful
    response goes first. With no history at all (e.g. right after a fresh
    deploy) this degrades to the declared ``OVERPASS`` order.
    """
    now = time.monotonic()

    def score(endpoint: str) -> tuple[float, str]:
        state = _mirror_state.get(endpoint, {})
        failure_at = state.get("last_failure_at")
        if failure_at is not None and (now - failure_at) < MIRROR_FAILURE_COOLDOWN:
            age = now - failure_at
            return 1000.0 + age, f"failed {age:.0f}s ago"
        latency = state.get("last_latency")
        if latency is not None:
            return latency, f"last responded in {latency:.1f}s"
        return 100.0, "no history"

    scored = [(endpoint,) + score(endpoint) for endpoint in OVERPASS]
    scored.sort(key=lambda t: t[1])  # stable: ties keep the declared order
    ordered = [endpoint for endpoint, _, _ in scored]

    if ordered[0] != OVERPASS[0]:
        first_reason = scored[0][2]
        skipped_reason = next(r for e, _, r in scored if e == OVERPASS[0])
        print(
            f"[overpass] trying {ordered[0]} first ({first_reason}); "
            f"{OVERPASS[0]} deprioritized ({skipped_reason})"
        )
    return ordered


def geocode(name: str) -> tuple[float, float, float, float, str]:
    """Resolve an area name to (south, west, north, east, display_name) via Nominatim."""
    q = urllib.parse.urlencode({"q": name, "format": "json", "limit": 1})
    req = urllib.request.Request(
        "https://nominatim.openstreetmap.org/search?" + q, headers=UA
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if not data:
        raise ValueError(f"No location found for '{name}'")
    # Nominatim boundingbox order is: South, North, West, East
    s, n, w, e = map(float, data[0]["boundingbox"])
    width_km, height_km = bbox_dimensions_km(s, w, n, e)
    if width_km > MAX_BBOX_KM or height_km > MAX_BBOX_KM:
        raise ValueError(
            "This area is too large. Please try a smaller neighbourhood or "
            "district, for example Shoreditch rather than London."
        )
    return s, w, n, e, data[0]["display_name"]


def resolve_area(
    area: str | None,
    bbox: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float, str]:
    """Resolve either a name or an explicit bbox to (south, west, north, east, display_name).

    When ``bbox`` (south, west, north, east) is given it is used directly — no
    geocoding — and validated only against the absolute explicit ceiling
    (``MAX_BBOX_KM_EXPLICIT``). When only ``area`` is given, behaviour is exactly
    the existing name-search path, including the neighbourhood-scale cap.
    ``bbox`` takes precedence if both are supplied.
    """
    if bbox is not None:
        s, w, n, e = bbox
        if not (n > s and e > w):
            raise ValueError(
                "Invalid bounding box: north must exceed south and east must exceed west."
            )
        width_km, height_km = bbox_dimensions_km(s, w, n, e)
        if width_km > MAX_BBOX_KM_EXPLICIT or height_km > MAX_BBOX_KM_EXPLICIT:
            raise ValueError(
                f"This box is too large ({width_km:.1f} km x {height_km:.1f} km). "
                f"The maximum is {MAX_BBOX_KM_EXPLICIT:.0f} km on a side — please draw "
                "a smaller area."
            )
        display = (
            f"Custom area "
            f"({(s + n) / 2:.4f}, {(w + e) / 2:.4f}) "
            f"{width_km:.1f}x{height_km:.1f} km"
        )
        return s, w, n, e, display
    if not area:
        raise ValueError("No area name or bounding box was provided.")
    return geocode(area)


def utm_epsg(lon: float, lat: float) -> int:
    """Auto-detect the correct UTM zone EPSG code from a coordinate."""
    zone = int((lon + 180) / 6) + 1
    return (32600 if lat >= 0 else 32700) + zone  # e.g. 32640 Dubai, 32630 most of UK


def fetch_overpass(
    query: str, total_timeout: float = OVERPASS_TOTAL_TIMEOUT
) -> dict[str, Any]:
    """POST to Overpass with mirror failover + exponential backoff.

    Bounded by a HARD total-time ceiling (``total_timeout`` seconds across all
    mirrors, retries and backoff) so a stuck or busy Overpass fails fast instead
    of hanging the request. Raises :class:`OverpassTimeoutError` when the budget
    is exhausted. Overpass frequently returns a non-JSON 'server busy' HTML page;
    that is treated as retryable rather than a hard failure.
    """
    start = time.monotonic()
    mirror_order = _order_mirrors()
    last: str | None = None
    attempt = 0
    while True:
        remaining = total_timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise OverpassTimeoutError(
                f"Overpass did not respond within {total_timeout:.0f}s "
                f"(last: {last or 'no response'})."
            )
        endpoint = mirror_order[attempt % len(mirror_order)]
        attempt_start = time.monotonic()
        try:
            body = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(endpoint, data=body, headers=UA)
            # Never let one attempt exceed the remaining budget.
            attempt_timeout = min(OVERPASS_PER_ATTEMPT_TIMEOUT, remaining)
            with urllib.request.urlopen(req, timeout=attempt_timeout) as r:
                txt = r.read().decode()
            if txt.strip().startswith("{"):
                _record_mirror_result(endpoint, True, time.monotonic() - attempt_start)
                return json.loads(txt)
            last = "server busy"
            _record_mirror_result(endpoint, False)
        except Exception as ex:  # noqa: BLE001 - any network error is retryable
            last = str(ex)
            print(f"[overpass] attempt {attempt + 1} on {endpoint} failed: {ex}")
            _record_mirror_result(endpoint, False)
        attempt += 1
        # Backoff, but never sleep past the remaining budget.
        remaining = total_timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise OverpassTimeoutError(
                f"Overpass did not respond within {total_timeout:.0f}s "
                f"(last: {last or 'no response'})."
            )
        time.sleep(min(2 ** attempt, remaining))


def get_transformer(
    s: float, w: float, n: float, e: float
) -> tuple[Transformer, int]:
    """Build a WGS84 -> local UTM transformer for the centre of the bbox."""
    clon, clat = (w + e) / 2, (s + n) / 2
    epsg = utm_epsg(clon, clat)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return transformer, epsg


if __name__ == "__main__":
    import sys

    area = " ".join(sys.argv[1:]) or "Dubai Marina"
    s, w, n, e, name = geocode(area)
    transformer, epsg = get_transformer(s, w, n, e)
    print(f"Resolved name : {name}")
    print(f"Bounding box  : S={s} W={w} N={n} E={e}")
    print(f"Detected EPSG : {epsg}")

    # A light probe query just to confirm Overpass connectivity + element count.
    query = f"""
[out:json][timeout:180];
(
  way["building"]({s},{w},{n},{e});
);
out geom;
"""
    data = fetch_overpass(query)
    print(f"Building ways : {len(data.get('elements', []))}")
