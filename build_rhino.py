"""SiteGrab 3D Rhino writer.

Produces a LEAN .3dm massing model from OpenStreetMap data using rhino3dm.
Intentionally minimal: buildings (extruded solids), main roads (raised
polylines) and water (flat closed curves) only. No footpaths, trees, landuse
or amenities — a massing model must stay uncluttered.

The model sits at full real-world UTM coordinates (hundreds of thousands of
metres from the origin). That is correct and intentional; it is NOT recentred.
"""

from __future__ import annotations

import base64
import math
import struct
from typing import Any

import rhino3dm

from fetch_core import fetch_overpass, get_transformer, resolve_area

LEVEL_HEIGHT_M = 3.3
# Carriageways sit a hair BELOW the ground datum: the road is the lowest
# surface in the street section, with pavements a kerb (~120mm) above it —
# see the surface-differentiation constants in build_combined.
ROAD_Z = -0.05

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


def _house_frame(
    pts_xy: list[tuple[float, float]], base: float, height: float
) -> tuple[list[tuple[float, float]], float, float, list[tuple[float, float]]] | None:
    """Shared hip-roof setup -> (ccw_pts, eaves_z, ridge_z, ridge_pts), or None.

    Cleans the footprint (closing duplicate stripped, consecutive duplicates
    welded, CCW winding), finds the ridge along the principal axis and clamps
    the rise; ``ridge_pts[i]`` is footprint vertex i's projection onto the
    ridge segment. None means the caller should fall back to a flat extrusion
    (sliver footprint, no room for a roof, degenerate input).
    """
    pts = list(pts_xy)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    pts = [p for k, p in enumerate(pts)
           if math.hypot(p[0] - pts[k - 1][0], p[1] - pts[k - 1][1]) > 1e-6]
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
    ridge_pts: list[tuple[float, float]] = []
    for x, y in pts:
        t = (x - rx0) * ux + (y - ry0) * uy
        t = min(max(t, 0.0), 2 * half_ridge)
        ridge_pts.append((rx0 + ux * t, ry0 + uy * t))
    return pts, eaves_z, ridge_z, ridge_pts


def house_mesh(
    pts_xy: list[tuple[float, float]], base: float, height: float
) -> rhino3dm.Mesh | None:
    """Hipped-roof house mesh, or None (caller falls back to a flat extrusion).

    Walls rise from ``base`` to eaves; each eaves edge is roofed to its
    projection on a ridge along the footprint's principal axis (square plans
    collapse to a pyramid). The ridge tops out at ``base + height``, so a
    real OSM ``height`` keeps meaning height-to-ridge. The underside is left
    open — it sits in/on the ground and is never seen. Since v6 this is the
    FALLBACK when :func:`house_solid` cannot produce a watertight Brep, and
    shares its welded-ridge construction (v5's per-vertex ridge duplicates
    made Rhino flag many house meshes as degenerate).
    """
    frame = _house_frame(pts_xy, base, height)
    if frame is None:
        return None
    return _house_welded_mesh(frame, base, floor=False)


# ---------------------------------------------------------------------------
# v6: watertight house solids.
# rhino3dm can convert a closed mesh to a Brep (Brep.CreateFromMesh) but, unlike
# RhinoCommon, exposes no SetTolerancesBoxesAndFlags — the resulting Brep's edge
# tolerances stay at ON_UNSET_VALUE and Rhino REJECTS such breps as invalid
# (verified empirically: doc.Objects.AddBrep returns Guid.Empty). Our faces meet
# exactly at shared mesh vertices, so the true edge tolerance is 0.0; we set it
# by patching the unset sentinel inside the brep's serialized archive, which is
# safe because ON_UNSET_VALUE (-1.23432101234321e308) cannot occur as real data.
# ---------------------------------------------------------------------------

_ON_UNSET_BYTES = struct.pack("<d", -1.23432101234321e308)
_ZERO_BYTES = struct.pack("<d", 0.0)


def _fix_brep_tolerances(brep: rhino3dm.Brep) -> rhino3dm.Brep | None:
    """Replace unset edge/vertex tolerances with 0.0 via an archive round-trip."""
    enc = brep.Encode()
    data = base64.b64decode(enc["data"])
    if _ON_UNSET_BYTES not in data:
        return brep
    enc["data"] = base64.b64encode(
        data.replace(_ON_UNSET_BYTES, _ZERO_BYTES)).decode("ascii")
    return rhino3dm.CommonObject.Decode(enc)


def _earclip(pts: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple CCW polygon by ear clipping -> index triples.

    Handles the concave outlines parks and houses actually have. If clipping
    stalls (self-intersecting input), the remainder is fanned — visually
    acceptable for the rare degenerate way.
    """
    n = len(pts)
    if n < 3:
        return []

    def cross(o: int, a: int, b: int) -> float:
        return ((pts[a][0] - pts[o][0]) * (pts[b][1] - pts[o][1])
                - (pts[a][1] - pts[o][1]) * (pts[b][0] - pts[o][0]))

    signed = sum(x1 * y2 - x2 * y1
                 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    idx = list(range(n)) if signed >= 0 else list(range(n - 1, -1, -1))
    tris: list[tuple[int, int, int]] = []
    while len(idx) > 3:
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cross(a, b, c) <= 0:  # reflex corner, not an ear
                continue
            if any(cross(a, b, p) > 0 and cross(b, c, p) > 0 and cross(c, a, p) > 0
                   for p in idx if p not in (a, b, c)):
                continue
            tris.append((a, b, c))
            del idx[k]
            break
        else:  # no ear found: fan the rest and stop
            tris.extend((idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1))
            return tris
    tris.append((idx[0], idx[1], idx[2]))
    return tris


def _house_welded_mesh(
    frame: tuple[list[tuple[float, float]], float, float, list[tuple[float, float]]],
    base: float,
    floor: bool,
) -> rhino3dm.Mesh:
    """Hip-roof mesh over a :func:`_house_frame`, with welded ridge topology.

    The ridge is a SHARED ordered vertex sequence: coincident projections
    weld to one vertex (else the hip ends leave naked edges and degenerate
    faces), and every distinct projection becomes a ridge vertex that ALL
    roof faces passing over it must include — fanning across the in-between
    ridge vertices keeps the ridge free of T-junctions. With ``floor`` the
    footprint is triangulated in (faces down) so the mesh can close into a
    solid; without it the underside stays open (the display-mesh fallback).
    """
    pts, eaves_z, ridge_z, ridge_pts = frame

    mesh = rhino3dm.Mesh()
    n = len(pts)
    for x, y in pts:
        mesh.Vertices.Add(x, y, base)
    for x, y in pts:
        mesh.Vertices.Add(x, y, eaves_z)
    rx0, ry0 = ridge_pts[0]
    rdx = rdy = 0.0
    for x, y in ridge_pts[1:]:
        if abs(x - rx0) + abs(y - ry0) > 1e-9:
            d = math.hypot(x - rx0, y - ry0)
            rdx, rdy = (x - rx0) / d, (y - ry0) / d
            break
    ridge_t: list[float] = []        # parameter of each footprint vertex's projection
    uniq: dict[float, int] = {}      # rounded parameter -> mesh vertex index
    for x, y in ridge_pts:
        # Weld at millimetre scale: at UTM coordinate magnitudes, projections
        # closer than that collapse to coincident vertex locations and Rhino
        # flags the touching faces as degenerate.
        t = round((x - rx0) * rdx + (y - ry0) * rdy, 3)
        if t not in uniq:
            uniq[t] = 2 * n + len(uniq)
            mesh.Vertices.Add(x, y, ridge_z)
        ridge_t.append(t)
    ordered_t = sorted(uniq)         # ridge vertices in axis order

    for i in range(n):
        j = (i + 1) % n
        mesh.Faces.AddFace(i, j, n + j, n + i)  # wall quad (planar: vertical)
        ti, tj = ridge_t[i], ridge_t[j]
        if ti == tj:
            mesh.Faces.AddFace(n + i, n + j, uniq[tj])  # hip-end triangle
            continue
        # Ridge vertices this roof face passes over, walked from ti to tj.
        lo, hi = min(ti, tj), max(ti, tj)
        span = [t for t in ordered_t if lo <= t <= hi]
        if tj < ti:
            span.reverse()
        ring = [uniq[t] for t in span]  # ring[0] under vertex i, ring[-1] under j
        # Planar-quad fast path: eaves edge parallel to the ridge and nothing
        # in between (the common rectangle case keeps clean 4-sided faces).
        ex, ey = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
        if len(ring) == 2 and abs(ex * rdy - ey * rdx) < 1e-6 * math.hypot(ex, ey):
            mesh.Faces.AddFace(n + i, n + j, ring[1], ring[0])
            continue
        mesh.Faces.AddFace(n + i, n + j, ring[-1])  # to vertex j's projection
        for k in range(len(ring) - 1, 0, -1):       # walk the ridge back to i's
            mesh.Faces.AddFace(n + i, ring[k], ring[k - 1])
    if floor:
        # Floor: triangulated (footprints can be concave), wound to face down.
        for a, b, c in _earclip(pts):
            mesh.Faces.AddFace(c, b, a)
    mesh.Normals.ComputeNormals()
    return mesh


def house_solid(
    pts_xy: list[tuple[float, float]],
    base: float,
    height: float,
    failures: dict[str, int] | None = None,
) -> rhino3dm.Brep | None:
    """Watertight hipped-house Brep (walls + roof + floor), or None.

    Same geometry as :func:`house_mesh` plus a triangulated floor, built as a
    topologically CLOSED mesh: ridge projections that coincide (the clamped
    hip ends) are welded to one vertex, every distinct projection becomes a
    shared ridge vertex all passing roof faces fan across, and roof quads
    whose eaves edge is not parallel to the ridge — which would be non-planar
    faces — are split into triangles. The closed mesh becomes a Brep of
    trimmed planar faces, gets its tolerances fixed, and is returned only if
    rhino3dm agrees it is a valid closed solid; any failure returns None so
    the caller can fall back to the v5 mesh and REPORT it (never silently
    degrade the file). When ``failures`` is given, the failing stage is
    counted in it: the honesty log for "didn't close, and why".
    """
    def fail(reason: str) -> None:
        if failures is not None:
            failures[reason] = failures.get(reason, 0) + 1

    frame = _house_frame(pts_xy, base, height)
    if frame is None:
        fail("no roof frame (sliver/degenerate/too low)")
        return None

    mesh = _house_welded_mesh(frame, base, floor=True)
    if not mesh.IsClosed:
        # Almost always a concave footprint whose single-ridge hip roof
        # self-overlaps (ridge sub-segments covered by >2 faces). v5's open
        # mesh tolerated that; a clean solid needs a straight-skeleton roof
        # (v7 candidate). Fall back honestly rather than ship a wrong solid.
        fail("roof self-overlap on concave footprint (mesh did not close)")
        return None
    brep = rhino3dm.Brep.CreateFromMesh(mesh, True)
    if brep is None:
        fail("CreateFromMesh returned None")
        return None
    brep = _fix_brep_tolerances(brep)
    if brep is None:
        fail("tolerance fix round-trip failed")
        return None
    if not brep.IsValid:
        fail("brep invalid after conversion")
        return None
    if not brep.IsSolid:
        fail("brep not closed")
        return None
    return brep


# ---------------------------------------------------------------------------
# Free-tier memory governor for house solids. Measured on the canonical
# bench sites (bench.py, Windows peak working set, no tracemalloc):
#   - a house Brep costs ~90KB of transient C++ heap over the v5 mesh
#     (500-house spike: +43.6MB RSS / 500);
#   - the rest of the build peaks roughly linearly in building count:
#     ~196MB at 1.5k buildings (Dubai), ~220MB at 4.7k (Clifton), ~475MB
#     at 13k (Shoreditch) -> ~160MB base + ~25KB per building;
#   - v5 at Shoreditch (475MB on this metric) deploys successfully on the
#     512MB tier, so the budget targets staying AT OR BELOW that proven
#     level rather than the nominal 512.
# Houses convert to solids until the headroom is spent; the rest keep the
# (v6-welded) display mesh and the build stats report the skip count and
# the budget, never a silent downgrade.
# ---------------------------------------------------------------------------
SOLID_BUDGET_TARGET_MB = 440.0
SOLID_BASE_MB = 160.0
SOLID_PER_BUILDING_MB = 0.025
SOLID_PER_HOUSE_MB = 0.09


def house_solid_budget(n_buildings: int) -> int:
    """Max house solids for a site with ``n_buildings`` building footprints."""
    headroom = (SOLID_BUDGET_TARGET_MB
                - (SOLID_BASE_MB + SOLID_PER_BUILDING_MB * n_buildings))
    return max(0, int(headroom / SOLID_PER_HOUSE_MB))


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
    house_solids = house_mesh_fallbacks = house_budget_skips = 0
    solid_failures: dict[str, int] = {}
    solid_budget = house_solid_budget(sum(
        1 for el in data.get("elements", [])
        if el.get("type") == "way" and "building" in el.get("tags", {})))

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
                if house_solids < solid_budget:
                    solid = house_solid(pts, 0.0, height, solid_failures)
                else:
                    solid = None
                    house_budget_skips += 1
                if solid is not None:
                    model.Objects.AddBrep(solid, attrs("BUILDINGS_houses"))
                    buildings += 1
                    house_solids += 1
                    continue
                mesh = house_mesh(pts, 0.0, height)
                if mesh is not None:
                    model.Objects.AddMesh(mesh, attrs("BUILDINGS_houses"))
                    buildings += 1
                    house_mesh_fallbacks += 1
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
        "house_solids": house_solids,
        "house_mesh_fallbacks": house_mesh_fallbacks,
        "house_solid_budget": solid_budget,
        "house_budget_skips": house_budget_skips,
        "solid_failures": solid_failures,
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
