"""SiteGrab elevation fetch (AWS Terrain Tiles, terrarium encoding).

Fetches a dense elevation grid for a WGS84 bbox from the open AWS Terrain
Tiles dataset — free, keyless PNG tiles:

    https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png

Each pixel encodes elevation in metres as ``(R * 256 + G + B / 256) - 32768``.
The underlying global DEMs are ~30m data (SRTM-class), ~10m in parts of the
US/Europe, so the zoom is auto-picked for a ~10–30m sample spacing — fetching
finer would interpolate, not inform (see TOPO_RATIONALE.md).

Same network discipline as the Overpass code: timeouts, retries with
exponential backoff, and a clear error if elevation can't be fetched.
"""

from __future__ import annotations

import io
import math
import time
import urllib.request
from dataclasses import dataclass

import numpy as np
from PIL import Image

from fetch_core import UA

ELEVATION_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_SIZE = 256

# Zoom search bounds. z15 is the highest level published; below z6 a 15km box
# is a handful of pixels and useless.
MAX_ZOOM = 15
MIN_ZOOM = 6

# Guards that keep a terrain fetch well inside the 512MB free tier:
# the fetched pixel window's larger side stays <= MAX_WINDOW_PX (so a full
# mosaic is at most ~MAX_WINDOW_PX^2 float32 ~= 0.4MB) and we never pull more
# than MAX_TILES tiles per request.
MAX_WINDOW_PX = 320
MAX_TILES = 16

# The mesh/contour grid is downsampled to at most this many samples per side;
# beyond ~180 the mesh gains weight but no real DEM information.
MAX_GRID = 180

# Terrarium tiles include ocean BATHYMETRY (GEBCO/ETOPO), so any coastal bbox
# reads tens of metres of seafloor as "ground" — which drags waterside
# buildings to e.g. -46m. We clamp the grid at sea level: water reads as a
# flat plane at 0 (≈ quay level), which is what a site model wants. The cost
# is the rare deep below-sea-level land site (Death Valley, Dead Sea shore),
# which would read flat at 0 — documented in TOPO_RATIONALE.md.
SEA_LEVEL_CLAMP_M = 0.0


def _lonlat_to_global_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    """WGS84 -> global Web-Mercator pixel coordinates at zoom ``z``."""
    scale = TILE_SIZE * (2**z)
    x = (lon + 180.0) / 360.0 * scale
    lat_r = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * scale
    return x, y


def _global_px_to_lat(py: float, z: int) -> float:
    """Inverse Web-Mercator: global pixel row -> latitude."""
    scale = TILE_SIZE * (2**z)
    n = math.pi * (1.0 - 2.0 * py / scale)
    return math.degrees(math.atan(math.sinh(n)))


def _pick_zoom(s: float, w: float, n: float, e: float) -> int:
    """Highest zoom whose pixel window and tile count satisfy the guards."""
    for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
        x0, y0 = _lonlat_to_global_px(w, n, z)
        x1, y1 = _lonlat_to_global_px(e, s, z)
        if max(x1 - x0, y1 - y0) > MAX_WINDOW_PX:
            continue
        tiles_x = int(x1 // TILE_SIZE) - int(x0 // TILE_SIZE) + 1
        tiles_y = int(y1 // TILE_SIZE) - int(y0 // TILE_SIZE) + 1
        if tiles_x * tiles_y <= MAX_TILES:
            return z
    return MIN_ZOOM


def _fetch_tile(z: int, x: int, y: int) -> np.ndarray:
    """Fetch + decode one terrarium tile to a (256, 256) float32 metres array."""
    url = ELEVATION_URL.format(z=z, x=x, y=y)
    last: str | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            rgb = np.asarray(img, dtype=np.float32)
            return rgb[:, :, 0] * 256.0 + rgb[:, :, 1] + rgb[:, :, 2] / 256.0 - 32768.0
        except Exception as ex:  # noqa: BLE001 - any network/decode error is retryable
            last = str(ex)
        time.sleep(2**attempt)
    raise RuntimeError(
        f"Elevation data unavailable (AWS Terrain Tiles, tile {z}/{x}/{y}): {last}"
    )


@dataclass
class ElevationGrid:
    """A regular (in Web-Mercator) elevation grid covering a bbox.

    ``elev[j, i]`` is metres above sea level at (``lats[j]``, ``lons[i]``).
    ``lons`` is uniform; ``lats`` is monotonically increasing but slightly
    non-uniform (Mercator rows). Both are ascending.
    """

    lons: np.ndarray  # (W,) ascending
    lats: np.ndarray  # (H,) ascending
    elev: np.ndarray  # (H, W) float32 metres
    zoom: int

    @property
    def zmin(self) -> float:
        return float(self.elev.min())

    @property
    def zmax(self) -> float:
        return float(self.elev.max())

    def sample(self, lon: float, lat: float) -> float:
        """Bilinear ground height at (lon, lat), clamped to the grid edges."""
        i = int(np.searchsorted(self.lons, lon))
        j = int(np.searchsorted(self.lats, lat))
        i = min(max(i, 1), len(self.lons) - 1)
        j = min(max(j, 1), len(self.lats) - 1)
        tx = (lon - self.lons[i - 1]) / (self.lons[i] - self.lons[i - 1])
        ty = (lat - self.lats[j - 1]) / (self.lats[j] - self.lats[j - 1])
        tx = min(max(tx, 0.0), 1.0)
        ty = min(max(ty, 0.0), 1.0)
        z00 = self.elev[j - 1, i - 1]
        z01 = self.elev[j - 1, i]
        z10 = self.elev[j, i - 1]
        z11 = self.elev[j, i]
        return float(
            z00 * (1 - tx) * (1 - ty)
            + z01 * tx * (1 - ty)
            + z10 * (1 - tx) * ty
            + z11 * tx * ty
        )


def fetch_elevation_grid(s: float, w: float, n: float, e: float) -> ElevationGrid:
    """Fetch the elevation grid covering bbox (south, west, north, east)."""
    z = _pick_zoom(s, w, n, e)
    x0f, y0f = _lonlat_to_global_px(w, n, z)  # top-left of window
    x1f, y1f = _lonlat_to_global_px(e, s, z)  # bottom-right of window
    px0, py0 = int(x0f), int(y0f)
    px1, py1 = int(math.ceil(x1f)), int(math.ceil(y1f))

    tx0, tx1 = px0 // TILE_SIZE, (px1 - 1) // TILE_SIZE
    ty0, ty1 = py0 // TILE_SIZE, (py1 - 1) // TILE_SIZE

    # Mosaic the covering tiles, then crop to the bbox pixel window.
    mosaic = np.empty(
        ((ty1 - ty0 + 1) * TILE_SIZE, (tx1 - tx0 + 1) * TILE_SIZE), dtype=np.float32
    )
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = _fetch_tile(z, tx, ty)
            r0, c0 = (ty - ty0) * TILE_SIZE, (tx - tx0) * TILE_SIZE
            mosaic[r0 : r0 + TILE_SIZE, c0 : c0 + TILE_SIZE] = tile

    window = mosaic[
        py0 - ty0 * TILE_SIZE : py1 - ty0 * TILE_SIZE,
        px0 - tx0 * TILE_SIZE : px1 - tx0 * TILE_SIZE,
    ]
    del mosaic

    # Downsample to the mesh grid cap (stride sampling keeps true DEM values),
    # then clamp out bathymetry (see SEA_LEVEL_CLAMP_M).
    step = max(1, math.ceil(max(window.shape) / MAX_GRID))
    window = np.ascontiguousarray(window[::step, ::step])
    np.maximum(window, SEA_LEVEL_CLAMP_M, out=window)

    rows = np.arange(py0, py1, step, dtype=np.float64) + 0.5  # pixel centres
    cols = np.arange(px0, px1, step, dtype=np.float64) + 0.5
    scale = TILE_SIZE * (2**z)
    lons = cols / scale * 360.0 - 180.0
    lats = np.array([_global_px_to_lat(py, z) for py in rows])  # descending (N->S)

    # Store ascending latitudes (south-first rows) for clean searchsorted.
    return ElevationGrid(lons=lons, lats=lats[::-1], elev=window[::-1], zoom=z)


if __name__ == "__main__":
    # Sanity checks: a coastal flat site should read near 0m; a hilly site
    # should show tens of metres of real variation.
    cases = {
        "Dubai Marina (coastal, flat)": (25.063, 55.122, 25.085, 55.147),
        "Clifton, Bristol (hilly)": (51.452, -2.633, 51.468, -2.605),
    }
    for name, (s, w, n, e) in cases.items():
        grid = fetch_elevation_grid(s, w, n, e)
        print(f"{name}")
        print(f"  zoom={grid.zoom}  grid={grid.elev.shape[1]}x{grid.elev.shape[0]}")
        print(f"  elevation range: {grid.zmin:.1f}m .. {grid.zmax:.1f}m")
        clon, clat = (w + e) / 2, (s + n) / 2
        print(f"  centre sample : {grid.sample(clon, clat):.1f}m")
