"""SiteGrab shared pipeline core.

Geocoding (Nominatim) + UTM zone detection + Overpass fetch with mirror
failover and exponential backoff. Shared by both the Rhino and DXF writers.

The Nominatim bounding-box ordering and the UTM formula are subtle and easy
to get wrong; they are correct as written here and must not be changed.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from pyproj import Transformer

UA: dict[str, str] = {
    "User-Agent": "sitegrab/1.0 (architectural site modelling; contact: kathpaliaarshay@gmail.com)"
}

# Public Overpass mirrors. Swap this list for a self-hosted / paid endpoint
# to take load off the free public servers (see README, data-source etiquette).
OVERPASS: list[str] = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


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
    return s, w, n, e, data[0]["display_name"]


def utm_epsg(lon: float, lat: float) -> int:
    """Auto-detect the correct UTM zone EPSG code from a coordinate."""
    zone = int((lon + 180) / 6) + 1
    return (32600 if lat >= 0 else 32700) + zone  # e.g. 32640 Dubai, 32630 most of UK


def fetch_overpass(query: str) -> dict[str, Any]:
    """POST to Overpass with mirror failover + exponential backoff.

    Overpass frequently returns a non-JSON 'server busy' HTML page; treat that
    as retryable rather than a hard failure.
    """
    last: str | None = None
    for attempt in range(6):
        endpoint = OVERPASS[attempt % len(OVERPASS)]
        try:
            body = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(endpoint, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                txt = r.read().decode()
            if txt.strip().startswith("{"):
                return json.loads(txt)
            last = "server busy"
        except Exception as ex:  # noqa: BLE001 - any network error is retryable
            last = str(ex)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Overpass unavailable after retries: {last}")


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
