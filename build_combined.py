"""SiteGrab combined 3D + 2D Rhino writer.

Produces a single ``.3dm`` that merges the lean 3D massing and the detailed 2D
linework into one spatially aligned Rhino model:

- The **2D linework sits flat at Z=0** as a readable ground plane.
- The **3D massing sits above it**, buildings extruded upward from Z=0.
- Both datasets are reprojected with the *same* WGS84 -> UTM transformer (the
  one auto-detected from the bbox centroid), so alignment is automatic. Neither
  dataset is recentred or rescaled.

The model is organised into two layer groups:

- ``3D/``        -> BUILDINGS_extruded, ROADS_primary, ROADS_secondary, WATER, SITE_BOUNDARY
- ``Linework/``  -> the granular DXF-style layers (BLDG_residential_PL, ROADS_footpath_LN, ...)

The genuinely subtle logic is *reused, not rewritten*: building-height parsing,
curve construction and extrusion come from ``build_rhino``; layer classification
comes from ``build_dxf``. This module only orchestrates them into one model.
"""

from __future__ import annotations

import gc
import uuid
from typing import Any

import rhino3dm

import build_dxf as dxf
import build_rhino as rh
from fetch_core import fetch_overpass, get_transformer, resolve_area

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

GROUND_Z = 0.0  # linework + building footprints sit on the ground plane


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
) -> dict[str, int]:
    """Populate the ``3D/`` group from the LEAN dataset, reusing build_rhino logic."""
    parent = _add_parent(model, "3D", (200, 200, 200))
    cache: dict[str, int] = {}
    for nm, rgb in rh._LAYERS.items():  # pre-create the five massing layers
        _add_child(model, nm, parent, rgb, cache)

    buildings = roads = water = 0
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
            curve = rh._closed_curve(_ensure_ccw(pts), GROUND_Z)
            if curve is None or not curve.IsClosed:
                continue
            height = rh._parse_height(tags)
            extrusion = rhino3dm.Extrusion.Create(curve, height, True)
            if extrusion is None:
                continue
            # Profile lies in the XY plane, so positive height extrudes +Z (upward).
            model.Objects.AddExtrusion(extrusion, _attrs(cache["BUILDINGS_extruded"]))
            buildings += 1

        elif "highway" in tags:
            layer = rh._road_layer(tags)
            curve = rh._open_curve(pts, rh.ROAD_Z)
            if curve is None:
                continue
            model.Objects.AddCurve(curve, _attrs(cache[layer]))
            roads += 1

        elif tags.get("natural") == "water" or "waterway" in tags:
            curve = rh._closed_curve(pts, GROUND_Z)
            if curve is None:
                continue
            model.Objects.AddCurve(curve, _attrs(cache["WATER"]))
            water += 1

    # Site boundary rectangle (reprojected bbox corners), flat at Z=0.
    boundary = rh._closed_curve([transformer.transform(lon, lat) for lon, lat in corners_ll], GROUND_Z)
    if boundary is not None:
        model.Objects.AddCurve(boundary, _attrs(cache["SITE_BOUNDARY"]))

    total = buildings + roads + water + (1 if boundary is not None else 0)
    return {"objects": total, "buildings": buildings, "roads": roads, "water": water}


def _build_linework_group(
    model: rhino3dm.File3dm,
    data: dict[str, Any],
    transformer: Any,
) -> dict[str, int]:
    """Populate the ``Linework/`` group from the DETAILED dataset, flat at Z=0.

    Layer naming reuses ``build_dxf.classify`` exactly; geometry is drawn as
    rhino3dm points (nodes) and polyline curves (ways) on the ground plane.
    """
    parent = _add_parent(model, "Linework", (180, 180, 180))
    cache: dict[str, int] = {}

    def resolve(layer_name: str) -> int:
        category = layer_name.split("_", 1)[0]
        rgb = LINEWORK_COLORS.get(category, (200, 200, 200))
        return _add_child(model, layer_name, parent, rgb, cache)

    objects = 0
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
            model.Objects.AddPoint(x, y, GROUND_Z, _attrs(resolve(layer)))
            objects += 1

        elif etype == "way":
            geom = el.get("geometry")
            if not geom or len(geom) < 2:
                continue
            result = dxf.classify(tags, is_point=False)
            if result is None:
                continue
            layer, closed = result
            pts = [transformer.transform(p["lon"], p["lat"]) for p in geom]
            curve = (rh._closed_curve(pts, GROUND_Z) if closed
                     else rh._open_curve(pts, GROUND_Z))
            if curve is None:
                continue
            model.Objects.AddCurve(curve, _attrs(resolve(layer)))
            objects += 1

    return {"objects": objects, "layers": len(cache)}


def build_combined(
    area: str | None,
    out_path: str,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Fetch both datasets and write one aligned combined ``.3dm`` to ``out_path``.

    Resolves the footprint from an explicit ``bbox`` (south, west, north, east)
    if given, otherwise geocodes ``area``. Returns a stats dict (object/layer
    counts per group, EPSG, display name) and runs a read-back verification of
    the structural invariants.
    """
    s, w, n, e, display_name = resolve_area(area, bbox)
    transformer, epsg = get_transformer(s, w, n, e)
    bbox = f"{s},{w},{n},{e}"
    corners_ll = [(w, s), (e, s), (e, n), (w, n)]

    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters

    # Process the two datasets sequentially so they never coexist in memory:
    # fetch the lean set, build the 3D massing, then drop it *before* fetching
    # the much larger detailed set. Both queries share the same bbox and
    # transformer, so the resulting groups stay automatically aligned.
    lean = fetch_overpass(rh._QUERY_TEMPLATE.format(bbox=bbox))
    g3d = _build_3d_group(model, lean, transformer, corners_ll)
    del lean
    gc.collect()

    detailed = fetch_overpass(dxf._QUERY_TEMPLATE.format(bbox=bbox))
    glin = _build_linework_group(model, detailed, transformer)
    del detailed
    gc.collect()

    model.Write(out_path, 0)

    # Read back and confirm the structural invariants.
    check = rhino3dm.File3dm.Read(out_path)
    obj_count = len(check.Objects)
    layer_count = len(check.Layers)
    groups = {lay.Name for lay in check.Layers}
    has_3d = "3D" in groups
    has_linework = "Linework" in groups

    return {
        "display_name": display_name,
        "epsg": epsg,
        "objects": obj_count,
        "layers": layer_count,
        "objects_3d": g3d["objects"],
        "objects_linework": glin["objects"],
        "buildings": g3d["buildings"],
        "roads": g3d["roads"],
        "water": g3d["water"],
        "has_3d_group": has_3d,
        "has_linework_group": has_linework,
    }


if __name__ == "__main__":
    import sys

    area = " ".join(sys.argv[1:]) or "Dubai Marina"
    out = "test_combined.3dm"
    stats = build_combined(area, out)
    print(f"Area            : {stats['display_name']}")
    print(f"EPSG            : {stats['epsg']}")
    print(f"Total objects   : {stats['objects']}  "
          f"(3D={stats['objects_3d']}, Linework={stats['objects_linework']})")
    print(f"Total layers    : {stats['layers']}")
    print(f"Groups present  : 3D={stats['has_3d_group']}  Linework={stats['has_linework_group']}")
    print(f"Buildings       : {stats['buildings']}  Roads: {stats['roads']}  Water: {stats['water']}")

    # Spatial-invariant checks across ALL objects: every building extrudes
    # upward (Z-min == 0, Z-max > 0) and all linework lies flat at Z = 0.
    model = rhino3dm.File3dm.Read(out)
    layers = {lay.Index: lay for lay in model.Layers}
    bldg_idx = next((i for i, l in layers.items() if l.Name == "BUILDINGS_extruded"), None)
    bldg_zmax_min = float("inf")   # smallest top across all buildings
    bldg_zmin_min = float("inf")   # lowest point across all buildings
    lin_zmax = float("-inf")
    lin_zmin = float("inf")
    for obj in model.Objects:
        li = obj.Attributes.LayerIndex
        bb = obj.Geometry.GetBoundingBox()
        if li == bldg_idx:
            bldg_zmax_min = min(bldg_zmax_min, bb.Max.Z)
            bldg_zmin_min = min(bldg_zmin_min, bb.Min.Z)
        elif li in layers and layers[li].Name.endswith(("_PL", "_LN", "_PT")):
            lin_zmax = max(lin_zmax, bb.Max.Z)
            lin_zmin = min(lin_zmin, bb.Min.Z)
    print(f"Buildings  : lowest base Z={bldg_zmin_min}  smallest top Z={bldg_zmax_min}  "
          f"(expect base 0, top > 0)")
    print(f"Linework   : Z range [{lin_zmin}, {lin_zmax}]  (expect [0, 0])")
