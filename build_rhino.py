"""SiteGrab 3D Rhino writer.

Produces a LEAN .3dm massing model from OpenStreetMap data using rhino3dm.
Intentionally minimal: buildings (extruded solids), main roads (raised
polylines) and water (flat closed curves) only. No footpaths, trees, landuse
or amenities — a massing model must stay uncluttered.

The model sits at full real-world UTM coordinates (hundreds of thousands of
metres from the origin). That is correct and intentional; it is NOT recentred.
"""

from __future__ import annotations

import math
from typing import Any

import rhino3dm

from fetch_core import fetch_overpass, get_transformer, resolve_area

LEVEL_HEIGHT_M = 3.3
ROAD_Z = 0.5  # raise roads slightly above ground plane

# ---------------------------------------------------------------------------
# Building heights: HONESTY DISCIPLINE (see MASSING_NOTES.md).
# OSM has real heights for only a minority of buildings. A real `height` or
# `building:levels` tag ALWAYS wins. Everything below is plausible TYPE-DRIVEN
# ESTIMATION to make the model legible — never surveyed data.
# ---------------------------------------------------------------------------

# Clearly-residential house types (eligible for pitched roofs).
_HOUSE_TYPES: set[str] = {"house", "detached", "semidetached_house", "bungalow", "terrace"}

# building= type -> estimated height (m). Storey logic in MASSING_NOTES.md.
_TYPE_HEIGHTS: dict[str, float] = {
    "bungalow": 3.8,
    "house": 5.8, "detached": 6.0, "semidetached_house": 5.8,
    "terrace": 6.8,
    "residential": 12.0, "apartments": 16.0, "dormitory": 14.0,
    "commercial": 18.0, "office": 22.0,
    "retail": 6.0, "supermarket": 7.0, "kiosk": 3.0,
    "warehouse": 9.0, "industrial": 9.0, "factory": 9.0, "barn": 7.0,
    "shed": 2.8, "hut": 2.8, "garage": 2.8, "garages": 2.8, "carport": 2.5,
    "greenhouse": 3.0, "service": 4.0,
    "civic": 14.0, "public": 14.0, "government": 16.0,
    "school": 11.0, "university": 16.0, "hospital": 18.0,
    "church": 15.0, "chapel": 10.0, "mosque": 16.0, "temple": 14.0,
    "synagogue": 14.0, "cathedral": 30.0,
    "hotel": 28.0, "tower": 40.0,
    "train_station": 12.0, "construction": 8.0,
}


def _footprint_area_m2(pts_xy: list[tuple[float, float]]) -> float:
    """Shoelace area of the (UTM, metres) footprint polygon."""
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts_xy, pts_xy[1:] + pts_xy[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _real_height(tags: dict[str, str]) -> float | None:
    """Real OSM data: `height` -> `building:levels` x 3.3m. None if absent."""
    if "height" in tags:
        try:
            return float(str(tags["height"]).split()[0].replace(",", "."))
        except (ValueError, IndexError):
            pass
    if "building:levels" in tags:
        try:
            return float(str(tags["building:levels"]).split()[0]) * LEVEL_HEIGHT_M
        except (ValueError, IndexError):
            pass
    return None


def _estimate_height(btype: str, area: float) -> float:
    """Type-driven estimate, with footprint-aware fallback and sanity clamps."""
    h = _TYPE_HEIGHTS.get(btype)
    if h is None:
        # Unknown / building=yes: infer scale from the footprint alone.
        if area < 90:
            h = 5.5     # house-scale
        elif area < 300:
            h = 9.0
        elif area < 1500:
            h = 13.0    # mid-rise
        else:
            h = 9.0     # big footprint + no tags reads as a shed, not a slab
    # Footprint sanity: a 40m2 hut is never an office tower; a 5000m2 box is
    # never a bungalow.
    if area < 100:
        h = min(h, 10.0)
    if area > 3000:
        h = max(h, 8.0)
    return h


def _jitter(h: float, osm_id: int) -> float:
    """+/-4% deterministic variation seeded by the OSM way id (stable re-runs)."""
    frac = ((osm_id * 1103515245 + 12345) % 2**31) / 2**31
    return h * (0.96 + 0.08 * frac)


def building_height(
    tags: dict[str, str], pts_xy: list[tuple[float, float]], osm_id: int
) -> tuple[float, bool]:
    """Building height in metres -> (height, is_estimated).

    Real `height`/`building:levels` data always wins, unjittered. Estimates
    (and only estimates) get the type/footprint logic plus gentle jitter.
    """
    real = _real_height(tags)
    if real is not None:
        return real, False
    area = _footprint_area_m2(pts_xy)
    return _jitter(_estimate_height(tags.get("building", "yes"), area), osm_id), True


# ---------------------------------------------------------------------------
# Pitched roofs: clearly-residential houses ONLY (see MASSING_NOTES.md §3).
# A cheap generalised hip — ridge along the footprint's principal axis, each
# eaves edge roofed to its projection on the ridge. No per-building roof
# analysis; everything that isn't a house stays a flat-topped extrusion.
# ---------------------------------------------------------------------------

ROOF_PITCH_DEG = 30.0
ROOF_MIN_RISE_M = 1.2
ROOF_MAX_RISE_M = 4.0
# Footprint window for roofing: under ~30m2 is an outbuilding; over ~400m2 a
# "terrace"-tagged way is a whole row and one mega-gable would be absurd.
HOUSE_ROOF_MIN_AREA = 30.0
HOUSE_ROOF_MAX_AREA = 400.0


def is_house(tags: dict[str, str], area_m2: float) -> bool:
    """True for a clearly-residential house with a house-sized footprint."""
    return (
        tags.get("building") in _HOUSE_TYPES
        and HOUSE_ROOF_MIN_AREA <= area_m2 <= HOUSE_ROOF_MAX_AREA
    )


def _principal_obb(
    pts: list[tuple[float, float]],
) -> tuple[tuple[float, float], tuple[float, float], float, float] | None:
    """Minimum-area oriented bounding box, tested over the edge directions.

    Returns (centre, major-axis unit vector, half_length, half_width), with
    the major axis always the longer dimension. None for degenerate input.
    """
    best = None
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        ex, ey = x2 - x1, y2 - y1
        d = math.hypot(ex, ey)
        if d < 1e-9:
            continue
        ux, uy = ex / d, ey / d
        us = [px * ux + py * uy for px, py in pts]
        vs = [py * ux - px * uy for px, py in pts]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        a = (u1 - u0) * (v1 - v0)
        if best is None or a < best[0]:
            best = (a, ux, uy, u0, u1, v0, v1)
    if best is None:
        return None
    _, ux, uy, u0, u1, v0, v1 = best
    if (u1 - u0) < (v1 - v0):
        # Rotate the frame 90 degrees so u is the long axis: u' = v, v' = -u.
        ux, uy = -uy, ux
        u0, u1, v0, v1 = v0, v1, -u1, -u0
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    centre = (cu * ux - cv * uy, cu * uy + cv * ux)
    return centre, (ux, uy), (u1 - u0) / 2, (v1 - v0) / 2


def house_mesh(
    pts_xy: list[tuple[float, float]], base: float, height: float
) -> rhino3dm.Mesh | None:
    """Hipped-roof house mesh, or None (caller falls back to a flat extrusion).

    Walls rise from ``base`` to eaves; each eaves edge is roofed to its
    projection on a ridge along the footprint's principal axis (square plans
    collapse to a pyramid). The ridge tops out at ``base + height``, so a
    real OSM ``height`` keeps meaning height-to-ridge. The underside is left
    open — it sits in/on the ground and is never seen.
    """
    pts = list(pts_xy)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    # CCW winding so the wall faces point outward.
    signed2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    if signed2 < 0:
        pts = pts[::-1]
    obb = _principal_obb(pts)
    if obb is None:
        return None
    (cx, cy), (ux, uy), half_len, half_wid = obb
    if half_wid < 0.8:  # sliver footprint: a flat top reads better
        return None
    rise = math.tan(math.radians(ROOF_PITCH_DEG)) * half_wid
    rise = min(max(rise, ROOF_MIN_RISE_M), ROOF_MAX_RISE_M)
    rise = min(rise, height - 2.2)  # keep at least a storey of wall
    if rise < 0.5:
        return None
    eaves_z = base + height - rise
    ridge_z = base + height
    half_ridge = max(half_len - half_wid, 0.0)  # 45-degree hip ends
    rx0, ry0 = cx - ux * half_ridge, cy - uy * half_ridge

    mesh = rhino3dm.Mesh()
    n = len(pts)
    for x, y in pts:
        mesh.Vertices.Add(x, y, base)
    for x, y in pts:
        mesh.Vertices.Add(x, y, eaves_z)
    ridge_pts: list[tuple[float, float]] = []
    for x, y in pts:
        t = (x - rx0) * ux + (y - ry0) * uy
        t = min(max(t, 0.0), 2 * half_ridge)
        ridge_pts.append((rx0 + ux * t, ry0 + uy * t))
        mesh.Vertices.Add(ridge_pts[-1][0], ridge_pts[-1][1], ridge_z)
    for i in range(n):
        j = (i + 1) % n
        mesh.Faces.AddFace(i, j, n + j, n + i)  # wall quad
        ri, rj = ridge_pts[i], ridge_pts[j]
        if abs(ri[0] - rj[0]) < 1e-9 and abs(ri[1] - rj[1]) < 1e-9:
            mesh.Faces.AddFace(n + i, n + j, 2 * n + j)  # roof triangle
        else:
            mesh.Faces.AddFace(n + i, n + j, 2 * n + j, 2 * n + i)  # roof quad
    mesh.Normals.ComputeNormals()
    return mesh

# Lean query: buildings, main roads, water only.
_QUERY_TEMPLATE = """
[out:json][timeout:180];
(
  way["building"]({bbox});
  way["highway"~"motorway|trunk|primary|secondary|tertiary"]({bbox});
  way["natural"="water"]({bbox});
  way["waterway"~"canal|dock|riverbank"]({bbox});
);
out geom;
"""

# Houses (pitched meshes) and blocks (flat extrusions) get separate layers so
# the residential grain is selectable on its own.
_LAYERS: dict[str, tuple[int, int, int]] = {
    "BUILDINGS_houses": (190, 125, 95),
    "BUILDINGS_blocks": (150, 150, 150),
    "ROADS_primary": (60, 60, 60),
    "ROADS_secondary": (120, 120, 120),
    "WATER": (90, 150, 220),
    "SITE_BOUNDARY": (255, 140, 0),
}


def _closed_curve(pts_xy: list[tuple[float, float]], z: float) -> rhino3dm.PolylineCurve | None:
    """Build a closed planar PolylineCurve from XY points at height ``z``."""
    if len(pts_xy) < 3:
        return None
    pl = rhino3dm.Polyline()
    for x, y in pts_xy:
        pl.Add(x, y, z)
    # Ensure the profile is closed.
    if pts_xy[0] != pts_xy[-1]:
        pl.Add(pts_xy[0][0], pts_xy[0][1], z)
    if pl.Count < 4:
        return None
    return pl.ToPolylineCurve()


def _open_curve(pts_xy: list[tuple[float, float]], z: float) -> rhino3dm.PolylineCurve | None:
    if len(pts_xy) < 2:
        return None
    pl = rhino3dm.Polyline()
    for x, y in pts_xy:
        pl.Add(x, y, z)
    return pl.ToPolylineCurve()


def _road_layer(tags: dict[str, str]) -> str:
    hw = tags.get("highway", "")
    if hw in ("motorway", "trunk", "primary"):
        return "ROADS_primary"
    return "ROADS_secondary"


def build_rhino(
    area: str | None,
    out_path: str,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Fetch + build a lean 3D .3dm and write it to ``out_path``.

    Resolves the footprint from an explicit ``bbox`` (south, west, north, east)
    if given, otherwise geocodes ``area``. Returns a stats dict.
    """
    s, w, n, e, display_name = resolve_area(area, bbox)
    transformer, epsg = get_transformer(s, w, n, e)
    bbox = f"{s},{w},{n},{e}"
    data = fetch_overpass(_QUERY_TEMPLATE.format(bbox=bbox))

    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters

    layer_index: dict[str, int] = {}
    for name, (r, g, b) in _LAYERS.items():
        lay = rhino3dm.Layer()
        lay.Name = name
        lay.Color = (r, g, b, 255)
        layer_index[name] = model.Layers.Add(lay)

    def attrs(layer: str) -> rhino3dm.ObjectAttributes:
        a = rhino3dm.ObjectAttributes()
        a.LayerIndex = layer_index[layer]
        return a

    buildings = roads = water = 0

    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        pts = [transformer.transform(p["lon"], p["lat"]) for p in geom]

        if "building" in tags:
            height, _estimated = building_height(tags, pts, el.get("id", 0))
            if is_house(tags, _footprint_area_m2(pts)):
                mesh = house_mesh(pts, 0.0, height)
                if mesh is not None:
                    model.Objects.AddMesh(mesh, attrs("BUILDINGS_houses"))
                    buildings += 1
                    continue
            curve = _closed_curve(pts, 0.0)
            if curve is None or not curve.IsClosed:
                continue
            extrusion = rhino3dm.Extrusion.Create(curve, height, True)
            if extrusion is None:
                continue
            # Profile lies in the XY plane, so positive height extrudes +Z (upward).
            model.Objects.AddExtrusion(extrusion, attrs("BUILDINGS_blocks"))
            buildings += 1

        elif "highway" in tags:
            layer = _road_layer(tags)
            curve = _open_curve(pts, ROAD_Z)
            if curve is None:
                continue
            model.Objects.AddCurve(curve, attrs(layer))
            roads += 1

        elif tags.get("natural") == "water" or "waterway" in tags:
            curve = _closed_curve(pts, 0.0)
            if curve is None:
                continue
            model.Objects.AddCurve(curve, attrs("WATER"))
            water += 1

    # Site boundary rectangle from bbox corners (reprojected), flat at Z=0.
    corners = [transformer.transform(lon, lat)
               for lon, lat in [(w, s), (e, s), (e, n), (w, n)]]
    boundary = _closed_curve(corners, 0.0)
    if boundary is not None:
        model.Objects.AddCurve(boundary, attrs("SITE_BOUNDARY"))

    model.Write(out_path, 0)

    # Read back to confirm object and layer counts before returning.
    check = rhino3dm.File3dm.Read(out_path)
    obj_count = len(check.Objects)
    layer_count = len(check.Layers)

    return {
        "display_name": display_name,
        "epsg": epsg,
        "objects": obj_count,
        "layers": layer_count,
        "buildings": buildings,
        "roads": roads,
        "water": water,
    }


if __name__ == "__main__":
    import sys

    area = " ".join(sys.argv[1:]) or "Dubai Marina"
    stats = build_rhino(area, "test_marina.3dm")
    print(f"Area      : {stats['display_name']}")
    print(f"EPSG      : {stats['epsg']}")
    print(f"Objects   : {stats['objects']}")
    print(f"Layers    : {stats['layers']}")
    print(f"Buildings : {stats['buildings']}  Roads: {stats['roads']}  Water: {stats['water']}")
