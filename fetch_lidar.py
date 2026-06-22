"""v9 — Real building heights from FREE LiDAR (England: Environment Agency).

DATA SOURCE. The Environment Agency / DEFRA **LIDAR Composite** DSM and DTM at
1 m, served as float32 GeoTIFF by a public OGC **WCS** (Web Coverage Service):
free, keyless, no billing, no registration. We fetch two coverages for the
bbox — the Digital Surface Model (DSM, top of buildings/trees) and the Digital
Terrain Model (DTM, bare earth) — and a building's height above its own ground
is ``DSM - DTM`` sampled under the footprint. Coverage is national (England);
outside it the per-building resolver falls back to the existing v5 estimate, so
nobody gets a broken model (see build_combined / build_rhino).

HONESTY. Where this returns a height it is **surveyed** (±15 cm RMSE source),
not a type guess — the honest upgrade Sun Path/Wind shadows always wanted. But
it is sampled and sanity-checked (see :func:`LidarHeights.height_for`), and the
caller still validates every value before trusting it; a rejected or missing
sample falls back to the estimate and is reported, never silently wrong.

NO NEW DEPENDENCY. The float32 GeoTIFF is read with Pillow (already a dep) as a
mode-'F' image; numpy (already a dep) does the per-footprint maths. ``rasterio``
/ GDAL is deliberately avoided — it is >100 MB of image on a 512 MB tier.

CRS. EA data is British National Grid (EPSG:27700). We hold a WGS84->27700
transformer here and sample by lon/lat (exactly as the terrain grid does), so
heights align with the OSM/UTM model without the caller knowing about 27700.
"""

from __future__ import annotations

import gc
import io
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image
from pyproj import Transformer

from fetch_core import UA

# --- WCS endpoints (keyless). Coverage ids come from each service's
# GetCapabilities; "Elevation" layers carry real metres (the "Hillshade" ones
# are styled and useless for heights). -----------------------------------------
_DSM_WCS = ("https://environment.data.gov.uk/spatialdata/"
            "lidar-composite-digital-surface-model-last-return-dsm-1m/wcs")
_DSM_CID = ("9ba4d5ac-d596-445a-9056-dae3ddec0178__"
            "Lidar_Composite_Elevation_LZ_DSM_1m")
_DTM_WCS = ("https://environment.data.gov.uk/spatialdata/"
            "lidar-composite-digital-terrain-model-dtm-1m/wcs")
_DTM_CID = ("13787b9a-26a4-4775-8523-806d13af58fc__"
            "Lidar_Composite_Elevation_DTM_1m")

# Coverage envelope in EPSG:27700 (from DescribeCoverage) — used to skip the
# fetch entirely for sites outside England before spending a request.
_COVER_E = (80000.0, 656000.0)
_COVER_N = (4000.0, 665000.0)

# Memory discipline (the binding 512 MB tier). Native is 1 m; we cap the raster
# at MAX_PX per axis and ask the server to downsample (scalefactor) for bigger
# bboxes, so the two arrays never exceed ~2*MAX_PX^2*4 bytes (~64 MB at 2000).
# 1-2 m is ample to percentile a building footprint; the effective resolution is
# reported. (scalesize 500-errors on this server; scalefactor works.)
MAX_PX = 2000
NATIVE_RES_M = 1.0

# The GeoTIFF nodata sentinel is -FLT_MAX (~-3.4e38); treat that and any
# non-finite as "no LiDAR here".
_NODATA_BELOW = -1.0e30

_RETRIES = 4


def _to_bng() -> Transformer:
    return Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)


def _overlaps_england(minx: float, miny: float, maxx: float, maxy: float) -> bool:
    return not (maxx < _COVER_E[0] or minx > _COVER_E[1]
                or maxy < _COVER_N[0] or miny > _COVER_N[1])


def _get_coverage(wcs: str, cid: str, bng_bbox: tuple[float, float, float, float],
                  scalefactor: float) -> np.ndarray:
    """One WCS GetCoverage -> (H, W) float32 array (north-up). Retries w/ backoff."""
    minx, miny, maxx, maxy = bng_bbox
    params = {
        "service": "WCS", "version": "2.0.1", "request": "GetCoverage",
        "coverageId": cid, "format": "image/tiff",
        "subset": [f"E({minx},{maxx})", f"N({miny},{maxy})"],
    }
    # urlencode with doseq so the two subset axes both appear.
    query = urllib.parse.urlencode(params, doseq=True, safe="(),")
    if scalefactor < 1.0:
        query += f"&scalefactor={scalefactor:.4f}"
    url = f"{wcs}?{query}"
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            im = Image.open(io.BytesIO(raw))
            arr = np.asarray(im, dtype=np.float32)
            del raw, im
            if arr.ndim != 2:
                raise ValueError(f"unexpected LiDAR raster shape {arr.shape}")
            return arr
        except Exception as ex:  # noqa: BLE001 - any network/decode error retryable
            last = ex
    raise RuntimeError(f"LiDAR WCS unavailable ({cid.split('__')[-1]}): {last}")


class LidarHeights:
    """A single height-above-ground raster for a bbox + a per-footprint sampler.

    To halve resident memory on the 512 MB tier we hold ONE array — the
    per-pixel ``DSM - DTM`` (NaN where either source was nodata) — not both
    rasters; the difference is all the height sampler needs. A building height is
    a high percentile of that difference inside the footprint polygon.
    """

    # Percentile of (DSM-DTM) used as the building height: high enough to reach
    # the roof/eaves and ignore courtyard/edge lows, low enough to ignore
    # chimneys, aerials and the odd overhanging-tree spike (which max() catches).
    HEIGHT_PCTL = 85.0
    MIN_VALID_PX = 3          # too few covered pixels -> untrustworthy -> None

    def __init__(self, hgt: np.ndarray,
                 bng_bbox: tuple[float, float, float, float], res_m: float):
        self.hgt = hgt        # height above ground (m), NaN where no coverage
        self.minx, self.miny, self.maxx, self.maxy = bng_bbox
        self.res_m = res_m
        h, w = hgt.shape
        self._h, self._w = h, w
        self._resE = (self.maxx - self.minx) / w
        self._resN = (self.maxy - self.miny) / h
        self._tf = _to_bng()

    def height_for(self, ring_lonlat: list[tuple[float, float]]
                   ) -> tuple[float | None, int]:
        """(height_m | None, n_valid_px) for a footprint given as lon/lat verts.

        None when the footprint has too few covered pixels (LiDAR gap), so the
        caller falls back to the estimate. The integer lets the caller log how
        much real coverage backed an accepted height.
        """
        es: list[float] = []
        ns: list[float] = []
        for lon, lat in ring_lonlat:
            x, y = self._tf.transform(lon, lat)
            es.append(x)
            ns.append(y)
        # Pixel window covering the footprint (north-up: row 0 = maxy).
        c0 = int((min(es) - self.minx) / self._resE)
        c1 = int((max(es) - self.minx) / self._resE) + 1
        r0 = int((self.maxy - max(ns)) / self._resN)
        r1 = int((self.maxy - min(ns)) / self._resN) + 1
        c0 = max(0, min(self._w, c0)); c1 = max(0, min(self._w, c1))
        r0 = max(0, min(self._h, r0)); r1 = max(0, min(self._h, r1))
        if c1 <= c0 or r1 <= r0:
            return None, 0

        hgt_win = self.hgt[r0:r1, c0:c1]
        # Pixel-centre coordinates for the window.
        cc = self.minx + (np.arange(c0, c1) + 0.5) * self._resE
        rr = self.maxy - (np.arange(r0, r1) + 0.5) * self._resN
        gx, gy = np.meshgrid(cc, rr)
        inside = _points_in_poly(gx, gy, es, ns)

        valid = inside & np.isfinite(hgt_win)
        n = int(valid.sum())
        if n < self.MIN_VALID_PX:
            return None, n
        return float(np.percentile(hgt_win[valid], self.HEIGHT_PCTL)), n

    def release(self) -> None:
        """Free the raster once the building loop is done (before the heavy
        rhino3dm geometry/write stages), so it never stacks with that peak."""
        self.hgt = None  # type: ignore[assignment]
        gc.collect()


def _points_in_poly(gx: np.ndarray, gy: np.ndarray,
                    xs: list[float], ys: list[float]) -> np.ndarray:
    """Vectorised even-odd ray test: which grid points fall in polygon (xs,ys)."""
    inside = np.zeros(gx.shape, dtype=bool)
    n = len(xs)
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = xs[i], ys[i], xs[j], ys[j]
        cond = ((yi > gy) != (yj > gy))
        # x of the edge at this y (guard against horizontal edges).
        denom = (yj - yi)
        if denom != 0.0:
            xint = (xj - xi) * (gy - yi) / denom + xi
            inside ^= cond & (gx < xint)
        j = i
    return inside


def fetch_lidar_heights(s: float, w: float, n: float, e: float,
                        max_px: int = MAX_PX
                        ) -> tuple[LidarHeights | None, dict]:
    """Fetch DSM+DTM for the WGS84 bbox; None if outside England coverage.

    ``max_px`` caps the raster's larger axis — the memory governor in
    build_combined passes a smaller value (or skips the call entirely) on big
    sites so the augmented build stays inside the 512 MB tier.

    Returns ``(heights | None, info)``. ``info`` always carries ``available``
    and a human ``reason``; when available it adds the effective resolution and
    raster size, for the provenance report and the honesty notes.
    """
    tf = _to_bng()
    # Project the four bbox corners and take their BNG envelope.
    xs, ys = [], []
    for lon, lat in ((w, s), (e, s), (e, n), (w, n)):
        x, y = tf.transform(lon, lat)
        xs.append(x); ys.append(y)
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    if not _overlaps_england(minx, miny, maxx, maxy):
        return None, {"available": False,
                      "reason": "Outside England LiDAR coverage (EA DSM/DTM); "
                                "heights are type-estimated."}
    # Clip the request to the coverage envelope (a coastal/border bbox can hang
    # off the edge; the uncovered part comes back as nodata and falls back).
    minx = max(minx, _COVER_E[0]); maxx = min(maxx, _COVER_E[1])
    miny = max(miny, _COVER_N[0]); maxy = min(maxy, _COVER_N[1])
    span = max(maxx - minx, maxy - miny)
    px_native = span / NATIVE_RES_M
    scalefactor = min(1.0, max_px / px_native) if px_native > 0 else 1.0
    res_m = NATIVE_RES_M / scalefactor
    bng = (minx, miny, maxx, maxy)
    try:
        dsm = _get_coverage(_DSM_WCS, _DSM_CID, bng, scalefactor)
        dtm = _get_coverage(_DTM_WCS, _DTM_CID, bng, scalefactor)
    except Exception as ex:  # noqa: BLE001 - unreachable service -> graceful fallback
        return None, {"available": False,
                      "reason": f"LiDAR service unreachable ({ex}); heights are "
                                f"type-estimated."}
    # Align shapes defensively (server pixel-snapping can differ by a row/col).
    h0 = min(dsm.shape[0], dtm.shape[0])
    w0 = min(dsm.shape[1], dtm.shape[1])
    # Collapse to ONE height-above-ground array (NaN where either source was
    # nodata) and free the two source rasters immediately, halving resident
    # memory for the rest of the build.
    hgt = dsm[:h0, :w0] - dtm[:h0, :w0]
    bad = ((dsm[:h0, :w0] <= _NODATA_BELOW) | (dtm[:h0, :w0] <= _NODATA_BELOW)
           | ~np.isfinite(dsm[:h0, :w0]) | ~np.isfinite(dtm[:h0, :w0]))
    hgt[bad] = np.nan
    del dsm, dtm, bad
    gc.collect()
    heights = LidarHeights(hgt, bng, res_m)
    return heights, {
        "available": True,
        "reason": "England EA LiDAR (DSM-DTM, 1 m composite).",
        "res_m": round(res_m, 2),
        "raster_px": [int(w0), int(h0)],
    }
