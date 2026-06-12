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
from fetch_core import fetch_overpass, get_transformer, resolve_area
from fetch_elevation import ElevationGrid, fetch_elevation_grid

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
) -> dict[str, Any]:
    """Populate the ``3D/`` group from the LEAN dataset, reusing build_rhino logic.

    With an elevation ``grid``, each building's base is set to the LOWEST
    ground height under its footprint (the building stays plumb and cuts into
    the slope — see TOPO_RATIONALE.md), main roads drape per-vertex, and water
    sits flat at the lowest ground under its outline. Without a grid the
    original flat behaviour at ``datum`` (Z=0) is unchanged.
    """
    parent = _add_parent(model, "3D", (200, 200, 200))
    cache: dict[str, int] = {}
    for nm, rgb in rh._LAYERS.items():  # pre-create the massing layers
        _add_child(model, nm, parent, rgb, cache)
    # Ground-surface layers, filled during the later detailed pass (the lean
    # dataset has no footways or green space).
    surface_layers = {
        "PAVEMENTS": _add_child(model, "PAVEMENTS", parent, (205, 200, 190), cache),
        "GREENS": _add_child(model, "GREENS", parent, (110, 185, 100), cache),
    }

    buildings = houses = roads = water = 0
    house_solids = house_mesh_fallbacks = 0
    solid_failures: dict[str, int] = {}
    base_z_samples: list[float] = []  # first few building bases, for verification
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
            base = (
                min(grid.sample(p["lon"], p["lat"]) for p in geom)
                if grid is not None
                else datum
            )
            height, _estimated = rh.building_height(tags, pts, el.get("id", 0))
            if rh.is_house(tags, rh._footprint_area_m2(pts)):
                added = False
                solid = rh.house_solid(pts, base, height, solid_failures)
                if solid is not None:
                    model.Objects.AddBrep(solid, _attrs(cache["BUILDINGS_houses"]))
                    house_solids += 1
                    added = True
                else:
                    mesh = rh.house_mesh(pts, base, height)
                    if mesh is not None:
                        model.Objects.AddMesh(mesh, _attrs(cache["BUILDINGS_houses"]))
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
            model.Objects.AddExtrusion(extrusion, _attrs(cache["BUILDINGS_blocks"]))
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
        "solid_failures": solid_failures,
        "roads": roads,
        "water": water,
        "base_z_samples": base_z_samples,
        "surface_layers": surface_layers,
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
                mesh = _green_mesh(pts[:-1], geom[:-1], ground, raise_m)
                if mesh is not None:
                    model.Objects.AddMesh(mesh, _attrs(surface_layers["GREENS"]))
                    greens += 1

    return {"objects": objects, "layers": len(cache),
            "pavements": pavements, "greens": greens}


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
    g3d = _build_3d_group(model, lean, transformer, corners_ll, grid, datum)
    del lean
    gc.collect()

    detailed = fetch_overpass(dxf._QUERY_TEMPLATE.format(bbox=bbox))
    glin = _build_linework_group(model, detailed, transformer, datum,
                                 grid, g3d["surface_layers"])
    del detailed, grid
    gc.collect()

    model.Write(out_path, 0)

    # Read back and confirm the structural invariants.
    check = rhino3dm.File3dm.Read(out_path)
    obj_count = len(check.Objects)
    layer_count = len(check.Layers)
    groups = {lay.Name for lay in check.Layers}
    has_3d = "3D" in groups
    has_linework = "Linework" in groups
    has_terrain = "TERRAIN" in groups

    return {
        "display_name": display_name,
        "epsg": epsg,
        "objects": obj_count,
        "layers": layer_count,
        "objects_3d": g3d["objects"],
        "objects_linework": glin["objects"],
        "buildings": g3d["buildings"],
        "houses": g3d["houses"],
        "house_solids": g3d["house_solids"],
        "house_mesh_fallbacks": g3d["house_mesh_fallbacks"],
        "solid_failures": g3d["solid_failures"],
        "roads": g3d["roads"],
        "water": g3d["water"],
        "pavements": glin["pavements"],
        "greens": glin["greens"],
        "base_z_samples": g3d["base_z_samples"],
        "datum": datum,
        "has_3d_group": has_3d,
        "has_linework_group": has_linework,
        "has_terrain_group": has_terrain,
        "terrain": tstats,
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
          f"{stats['house_mesh_fallbacks']}")
    if stats["solid_failures"]:
        print(f"Solid failures  : {stats['solid_failures']}")
    print(f"Surfaces        : pavements={stats['pavements']}  greens={stats['greens']}")
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
        elif li == cont_idx:
            contours += 1
        elif li == pav_idx:
            pavs += 1
        elif li == grn_idx:
            grns += 1
        elif li in layers and layers[li].Name.endswith(("_PL", "_LN", "_PT")):
            lin_zmax = max(lin_zmax, bb.Max.Z)
            lin_zmin = min(lin_zmin, bb.Min.Z)
    print(f"Terrain mesh    : {meshes}  contour curves: {contours}")
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
