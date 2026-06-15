# SiteGrab Analysis Framework (v7)

The geometry pipeline (terrain, massing, linework) is the *free* layer and is finished.
v7 begins the pivot the real user feedback demands — **"useful, but I'd pay for analysis,
not geometry."** This document defines the small, pluggable **site-analysis framework** that
every future analysis (shadow studies, wind, views, flood, noise…) plugs into without
rework. **Sun Path is the first module built on it**, and one of its jobs is to prove the
framework's shape is right.

It is deliberately *not* a generic plugin engine. It is the thinnest contract that lets a
new analysis declare what it is, take a resolved site, and emit aligned CAD layers — and
nothing more. If a future analysis needs something the contract can't express, the contract
grows then, against a real second example, not speculatively now.

---

## 1. The two-problem discipline (DATA vs REPRESENTATION)

Every analysis is split into two problems that never mix in the code:

1. **Data** — where the real numbers come from. This is the analysis's claim to honesty.
   It is a pure function of the site and a few parameters: it fetches or *computes* numbers
   and returns plain Python values (floats, lists, dataclasses). It touches no `rhino3dm`
   or `ezdxf`. For Sun Path the data is **calculated solar geometry** (sun azimuth +
   altitude), not fetched — see `solar.py`.
2. **Representation** — turning those numbers into Rhino/CAD geometry on named layers. It
   imports the geometry libraries and the shared writers, and knows nothing about *how* the
   numbers were obtained.

Keeping them apart means the data layer is unit-testable against known values (the sun's
noon altitude must be `90 − |lat| ± 23.44°`) with zero geometry, and the representation
layer can be re-pointed at a different data source later without a rewrite.

Each module must document, in its own header, **exactly what data source it uses and
whether that source is free and keyless.** SiteGrab ships only on free, keyless data.

---

## 2. The contract (`analysis/framework.py`)

Four small types and a registry. No base classes to inherit, no lifecycle hooks — a module
is just an object exposing a `spec` and a `run`.

### `SiteContext` — what every analysis receives

The site, already resolved by the *existing, unchanged* `fetch_core` pipeline, so an
analysis never re-implements geocoding, the bbox caps, the UTM zone choice, or the
transformer. Alignment with the geometry model is therefore automatic and free.

```
SiteContext:
    south, west, north, east : float          # WGS84 bbox
    transformer              : pyproj WGS84→UTM (the SAME one the geometry uses)
    epsg                     : int             # the UTM zone EPSG
    display_name             : str
    centroid                 : (lon, lat)       # property
    # convenience the geometry pipeline already knows how to build, lazily:
    grid                     : ElevationGrid | None   # terrain, only if a module asks
    buildings                : list[Building] | None  # footprints+heights, only if asked
```

`grid` and `buildings` are **optional and lazily populated by the runner** *only when a
module's `spec.requires` lists them* — a sun-arc-only request never pays for an OSM
building fetch, and a no-terrain request never fetches DEM tiles. This is how the framework
respects the 512 MB tier: a module pulls exactly the data sources it declares, nothing more.

### `AnalysisSpec` — what a module declares about itself

This is the single source of truth the **frontend** reads to render the selectable analysis
list, and the **runner** reads to know what to prepare and validate.

```
AnalysisSpec:
    key          : str            # "sun_path"  (URL/registry id)
    name         : str            # "Sun Path"  (shown to the user)
    summary      : str            # one line for the UI
    data_source  : str            # human description of the DATA origin
    data_is_free : bool           # must be True to ship
    requires     : tuple[str,...] # any of: "location", "buildings", "terrain"
    outputs      : tuple[str,...] # subset of ("dxf", "3dm")
    params       : tuple[Param,...]  # typed, defaulted inputs (date range, etc.)
```

`requires` drives lazy data preparation; `outputs` tells the UI which format toggles to
offer; `params` is a tiny typed schema (name, label, type, default, choices) the frontend
turns into controls and the runner validates and fills with defaults.

### `AnalysisResult` — what a module returns

```
AnalysisResult:
    files : list[(path, download_name)]   # the .3dm and/or .dxf written to out_dir
    stats : dict                          # counts, peak numbers, verification read-back
    notes : list[str]                     # HONESTY notes surfaced to the user
```

`notes` is mandatory and is where each analysis states its caveats in plain language
(e.g. Sun Path: "sun geometry is exact; shadow lengths inherit the building-height
estimate where OSM lacks real heights"). The UI shows them next to the download.

### The module object

```
class SunPath:
    spec = AnalysisSpec(... )
    def run(self, ctx: SiteContext, params: dict, formats: list[str],
            out_dir: str) -> AnalysisResult: ...
```

### Registry (`analysis/__init__.py`)

```
register(SunPath())          # modules self-register on import
get(key)        -> module
list_specs()    -> [spec, ...]   # the frontend's menu
```

---

## 3. The runner (`analysis/runner.py`) — the one place that touches the pipeline

A single `run_analysis(key, area, bbox, params, formats, out_dir)` that:

1. resolves the site with the existing `resolve_area` + `get_transformer` (same caps, same
   UTM choice as `/generate` — so the analysis lands in the *same coordinates* as the
   model);
2. builds a `SiteContext`;
3. reads the module's `spec.requires` and lazily attaches only the data sources asked for
   (`terrain` → `fetch_elevation_grid`; `buildings` → the lean Overpass building fetch,
   reusing `build_rhino.building_height`/`_footprint_area_m2` so heights are identical to
   the massing model);
4. validates `formats` against `spec.outputs` and `params` against `spec.params`;
5. calls `module.run(...)` and returns its `AnalysisResult`.

`main.py` gains exactly one endpoint, `POST /analysis`, that calls the runner and streams
the file(s) back — the **same** single-vs-zip + `BackgroundTask` cleanup logic `/generate`
already uses. No geometry code is touched.

---

## 4. How Sun Path plugs in (the proof)

| contract field | Sun Path's value |
|---|---|
| `data_source` | **Calculated** solar position (`astral`), free + keyless — no API, no billing |
| `requires` | `("location", "buildings")` + `"terrain"` only when draped shadows are asked |
| `outputs` | `("dxf", "3dm")` — user picks either or both |
| `params` | `date_set` (key dates, default all three), `shadow_times`, `draped_shadows` |
| DATA half | `solar.py` — pure numbers: azimuth/altitude tracks, hourly samples, sunrise/sunset |
| REPRESENTATION half | `sunpath.py` — arcs, hourly points, cast shadows on granular `SUN/...` layers, written to `.3dm` and `.dxf` |
| `notes` | sun geometry exact; shadows inherit the v5 height-estimate caveat; north basis stated |

**Why `astral`, not `pvlib`** (the deploy-risk dependency call). The brief prefers `pvlib`
for precision, but this project's binding constraint is the 512 MB free tier (documented at
length in REALISM_AND_GAPS v6 — the stress site already peaks at ~93% of the ceiling).
`pvlib` pulls in `pandas` **and** `scipy` (>100 MB of image, tens of MB resident on import);
`astral` is pure-Python and installs with a single tiny dep (`tzdata`). The accuracy
difference is *invisible at site scale*: `astral`'s sun position is good to a small fraction
of a degree, and at a 250 m arc radius a 0.1° error is sub-centimetre. Verified against the
exact formula — Greenwich (lat 51.48) noon altitudes came out 62.0 / 38.5 / 15.1° versus the
predicted 61.96 / 38.52 / 15.08°. So we keep the honesty claim ("sun geometry is real and
precise") while taking the dependency that *cannot* break the deploy. `astral` is added to
`requirements.txt` and its import is asserted in the Docker build like the rest of the stack.

---

## 5. Adding the next analysis (the no-rework test)

A v8 analysis (say a wind-exposure or a viewshed study) ships by:

1. writing `analysis/<name>.py` with a `data` function (pure numbers, documented source)
   and a `representation` half (geometry on `<NAME>/...` layers);
2. defining its `AnalysisSpec` (what it needs, what it outputs, its params);
3. `register(...)`-ing it.

No change to the runner, the endpoint, or the frontend menu logic — the spec drives all
three. If step 1 needs a data source the `SiteContext` lacks, *that* is the signal to extend
the contract, against a concrete second module rather than a guess. That is the framework's
success criterion, and v8 is its first real test.
