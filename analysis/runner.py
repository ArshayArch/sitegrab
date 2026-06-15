"""SiteGrab analysis runner — resolves a site and drives one analysis module.

The single bridge between the FastAPI endpoint and the analysis modules. It is
the ONLY place in the framework that touches the existing geometry pipeline
(``fetch_core``, ``fetch_elevation``, ``build_rhino``), so modules stay pure.

Responsibilities (see ANALYSIS_FRAMEWORK.md §3):
  1. resolve the site with the EXISTING ``resolve_area`` + ``get_transformer``
     (same caps, same UTM choice as ``/generate`` — alignment is automatic);
  2. build a :class:`SiteContext`;
  3. lazily attach only the data sources the module's ``spec.requires`` lists;
  4. validate the requested ``formats`` and fill ``params`` with defaults;
  5. call ``module.run`` and return its :class:`AnalysisResult`.
"""

from __future__ import annotations

import gc
from typing import Any

import build_rhino as rh
from fetch_core import fetch_overpass, get_transformer, resolve_area

from .framework import (
    BUILDINGS,
    TERRAIN,
    Building,
    SiteContext,
    get,
)


def _fetch_buildings(ctx: SiteContext) -> list[Building]:
    """Lean OSM building footprints with heights identical to the massing model.

    Reuses ``build_rhino``'s height logic so an analysis's heights match the
    geometry export exactly; bases sit at the lowest ground under the footprint
    when terrain is present (matching ``build_combined``), else 0.0.
    """
    bbox = f"{ctx.south},{ctx.west},{ctx.north},{ctx.east}"
    data = fetch_overpass(rh._QUERY_TEMPLATE.format(bbox=bbox))
    out: list[Building] = []
    elements = data.get("elements", [])
    while elements:
        el = elements.pop()
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        if "building" not in tags:
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 3:
            continue
        pts = [ctx.transformer.transform(p["lon"], p["lat"]) for p in geom]
        height, estimated = rh.building_height(tags, pts, el.get("id", 0))
        base = (
            min(ctx.grid.sample(p["lon"], p["lat"]) for p in geom)
            if ctx.grid is not None
            else 0.0
        )
        out.append(Building(pts=pts, height=height, base=base,
                            height_estimated=estimated))
    del data
    gc.collect()
    return out


def run_analysis(
    key: str,
    area: str | None,
    bbox: tuple[float, float, float, float] | None,
    params: dict[str, Any],
    formats: list[str],
    out_dir: str,
):
    """Resolve the site, prepare declared data sources, and run the module."""
    module = get(key)
    spec = module.spec

    # 1-2. Resolve site + context (the existing pipeline; same caps + UTM zone).
    s, w, n, e, display_name = resolve_area(area, bbox)
    transformer, epsg = get_transformer(s, w, n, e)
    ctx = SiteContext(south=s, west=w, north=n, east=e,
                      transformer=transformer, epsg=epsg,
                      display_name=display_name)

    # 4. Validate formats against what the module can emit.
    wanted = [f for f in spec.outputs if f in {f.lower() for f in formats}]
    if not wanted:
        raise ValueError(
            f"Select at least one output format for {spec.name}: "
            f"{', '.join(spec.outputs)}.")

    # Fill params with declared defaults (module reads only what it declared).
    resolved = {p.name: params.get(p.name, p.default) for p in spec.params}

    # 3. Lazily attach ONLY the declared data sources. Terrain first (a draped
    # analysis needs the grid while reading building bases).
    needs_terrain = TERRAIN in spec.requires and bool(
        resolved.get("draped_shadows", False))
    if needs_terrain:
        from fetch_elevation import fetch_elevation_grid
        ctx.grid = fetch_elevation_grid(s, w, n, e)
    if BUILDINGS in spec.requires:
        ctx.buildings = _fetch_buildings(ctx)

    # 5. Run.
    return module.run(ctx, resolved, wanted, out_dir)
