# Building-Height Provenance (v9) — which heights to trust

SiteGrab's combined model now resolves **every building's height individually**
from the best available source and **tells you which source it used**, both as a
layer split you can see in Rhino and as counts in the download note. This is the
honesty spine of the product applied to heights: real where we have real data,
estimated where we don't, and never silently mixed.

## The three sources, in priority order

For each building footprint, in order:

1. **`osm` — a real OSM `height` / `building:levels` tag.** Surveyed or declared
   data; the most trustworthy. Always wins (this never regressed from v5).
2. **`lidar` — a sanity-passed LiDAR measurement.** Where England's free
   Environment Agency LIDAR Composite covers the site, the height above ground is
   sampled as the 85th-percentile of per-pixel **DSM − DTM** (surface minus bare
   earth) inside the footprint. Used only if it passes the sanity check below.
3. **`estimated` — the v5 type/footprint estimate.** The legibility guess used
   when there is no tag and no trustworthy LiDAR (outside England, in a coverage
   gap, or when the LiDAR value failed the sanity check).

Sources 1 and 2 are **real** (surveyed/declared); source 3 is **approximate**.

## The sanity check (why LiDAR isn't trusted blindly)

LiDAR is real but noisy and time-stamped: it can catch a tree over a shed, a
crane, a site mid-construction, or plain raster noise. Before a LiDAR height is
trusted it must be plausible (see `build_rhino._lidar_is_sane`):

- **≥ 2 m** — below this is not a standing building (demolished / under
  construction at fly-time / bare ground).
- **≤ 280 m** — taller than all but a handful of UK buildings; beyond this is
  almost surely noise.
- **slenderness ≤ 10** (height ÷ √footprint-area) — rejects an absurdly thin
  reading, e.g. a 40 m² plot "reading" 80 m.
- **not tall-on-tiny** — a > 25 m reading on a < 50 m² footprint is a tree or
  aerial spike, not a tower.

A footprint with too little LiDAR coverage (fewer than a few valid pixels) is
treated as *no sample* and also falls back. Every rejection falls back to the
estimate — the model degrades honestly, never breaks.

## What you see (Option C — the layer split)

Buildings are split by provenance, keeping the house/block grain:

| layer | meaning |
|---|---|
| `3D/BUILDINGS_houses_real` | houses with a real height (OSM tag or LiDAR) |
| `3D/BUILDINGS_blocks_real` | blocks/other with a real height |
| `3D/BUILDINGS_houses_estimated` | houses whose height is a type estimate |
| `3D/BUILDINGS_blocks_estimated` | blocks/other whose height is a type estimate |

Real layers keep the v5 hues; the estimated layers take a muted, yellowed tint
so "approximate" reads at a glance. The download note reports the counts, e.g.
`Heights: N surveyed (OSM), M surveyed (LiDAR), K estimated`.

Scope note: the provenance split and LiDAR heights apply to the **combined**
model (the headline aligned output). The standalone lean `.3dm` and the `.dxf`
keep the v5 type estimates — LiDAR is fetched once, for the combined build, to
respect the 512 MB tier.

## Measured provenance (canonical sites)

Measured with `measure_ws.py <site>` on 2026-06-22 (one site per process; the
governed combined pipeline, no tracemalloc):

| site | buildings | osm | lidar | estimated | peak | LiDAR coverage |
|---|---|---|---|---|---|---|
| Shoreditch, London (full) | 13,023 | 4,275 | 0 | 8,749 | 487 MB | **skipped by the memory governor** (megasite) |
| Clifton, Bristol | 4,670 | 199 | 4,431 | 40 | 236 MB | full 1 m raster; 4,669/4,670 footprints covered |
| Dubai Marina (non-England) | 1,482 | 415 | 0 | 1,067 | 181 MB | none — outside EA coverage, graceful fallback |

The three rows are the three regimes, and each degrades honestly:

- **Covered mid-size site (Clifton).** 99% of buildings get a **real** height
  (4,431 LiDAR + 199 OSM of 4,670); the estimate is the rare fallback (40), not
  the norm. This is the honest upgrade to nearly every height in the model, and
  the basis on which Sun Path shadow *lengths* become trustworthy.
- **Megasite (full Shoreditch).** The governor trades LiDAR for headroom: it
  keeps the OSM tags (4,275 surveyed) and type-estimates the rest, holding peak
  to 487 MB (un-governed it measured 599 MB — over budget). Real where declared,
  estimated elsewhere, and the download note **says** LiDAR was skipped.
- **Outside England (Dubai).** LiDAR is simply unavailable; every building falls
  back to an OSM tag or an estimate. No crash, no silent zero — just the v5
  behaviour with a stated reason.

## Data source (free, keyless)

Environment Agency / DEFRA **LIDAR Composite** DSM and DTM, 1 m, via the public
OGC **WCS** (`environment.data.gov.uk/spatialdata/...`). No key, no billing, no
registration. British National Grid (EPSG:27700), reprojected with the same
pyproj machinery as the rest of the model. Read as float32 GeoTIFF with Pillow —
no `rasterio`/GDAL dependency (it would not fit the 512 MB tier). Source vertical
accuracy ±15 cm RMSE; our per-footprint 85th-percentile is necessarily coarser.

To check it honestly, `validate_lidar_vs_osm.py` compares the LiDAR height to the
**independent** OSM `height`/`levels` tag on every building that carries one (the
tag never feeds the LiDAR sample, so it is true ground truth). Measured
2026-06-22:

| bbox | tagged buildings | median \|LiDAR − OSM\| | within 5 m |
|---|---|---|---|
| central Shoreditch (`51.523,-0.085,51.531,-0.072`) | 497 | **2.12 m** | 83% |
| Clifton (`51.452,-2.633,51.468,-2.605`) | 199 | **2.30 m** | 81% |

A ~2 m median difference — well inside massing-model tolerance — between two
independent height sources is the evidence that the LiDAR heights are real, not
decorative. (Re-run either row to reproduce; numbers will track live OSM edits.)
