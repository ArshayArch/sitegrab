"""SiteGrab combined 3D + 2D + terrain Rhino writer.

Produces a single ``.3dm`` that merges the lean 3D massing, the detailed 2D
linework and (by default) the real topography into one spatially aligned model:

- ``TERRAIN/`` -> ``surface`` (DEM mesh) + ``contours`` (true-elevation polylines).
- ``3D/``      -> buildings draped onto the terrain (each base sits at the
  LOWEST ground height under its footprint — buildings stay plumb, cutting
  into the hill on the high side; see TOPO_RATIONALE.md): pitched-roof
  houses on ``BUILDINGS_houses``, flat blocks on ``BUILDINGS_blocks``.
  Main roads draped per-vertex a hair below ground, footways a kerb above
  on ``PAVEMENTS``, filled green surfaces on ``GREENS``, water, boundary.
- ``Linework/`` -> the granular DXF-style layers, kept FLAT as a clean plan
  drawing at a datum just under the terrain (the site's lowest elevation,
  floored to a whole metre) — the deliberate scope decision in
  TOPO_RATIONALE.md. With terrain off, everything sits at Z=0 as before.

All three datasets are reprojected with the *same* WGS84 -> UTM transformer
(auto-detected from the bbox centroid), so alignment is automatic. Nothing is
recentred or rescaled.

The genuinely subtle logic is *reused, not rewritten*: building-height parsing,
curve construction and extrusion come from ``build_rhino``; layer classification
comes from ``build_dxf``; the terrain mesh/contours come from ``build_terrain``.
This module only orchestrates them into one model.
"""

from __future__ import annotations

import gc
import math
import uuid
from typing import Any

import rhino3dm

import build_dxf as dxf
import build_rhino as rh
from build_terrain import add_terrain
from errors import AreaTooLargeError, MemoryLimitError
from fetch_core import fetch_overpass, get_transformer, resolve_area
from fetch_elevation import ElevationGrid, fetch_elevation_grid
from fetch_lidar import LidarHeights, fetch_lidar_heights

# --- Resident-memory instrumentation (the 512 MB free tier is the binding
# constraint). psutil gives the true current RSS; if it's ever unavailable the
# governor degrades to a no-op (checks return None and are skipped) rather than
# crashing the build. ----------------------------------------------------------
try:
    import psutil

    _PROC = psutil.Process()
except Exception:  # noqa: BLE001 - psutil missing/unusable -> governor is a no-op
    _PROC = None


def current_rss_mb() -> float | None:
    """Current resident set size in MB, or None if psutil is unavailable."""
    if _PROC is None:
        return None
    try:
        return _PROC.memory_info().rss / 1048576
    except Exception:  # noqa: BLE001
        return None


# OOM governor thresholds (MB of RSS). The LiDAR/solid budgets are UPFRONT
# estimates; these are the LIVE backstops during the heavy building loop, so a
# denser-than-modelled site is caught before Render's OOM killer fires:
#   - at SOFT we stop minting new house solids (the heaviest per-house cost) and
#     fall back to lighter meshes, slowing memory growth;
#   - at HARD we abort with a clean MemoryLimitError (a JSON 507) rather than
#     marching into a silent OOM kill.
MEMORY_SOFT_MB = 455.0
MEMORY_HARD_MB = 495.0
MEMORY_CHECK_EVERY = 400          # buildings between live RSS checks

# Upfront request-size reject. Shoreditch (~13k buildings) is the largest site
# proven to deploy on the 512 MB tier; well beyond that an urban box is a certain
# OOM, so we reject it before building any geometry with a clear 413 rather than
# attempting it and getting killed mid-process.
MAX_BUILDINGS = 24000

# RGB palette for the Linework groups, keyed by the top-level DXF category.
# (build_dxf uses AutoCAD Color Index integers; Rhino needs RGB, so we map the
# same categories to readable colours here.)
LINEWORK_COLORS: dict[str, tuple[int, int, int]] = {
    "BLDG": (150, 150, 150),
    "ROADS": (80, 80, 80),
    "RAIL": (200, 80, 200),
    "WATER": (90, 150, 220),
    "NATURAL": (90, 170, 90),
    "LANDUSE": (200, 190, 90),
    "LEISURE": (90, 200, 200),
    "AMENITY": (220, 90, 90),
    "INFRA": (150, 150, 150),
    "ADMIN": (200, 80, 200),
    "LABEL": (200, 200, 200),
}

# Provenance-split building layers (v9, Option C). "real" = a real OSM
# height/levels tag OR a sanity-passed LiDAR measurement; "estimated" = the
# type/footprint guess. Real layers keep the v5 house/block hues; estimated
# layers take a muted yellowed tint so the eye reads them as approximate.
_PROVENANCE_LAYERS: dict[str, tuple[int, int, int]] = {
    "BUILDINGS_houses_real": (190, 125, 95),
    "BUILDINGS_houses_estimated": (203, 185, 140),
    "BUILDINGS_blocks_real": (150, 150, 150),
    "BUILDINGS_blocks_estimated": (197, 192, 165),
}

# --- LiDAR memory governor (v9) -------------------------------------------
# The 512 MB free tier is the binding constraint (REALISM_AND_GAPS v6/v7). The
# LiDAR rasters stack with the massing build's working set, and the canonical
# Shoreditch megasite measured 606 MB un-governed (v6 baseline 485). So, exactly
# as houses are converted to solids only within a measured budget, LiDAR is
# fetched only with enough headroom — otherwise the site keeps type estimates
# and SAYS SO (never a silent OOM). Full-resolution, uncapped LiDAR on megasites
# is the Pro / 2 GB-tier feature.
#
# Baseline peak (MB) ~ fixed cost + per-building, fitted to the v6 benches
# (160 MB + ~25 KB/building reproduces Shoreditch 485 / Clifton ~225). LiDAR is
# allowed to spend down to a target at/below v6's proven-deployable peak; the
# remaining headroom buys the single held height raster (px^2 * 4 bytes), with a
# safety factor for the fetch/decode transient and per-footprint churn.
LIDAR_BASE_MB = 160.0
LIDAR_PER_BLDG_MB = 0.025
LIDAR_PEAK_TARGET_MB = 470.0
LIDAR_SAFETY = 0.45            # fraction of headroom spent on the held raster
LIDAR_MIN_PX = 500            # below this the raster is too coarse to bother
LIDAR_MAX_PX = 2000          # absolute cap (matches fetch_lidar.MAX_PX)


def lidar_budget_px(n_buildings: int) -> int:
    """Largest LiDAR raster (px/axis) that fits the memory budget; 0 = skip.

    Returns 0 when the massing build alone is already near the ceiling (the
    megasite case), so the caller skips LiDAR and falls back to estimates.
    """
    headroom_mb = LIDAR_PEAK_TARGET_MB - (LIDAR_BASE_MB + LIDAR_PER_BLDG_MB * n_buildings)
    if headroom_mb <= 0:
        return 0
    budget_bytes = headroom_mb * 1.0e6 * LIDAR_SAFETY
    px = int((budget_bytes / 4.0) ** 0.5)        # one float32 array
    if px < LIDAR_MIN_PX:
        return 0
    return min(LIDAR_MAX_PX, px)


# With terrain OFF everything sits on the Z=0 plane (original behaviour).
# With terrain ON the flat datum becomes the site's lowest elevation instead.
FLAT_GROUND_Z = 0.0

# ---------------------------------------------------------------------------
# Surface differentiation (kerb-scale, subtle). All offsets are relative to
# the local ground (terrain sample, or the flat datum with terrain off):
# carriageways sit a hair below ground (rh.ROAD_Z = -0.05), pavements ride a
# kerb above, greens get their own faintly-raised filled surface. The flat
# Linework/ layers are untouched — this is about the 3D reading only.
# ---------------------------------------------------------------------------
PAVEMENT_RAISE_M = 0.12  # kerb height
GREEN_RAISE_M = 0.05
# v6: a green whose boundary ground varies no more than this becomes a clean
# planar trimmed surface (a single editable Brep face) instead of a draped
# mesh — flattening within DEM noise is honest; flattening a real slope isn't.
GREEN_PLANAR_MAX_RELIEF_M = 0.75
# Pitches/playgrounds/gardens usually sit INSIDE a park polygon; a slightly
# higher offset keeps the nested surface from z-fighting its parent.
GREEN_NESTED_RAISE_M = 0.08
_GREEN_NESTED = {"pitch", "playground", "garden", "dog_park"}

_PAVEMENT_HIGHWAYS = {"footway", "path", "pedestrian", "cycleway", "steps"}
_GREEN_LEISURE = {"park", "garden", "pitch", "playground", "recreation_ground",
                  "common", "village_green", "dog_park", "golf_course"}
_GREEN_LANDUSE = {"grass", "meadow", "village_green", "recreation_ground",
                  "cemetery", "allotments"}
_GREEN_NATURAL = {"grassland", "heath"}

# Green polygons are decimated to this many boundary vertices before
# triangulation — keeps the O(n^2) ear clipper cheap; visually lossless at
# site scale for organic park outlines.
_GREEN_MAX_VERTS = 150


def _is_green(tags: dict[str, str]) -> bool:
    return (tags.get("leisure") in _GREEN_LEISURE
            or tags.get("landuse") in _GREEN_LANDUSE
            or tags.get("natural") in _GREEN_NATURAL)


# Polygon triangulation lives in build_rhino since v6 (house solids need it
# for their floor caps too).
_earclip = rh._earclip


def _green_mesh(
    pts: list[tuple[float, float]],
    geom: list[dict[str, float]],
    ground: Any,
    raise_m: float = GREEN_RAISE_M,
) -> rhino3dm.Mesh | None:
    """Filled green surface: the polygon triangulated, each boundary vertex
    draped at ``ground(p) + raise_m``. ``pts``/``geom`` are the open ring
    (no duplicated closing point). Triangle interiors stay planar between
    boundary vertices — fine at kerb scale on park-sized polygons.
    """
    step = math.ceil(len(pts) / _GREEN_MAX_VERTS)
    if step > 1:
        pts, geom = pts[::step], geom[::step]
    if len(pts) < 3:
        return None
    tris = _earclip(pts)
    if not tris:
        return None
    mesh = rhino3dm.Mesh()
    for (x, y), p in zip(pts, geom):
        mesh.Vertices.Add(x, y, ground(p) + raise_m)
    for a, b, c in tris:
        mesh.Faces.AddFace(a, b, c)
    mesh.Normals.ComputeNormals()
    return mesh


def _green_plane(
    pts: list[tuple[float, float]], z: float
) -> rhino3dm.Brep | None:
    """Single trimmed planar Brep face for a green on effectively-flat ground.

    ``pts`` is the open boundary ring (no duplicated closing point), kept at
    full resolution — a trim curve costs nothing compared to triangulation.
    Returns None when the trim fails (degenerate/self-intersecting outline);
    the caller falls back to the draped mesh and counts it.
    """
    if len(pts) < 3:
        return None
    curve = rh._closed_curve(_ensure_ccw(pts), z)
    if curve is None:
        return None
    plane = rhino3dm.Plane(
        rhino3dm.Point3d(pts[0][0], pts[0][1], z), rhino3dm.Vector3d(0, 0, 1))
    brep = rhino3dm.Brep.CreateTrimmedPlane(plane, curve)
    if brep is None or not brep.IsValid:
        return None
    return brep


def _ensure_ccw(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return ``pts`` ordered counter-clockwise (positive shoelace area).

    ``rhino3dm.Extrusion.Create`` extrudes along the profile plane's normal,
    whose sign follows the winding order. Forcing counter-clockwise winding makes
    that normal point +Z, so a positive height always extrudes *upward*.
    """
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        area += x1 * y2 - x2 * y1
    return pts if area >= 0 else pts[::-1]


def _add_parent(model: rhino3dm.File3dm, name: str, rgb: tuple[int, int, int]) -> uuid.UUID:
    """Create a top-level group layer and return its Id (for child parenting)."""
    lay = rhino3dm.Layer()
    lay.Name = name
    lay.Id = uuid.uuid4()
    lay.Color = (*rgb, 255)
    model.Layers.Add(lay)
    return lay.Id


def _add_child(
    model: rhino3dm.File3dm,
    name: str,
    parent_id: uuid.UUID,
    rgb: tuple[int, int, int],
    cache: dict[str, int],
) -> int:
    """Create (once) a child layer under ``parent_id``; return its layer index."""
    if name in cache:
        return cache[name]
    lay = rhino3dm.Layer()
    lay.Name = name
    lay.Id = uuid.uuid4()
    lay.ParentLayerId = parent_id
    lay.Color = (*rgb, 255)
    cache[name] = model.Layers.Add(lay)
    return cache[name]


def _attrs(layer_index: int) -> rhino3dm.ObjectAttributes:
    a = rhino3dm.ObjectAttributes()
    a.LayerIndex = layer_index
    return a


def _build_3d_group(
    model: rhino3dm.File3dm,
    data: dict[str, Any],
    transformer: Any,
    corners_ll: list[tuple[float, float]],
    grid: ElevationGrid | None = None,
    datum: float = FLAT_GROUND_Z,
    lidar: LidarHeights | None = None,
) -> dict[str, Any]:
    """Populate the ``3D/`` group from the LEAN dataset, reusing build_rhino logic.

    With an elevation ``grid``, each building's base is set to the LOWEST
    ground height under its footprint (the building stays plumb and cuts into
    the slope — see TOPO_RATIONALE.md), main roads drape per-vertex, and water
    sits flat at the lowest ground under its outline. Without a grid the
    original flat behaviour at ``datum`` (Z=0) is unchanged.

    With ``lidar`` (England only), each building's height is resolved per-building
    (OSM tag > sanity-passed LiDAR > estimate) and placed on a provenance layer:
    real heights on ``BUILDINGS_*_real``, estimates on ``BUILDINGS_*_estimated``,
    so the user can SEE which heights are surveyed (Option C).
    """
    parent = _add_parent(model, "3D", (200, 200, 200))
    cache: dict[str, int] = {}
    for nm, rgb in rh._LAYERS.items():  # pre-create the massing layers
        if nm in ("BUILDINGS_houses", "BUILDINGS_blocks"):
            continue  # replaced by the provenance-split layers below
        _add_child(model, nm, parent, rgb, cache)
    # Provenance split (Option C): real (OSM tag or sanity-passed LiDAR) vs
    # estimated, keeping the house/block grain. Estimated layers take a muted,
    # yellowed tint so "approximate" reads at a glance; real keeps the v5 hues.
    for nm, rgb in _PROVENANCE_LAYERS.items():
        _add_child(model, nm, parent, rgb, cache)
    # Ground-surface layers, filled during the later detailed pass (the lean
    # dataset has no footways or green space).
    surface_layers = {
        "PAVEMENTS": _add_child(model, "PAVEMENTS", parent, (205, 200, 190), cache),
        "GREENS": _add_child(model, "GREENS", parent, (110, 185, 100), cache),
    }

    buildings = houses = roads = water = 0
    house_solids = house_mesh_fallbacks = house_budget_skips = 0
    solid_failures: dict[str, int] = {}
    # Height provenance tally (Option C reporting). LiDAR coverage is the count
    # of footprints that had a usable LiDAR sample (covered+dense enough),
    # whether or not the sanity check then accepted it.
    prov_counts = {rh.PROV_OSM: 0, rh.PROV_LIDAR: 0, rh.PROV_ESTIMATED: 0}
    lidar_covered = 0
    solid_budget = rh.house_solid_budget(sum(
        1 for el in data.get("elements", [])
        if el.get("type") == "way" and "building" in el.get("tags", {})))
    base_z_samples: list[float] = []  # first few building bases, for verification
    bldg_seen = 0                     # buildings entered, for periodic RSS checks
    force_mesh = False                # set once the soft memory limit is crossed
    mem_soft_at: int | None = None    # building count at which the soft limit hit
    # Drain the element list as we go so each parsed feature's raw geometry is
    # released immediately, rather than holding the whole dataset until return.
    elements = data.get("elements", [])
    while elements:
        el = elements.pop()
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        pts = [transformer.transform(p["lon"], p["lat"]) for p in geom]

        if "building" in tags:
            # Live OOM backstop: the solid/LiDAR budgets are upfront estimates,
            # so we also watch actual RSS during the heavy building loop. At the
            # soft limit we stop minting solids; at the hard limit we abort
            # cleanly instead of being OOM-killed by Render.
            bldg_seen += 1
            if bldg_seen % MEMORY_CHECK_EVERY == 0:
                rss = current_rss_mb()
                if rss is not None:
                    if rss >= MEMORY_HARD_MB:
                        raise MemoryLimitError(
                            f"Memory ceiling reached mid-build ({rss:.0f} MB, "
                            f"limit {MEMORY_HARD_MB:.0f} MB) after {bldg_seen} "
                            f"of ~{buildings + 1}+ buildings."
                        )
                    if rss >= MEMORY_SOFT_MB and not force_mesh:
                        force_mesh = True
                        mem_soft_at = bldg_seen
                        print(f"[generate] stage=build soft memory limit "
                              f"{rss:.0f} MB at {bldg_seen} buildings — meshes "
                              f"only from here to slow growth.")
            base = (
                min(grid.sample(p["lon"], p["lat"]) for p in geom)
                if grid is not None
                else datum
            )
            # Per-building height + provenance: OSM tag > sanity-passed LiDAR >
            # estimate. The LiDAR sample is taken from the footprint's lon/lat
            # ring (same coords the grid uses), so it aligns with the model.
            lidar_h = None
            if lidar is not None:
                lidar_h, _npx = lidar.height_for([(p["lon"], p["lat"]) for p in geom])
                if lidar_h is not None:
                    lidar_covered += 1
            height, prov = rh.resolve_height(tags, pts, el.get("id", 0), lidar_h)
            prov_counts[prov] += 1
            real = prov != rh.PROV_ESTIMATED
            houses_layer = "BUILDINGS_houses_real" if real else "BUILDINGS_houses_estimated"
            blocks_layer = "BUILDINGS_blocks_real" if real else "BUILDINGS_blocks_estimated"
            if rh.is_house(tags, rh._footprint_area_m2(pts)):
                added = False
                if house_solids < solid_budget and not force_mesh:
                    solid = rh.house_solid(pts, base, height, solid_failures)
                else:
                    solid = None
                    house_budget_skips += 1
                if solid is not None:
                    model.Objects.AddBrep(solid, _attrs(cache[houses_layer]))
                    house_solids += 1
                    added = True
                else:
                    mesh = rh.house_mesh(pts, base, height)
                    if mesh is not None:
                        model.Objects.AddMesh(mesh, _attrs(cache[houses_layer]))
                        house_mesh_fallbacks += 1
                        added = True
                if added:
                    if len(base_z_samples) < 10:
                        base_z_samples.append(base)
                    buildings += 1
                    houses += 1
                    continue
                # else: fall through to the flat extrusion below
            curve = rh._closed_curve(_ensure_ccw(pts), base)
            if curve is None or not curve.IsClosed:
                continue
            extrusion = rhino3dm.Extrusion.Create(curve, height, True)
            if extrusion is None:
                continue
            # Profile lies in a horizontal plane, so positive height extrudes
            # +Z (upward) from the base level.
            model.Objects.AddExtrusion(extrusion, _attrs(cache[blocks_layer]))
            if len(base_z_samples) < 10:
                base_z_samples.append(base)
            buildings += 1

        elif "highway" in tags:
            layer = rh._road_layer(tags)
            if grid is not None:
                # Drape the road per-vertex so it follows the hillside.
                pl = rhino3dm.Polyline()
                for (x, y), p in zip(pts, geom):
                    pl.Add(x, y, grid.sample(p["lon"], p["lat"]) + rh.ROAD_Z)
                curve = pl.ToPolylineCurve() if pl.Count >= 2 else None
            else:
                curve = rh._open_curve(pts, rh.ROAD_Z)
            if curve is None:
                continue
            model.Objects.AddCurve(curve, _attrs(cache[layer]))
            roads += 1

        elif tags.get("natural") == "water" or "waterway" in tags:
            z_water = (
                min(grid.sample(p["lon"], p["lat"]) for p in geom)
                if grid is not None
                else datum
            )
            curve = rh._closed_curve(pts, z_water)
            if curve is None:
                continue
            model.Objects.AddCurve(curve, _attrs(cache["WATER"]))
            water += 1

    # Site boundary rectangle (reprojected bbox corners), flat at the datum.
    boundary = rh._closed_curve([transformer.transform(lon, lat) for lon, lat in corners_ll], datum)
    if boundary is not None:
        model.Objects.AddCurve(boundary, _attrs(cache["SITE_BOUNDARY"]))

    total = buildings + roads + water + (1 if boundary is not None else 0)
    return {
        "objects": total,
        "buildings": buildings,
        "houses": houses,
        "house_solids": house_solids,
        "house_mesh_fallbacks": house_mesh_fallbacks,
        "house_solid_budget": solid_budget,
        "house_budget_skips": house_budget_skips,
        "solid_failures": solid_failures,
        "roads": roads,
        "water": water,
        "base_z_samples": base_z_samples,
        "surface_layers": surface_layers,
        "prov_osm": prov_counts[rh.PROV_OSM],
        "prov_lidar": prov_counts[rh.PROV_LIDAR],
        "prov_estimated": prov_counts[rh.PROV_ESTIMATED],
        "lidar_covered": lidar_covered,
        "mem_soft_at": mem_soft_at,
    }


def _build_linework_group(
    model: rhino3dm.File3dm,
    data: dict[str, Any],
    transformer: Any,
    datum: float = FLAT_GROUND_Z,
    grid: ElevationGrid | None = None,
    surface_layers: dict[str, int] | None = None,
) -> dict[str, int]:
    """Populate the ``Linework/`` group from the DETAILED dataset, flat at ``datum``.

    The linework deliberately stays planar (a drawing to trace/measure in
    plan, not draped 3D noise — see TOPO_RATIONALE.md). With terrain on, the
    datum is the site's lowest elevation; otherwise Z=0. Layer naming reuses
    ``build_dxf.classify`` exactly; geometry is drawn as rhino3dm points
    (nodes) and polyline curves (ways) on that plane.

    The same pass also feeds the 3D surface differentiation (the detailed
    dataset is the only one with footways and green space): footways become
    draped polylines a kerb above ground on ``3D/PAVEMENTS``, and closed
    park/grass outlines become filled draped meshes on ``3D/GREENS``.
    """
    parent = _add_parent(model, "Linework", (180, 180, 180))
    cache: dict[str, int] = {}

    def resolve(layer_name: str) -> int:
        category = layer_name.split("_", 1)[0]
        rgb = LINEWORK_COLORS.get(category, (200, 200, 200))
        return _add_child(model, layer_name, parent, rgb, cache)

    def ground(p: dict[str, float]) -> float:
        return grid.sample(p["lon"], p["lat"]) if grid is not None else datum

    objects = pavements = greens = 0
    greens_planar = greens_mesh = 0
    # Drain the element list as we go so each parsed feature's raw geometry is
    # released immediately, rather than holding the whole dataset until return.
    elements = data.get("elements", [])
    while elements:
        el = elements.pop()
        tags = el.get("tags", {})
        if not tags:
            continue
        etype = el.get("type")

        if etype == "node":
            result = dxf.classify(tags, is_point=True)
            if result is None:
                continue
            layer, _closed = result
            x, y = transformer.transform(el["lon"], el["lat"])
            model.Objects.AddPoint(x, y, datum, _attrs(resolve(layer)))
            objects += 1

        elif etype == "way":
            geom = el.get("geometry")
            if not geom or len(geom) < 2:
                continue
            pts = [transformer.transform(p["lon"], p["lat"]) for p in geom]

            result = dxf.classify(tags, is_point=False)
            if result is not None:
                layer, closed = result
                curve = (rh._closed_curve(pts, datum) if closed
                         else rh._open_curve(pts, datum))
                if curve is not None:
                    model.Objects.AddCurve(curve, _attrs(resolve(layer)))
                    objects += 1

            if surface_layers is None:
                continue
            if tags.get("highway") in _PAVEMENT_HIGHWAYS:
                pl = rhino3dm.Polyline()
                for (x, y), p in zip(pts, geom):
                    pl.Add(x, y, ground(p) + PAVEMENT_RAISE_M)
                if pl.Count >= 2:
                    model.Objects.AddCurve(
                        pl.ToPolylineCurve(), _attrs(surface_layers["PAVEMENTS"]))
                    pavements += 1
            elif _is_green(tags) and len(geom) >= 4 and geom[0] == geom[-1]:
                raise_m = (GREEN_NESTED_RAISE_M
                           if tags.get("leisure") in _GREEN_NESTED
                           else GREEN_RAISE_M)
                # v6: effectively-flat greens become clean planar surfaces;
                # greens on real slopes keep the honest draped mesh.
                zs = [ground(p) for p in geom[:-1]]
                if max(zs) - min(zs) <= GREEN_PLANAR_MAX_RELIEF_M:
                    srf = _green_plane(pts[:-1], sum(zs) / len(zs) + raise_m)
                    if srf is not None:
                        model.Objects.AddBrep(srf, _attrs(surface_layers["GREENS"]))
                        greens += 1
                        greens_planar += 1
                        continue
                mesh = _green_mesh(pts[:-1], geom[:-1], ground, raise_m)
                if mesh is not None:
                    model.Objects.AddMesh(mesh, _attrs(surface_layers["GREENS"]))
                    greens += 1
                    greens_mesh += 1

    return {"objects": objects, "layers": len(cache),
            "pavements": pavements, "greens": greens,
            "greens_planar": greens_planar, "greens_mesh": greens_mesh}


def build_combined(
    area: str | None,
    out_path: str,
    bbox: tuple[float, float, float, float] | None = None,
    terrain: bool = True,
) -> dict[str, Any]:
    """Fetch all datasets and write one aligned combined ``.3dm`` to ``out_path``.

    Resolves the footprint from an explicit ``bbox`` (south, west, north, east)
    if given, otherwise geocodes ``area``. With ``terrain`` (default) the model
    gains the ``TERRAIN/`` group and the massing is draped onto the ground;
    without it, the original flat Z=0 model is produced. Returns a stats dict
    (object/layer counts per group, EPSG, display name) and runs a read-back
    verification of the structural invariants.
    """
    s, w, n, e, display_name = resolve_area(area, bbox)
    transformer, epsg = get_transformer(s, w, n, e)
    bbox = f"{s},{w},{n},{e}"
    corners_ll = [(w, s), (e, s), (e, n), (w, n)]

    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters

    # Terrain first: the grid is needed while draping the 3D massing, and the
    # flat-linework datum (lowest site elevation, floored) comes from it.
    grid = None
    datum = FLAT_GROUND_Z
    tstats: dict[str, Any] = {}
    if terrain:
        grid = fetch_elevation_grid(s, w, n, e)
        datum = float(math.floor(grid.zmin))
        tstats = add_terrain(model, grid, transformer)

    # Process the two OSM datasets sequentially so they never coexist in
    # memory: fetch the lean set, build the 3D massing (draped via the grid),
    # then drop it *before* fetching the much larger detailed set. The grid
    # itself is tiny (<=180x180 float32) and stays alive for draping the
    # pavement/green surfaces in the detailed pass. All queries share the
    # same bbox and transformer, so the groups stay aligned.
    lean = fetch_overpass(rh._QUERY_TEMPLATE.format(bbox=bbox))

    # Real building heights from free LiDAR (England), resolved per-building in
    # the 3D massing below. The raster is fetched here so it lives only across
    # the massing loop and is released before the heavier linework/write stages.
    # A MEMORY GOVERNOR sizes (or skips) it from the building count so the
    # augmented build stays inside the 512 MB tier; outside England the fetch
    # returns (None, ...) instantly. Either way every uncovered building falls
    # back to the v5 estimate — see fetch_lidar / build_rhino / the governor.
    n_buildings = sum(
        1 for el in lean.get("elements", [])
        if el.get("type") == "way" and "building" in el.get("tags", {}))

    # Upfront OOM guard: reject a site whose building count is beyond what the
    # 512 MB tier can build, with a clear error, BEFORE spending memory on
    # geometry and the (much larger) detailed fetch. Getting killed mid-process
    # is the failure mode we're eliminating here.
    if n_buildings > MAX_BUILDINGS:
        print(f"[generate] stage=validate reject: {n_buildings} buildings "
              f"exceeds the {MAX_BUILDINGS} limit for this server tier.")
        raise AreaTooLargeError(
            f"This area has {n_buildings:,} buildings — beyond the "
            f"{MAX_BUILDINGS:,} the free server tier can build. Try a smaller "
            f"box, or disable terrain."
        )

    budget_px = lidar_budget_px(n_buildings)
    if budget_px <= 0:
        lidar, lidar_info = None, {
            "available": False,
            "reason": (f"LiDAR skipped to stay within the free-tier memory "
                       f"budget on this large site ({n_buildings} buildings); "
                       f"heights are type-estimated. Full-resolution LiDAR on "
                       f"large sites is a Pro-tier feature."),
            "governed": True, "n_buildings": n_buildings}
    else:
        lidar, lidar_info = fetch_lidar_heights(s, w, n, e, max_px=budget_px)
        lidar_info["budget_px"] = budget_px

    g3d = _build_3d_group(model, lean, transformer, corners_ll, grid, datum, lidar)
    del lean
    if lidar is not None:
        lidar.release()
        del lidar
    gc.collect()

    detailed = fetch_overpass(dxf._QUERY_TEMPLATE.format(bbox=bbox))
    glin = _build_linework_group(model, detailed, transformer, datum,
                                 grid, g3d["surface_layers"])
    del detailed, grid
    gc.collect()

    model.Write(out_path, 0)

    # Read back and confirm the structural invariants — but only with headroom.
    # The read-back loads a SECOND full copy of the model alongside the live one,
    # which on a large dense site is exactly the spike that trips Render's OOM
    # killer. If RSS is already near the ceiling we skip the self-check (the file
    # is written and served either way) rather than risk being killed AFTER the
    # work is done.
    rss = current_rss_mb()
    if rss is not None and rss >= MEMORY_SOFT_MB:
        print(f"[generate] stage=write skipping read-back verification at "
              f"{rss:.0f} MB RSS to avoid an OOM spike.")
        del model
        gc.collect()
        # -1 = "not verified" (not counted); the groups we know we built.
        obj_count = layer_count = -1
        has_3d = has_linework = True
        has_terrain = terrain
        readback_skipped = True
    else:
        check = rhino3dm.File3dm.Read(out_path)
        obj_count = len(check.Objects)
        layer_count = len(check.Layers)
        groups = {lay.Name for lay in check.Layers}
        has_3d = "3D" in groups
        has_linework = "Linework" in groups
        has_terrain = "TERRAIN" in groups
        readback_skipped = False

    return {
        "display_name": display_name,
        "epsg": epsg,
        "readback_skipped": readback_skipped,
        "objects": obj_count,
        "layers": layer_count,
        "objects_3d": g3d["objects"],
        "objects_linework": glin["objects"],
        "buildings": g3d["buildings"],
        "houses": g3d["houses"],
        "house_solids": g3d["house_solids"],
        "house_mesh_fallbacks": g3d["house_mesh_fallbacks"],
        "house_solid_budget": g3d["house_solid_budget"],
        "house_budget_skips": g3d["house_budget_skips"],
        "solid_failures": g3d["solid_failures"],
        "roads": g3d["roads"],
        "water": g3d["water"],
        "pavements": glin["pavements"],
        "greens": glin["greens"],
        "greens_planar": glin["greens_planar"],
        "greens_mesh": glin["greens_mesh"],
        "base_z_samples": g3d["base_z_samples"],
        "datum": datum,
        "has_3d_group": has_3d,
        "has_linework_group": has_linework,
        "has_terrain_group": has_terrain,
        "terrain": tstats,
        # Height provenance (Option C): real = OSM tag or sanity-passed LiDAR.
        "prov_osm": g3d["prov_osm"],
        "prov_lidar": g3d["prov_lidar"],
        "prov_estimated": g3d["prov_estimated"],
        "lidar_covered": g3d["lidar_covered"],
        "lidar": lidar_info,
    }


if __name__ == "__main__":
    import sys
    import tracemalloc

    # Default test: Clifton, Bristol (real relief — the Avon Gorge) by explicit
    # bbox, so the test is deterministic. Pass an area name to override.
    area: str | None = " ".join(sys.argv[1:]) or None
    bbox_arg = None if area else (51.452, -2.633, 51.468, -2.605)
    out = "test_combined.3dm"

    tracemalloc.start()
    stats = build_combined(area, out, bbox_arg)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"Area            : {stats['display_name']}")
    print(f"EPSG            : {stats['epsg']}")
    print(f"Total objects   : {stats['objects']}  "
          f"(3D={stats['objects_3d']}, Linework={stats['objects_linework']})")
    print(f"Total layers    : {stats['layers']}")
    print(f"Groups present  : 3D={stats['has_3d_group']}  "
          f"Linework={stats['has_linework_group']}  TERRAIN={stats['has_terrain_group']}")
    print(f"Buildings       : {stats['buildings']}  (houses with pitched roofs: "
          f"{stats['houses']})  Roads: {stats['roads']}  Water: {stats['water']}")
    print(f"House solids    : {stats['house_solids']}  mesh fallbacks: "
          f"{stats['house_mesh_fallbacks']}  budget skips: "
          f"{stats['house_budget_skips']} (budget {stats['house_solid_budget']})")
    if stats["solid_failures"]:
        print(f"Solid failures  : {stats['solid_failures']}")
    print(f"Surfaces        : pavements={stats['pavements']}  greens={stats['greens']} "
          f"(planar breps={stats['greens_planar']}, draped meshes={stats['greens_mesh']})")
    print(f"Terrain         : {stats['terrain']}")
    print(f"Datum           : {stats['datum']}")
    print(f"tracemalloc peak: {peak / 1048576:.1f} MB")

    # Spatial-invariant checks across ALL objects: with terrain, building
    # bases sit at real ground heights (and the first bases match the lowest
    # sampled terrain under their footprints exactly); linework lies flat at
    # the datum; the terrain mesh and contours are present.
    model = rhino3dm.File3dm.Read(out)
    layers = {lay.Index: lay for lay in model.Layers}
    bldg_idx = {i for i, l in layers.items()
                if l.Name in ("BUILDINGS_houses", "BUILDINGS_blocks")}
    house_idx = next((i for i, l in layers.items() if l.Name == "BUILDINGS_houses"), None)
    surf_idx = next((i for i, l in layers.items() if l.Name == "surface"), None)
    cont_idx = next((i for i, l in layers.items() if l.Name == "contours"), None)
    pav_idx = next((i for i, l in layers.items() if l.Name == "PAVEMENTS"), None)
    grn_idx = next((i for i, l in layers.items() if l.Name == "GREENS"), None)
    bases: list[float] = []
    lin_zmax, lin_zmin = float("-inf"), float("inf")
    meshes = contours = house_objs = pavs = grns = 0
    house_breps_solid = house_breps_open = house_mesh_objs = 0
    blocks_solid = blocks_open = 0
    terrain_kind = "absent"
    green_kinds: dict[str, int] = {}
    for obj in model.Objects:
        li = obj.Attributes.LayerIndex
        geo = obj.Geometry
        bb = geo.GetBoundingBox()
        if li in bldg_idx:
            bases.append(bb.Min.Z)
            if li == house_idx:
                house_objs += 1
                if isinstance(geo, rhino3dm.Brep):
                    if geo.IsValid and geo.IsSolid:
                        house_breps_solid += 1
                    else:
                        house_breps_open += 1
                else:
                    house_mesh_objs += 1
            elif isinstance(geo, rhino3dm.Extrusion):
                if geo.IsSolid and geo.IsCappedAtTop and geo.IsCappedAtBottom:
                    blocks_solid += 1
                else:
                    blocks_open += 1
        elif li == surf_idx:
            meshes += 1
            terrain_kind = type(geo).__name__
        elif li == cont_idx:
            contours += 1
        elif li == pav_idx:
            pavs += 1
        elif li == grn_idx:
            grns += 1
            kind = type(geo).__name__
            green_kinds[kind] = green_kinds.get(kind, 0) + 1
        elif li in layers and layers[li].Name.endswith(("_PL", "_LN", "_PT")):
            lin_zmax = max(lin_zmax, bb.Max.Z)
            lin_zmin = min(lin_zmin, bb.Min.Z)
    print(f"Terrain object  : {meshes} ({terrain_kind})  contour curves: {contours}")
    print(f"Greens read-back: {green_kinds}")
    print(f"Houses read-back: {house_objs} (solid breps={house_breps_solid}, "
          f"INVALID/open breps={house_breps_open}, mesh fallbacks={house_mesh_objs})")
    print(f"Blocks read-back: solid capped extrusions={blocks_solid}, "
          f"NOT solid={blocks_open}")
    print(f"Surfaces        : pavements={pavs}  greens={grns} (read-back)")
    print(f"Building bases  : min={min(bases):.1f}  max={max(bases):.1f}  "
          f"distinct={len({round(b, 2) for b in bases})}  (expect stepping, not all 0)")
    expected = stats["base_z_samples"]
    # NOTE: buildings are added in pop() order; compare the LAST n building
    # objects' recorded order isn't stable across read-back, so check the
    # recorded sample bases all appear among the read-back bases instead.
    base_set = {round(b, 2) for b in bases}
    matched = sum(1 for b in expected if round(b, 2) in base_set)
    print(f"Recorded bases  : {matched}/{len(expected)} found in read-back")
    print(f"Linework Z      : [{lin_zmin}, {lin_zmax}]  (expect flat at datum "
          f"{stats['datum']})")
