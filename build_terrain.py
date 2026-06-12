"""SiteGrab terrain builder.

Turns an :class:`fetch_elevation.ElevationGrid` into Rhino geometry, in the
SAME UTM coordinate system as the buildings and linework (the shared pyproj
transformer is passed in — nothing here builds its own projection):

- ``TERRAIN/surface``  — one rhino3dm mesh: grid vertices at (x, y, z) with
  quad faces. A mesh, not NURBS — honest about the ~10–30m DEM resolution.
- ``TERRAIN/contours`` — marching-squares contour polylines, each at its true
  elevation, at an interval auto-picked from the site's relief (1/2/5/10m...).

Ground-height queries for the building drape use ``ElevationGrid.sample`` in
(lon, lat) — the OSM geometry is still in lon/lat when draped, so no inverse
projection is needed. ``utm_sampler`` provides an (x, y) variant for callers
that only hold projected coordinates.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Callable

import numpy as np
import rhino3dm
from pyproj.enums import TransformDirection

from fetch_elevation import ElevationGrid

# Contour intervals tried smallest-first; the first giving <= MAX_CONTOURS
# levels wins, like picking 2m/5m/10m contours on a real site plan. 2m is the
# deliberate floor: SRTM-class vertical noise is several metres, so 1m
# contours on near-flat ground are speckle, not information.
_INTERVALS: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 50.0)
MAX_CONTOURS = 40

# Below this elevation range the site is flat: contours would be DEM noise.
MIN_RELIEF_M = 1.5

# Closed noise bumps in the DEM produce tiny contour loops; anything shorter
# than this total length (a couple of grid cells) is dropped as speckle.
MIN_CONTOUR_LEN_M = 40.0

TERRAIN_COLOR = (146, 116, 91)     # earth brown
CONTOUR_COLOR = (196, 148, 90)     # lighter sand

# ---------------------------------------------------------------------------
# v6: the terrain is offered as an editable NURBS surface that INTERPOLATES
# the elevation grid: control points are solved from the separable degree-3
# collocation system at the Greville abscissae, so the surface passes through
# every projected grid node exactly (a naive CVs=nodes fit smooths real
# relief by metres and never qualified). Node fit is therefore not the
# honesty question — RINGING is: a cubic interpolant can overshoot between
# samples at sharp steps (gorge lips, quay walls, urban DEM artifacts) and
# invent ground the data does not support. Every cell centre is checked and
# the build falls back to the mesh when the surface escapes the [min, max]
# band of the cell's four corner samples by more than this. Internal,
# deliberately not a user setting.
# ---------------------------------------------------------------------------
TERRAIN_SURFACE_MAX_RINGING_M = 1.0
# Guard only — current elevation fetch caps grids well below this.
TERRAIN_SURFACE_MAX_CVS = 40000
_NURBS_DEGREE = 3


def select_contour_interval(zmin: float, zmax: float) -> float | None:
    """Contour interval for the relief, or None for an effectively flat site."""
    rng = zmax - zmin
    if rng < MIN_RELIEF_M:
        return None
    for interval in _INTERVALS:
        if rng / interval <= MAX_CONTOURS:
            return interval
    return _INTERVALS[-1]


def utm_sampler(
    grid: ElevationGrid, transformer: Any
) -> Callable[[float, float], float]:
    """Ground height at projected (x, y), via the shared transformer's inverse."""

    def sample_xy(x: float, y: float) -> float:
        lon, lat = transformer.transform(x, y, direction=TransformDirection.INVERSE)
        return grid.sample(lon, lat)

    return sample_xy


def _project_nodes(
    grid: ElevationGrid, transformer: Any
) -> tuple[np.ndarray, np.ndarray]:
    """Reproject every grid node with the shared transformer -> (xs, ys), (H, W)."""
    lon2d, lat2d = np.meshgrid(grid.lons, grid.lats)
    xs, ys = transformer.transform(lon2d, lat2d)
    return xs, ys


def _greville(n: int) -> np.ndarray:
    """Greville abscissae for n CVs, clamped uniform knots (spacing 1), degree 3."""
    m = n - _NURBS_DEGREE
    knots = np.concatenate([[0.0, 0.0, 0.0], np.arange(1.0, m), [m, m, m]])
    return (knots[:n] + knots[1:n + 1] + knots[2:n + 2]) / 3.0


def _collocation_matrix(n: int) -> np.ndarray:
    """A[r, j] = N_j(greville_r) for the degree-3 clamped uniform basis.

    Cox-de Boor over the full-length knot vector (Rhino's stores one fewer
    knot at each end; the bases are identical). Banded, but at grid scale
    (n <= ~200) a dense solve is microseconds — not worth a band solver.
    """
    deg = _NURBS_DEGREE
    m = n - deg
    k = np.concatenate([[0.0] * (deg + 1), np.arange(1.0, m), [float(m)] * (deg + 1)])
    A = np.zeros((n, n))
    for r, t in enumerate(_greville(n)):
        N = np.zeros((n + deg, deg + 1))
        for j in range(n + deg - 1):
            if (k[j] <= t < k[j + 1]) or (t == k[-1] and k[j] < k[j + 1] == k[-1]):
                N[j, 0] = 1.0
        for d in range(1, deg + 1):
            for j in range(n + deg - d):
                a = 0.0
                if k[j + d] > k[j]:
                    a += (t - k[j]) / (k[j + d] - k[j]) * N[j, d - 1]
                if k[j + d + 1] > k[j + 1]:
                    a += ((k[j + d + 1] - t) / (k[j + d + 1] - k[j + 1])
                          * N[j + 1, d - 1])
                N[j, d] = a
        A[r] = N[:n, deg]
    return A


def _try_nurbs_surface(
    grid: ElevationGrid, xs: np.ndarray, ys: np.ndarray
) -> tuple[rhino3dm.NurbsSurface | None, float | None, str | None]:
    """Attempt the terrain as an interpolating degree-3 NURBS surface.

    Control points are solved so the surface passes through every projected
    grid node (x, y AND z are interpolated; u <-> lon index, v <-> lat
    index). Returns (surface, worst_ringing_m, None) on success or (None,
    worst_ringing_m, reason) when the honest answer is to keep the mesh.
    """
    h, w = grid.elev.shape
    order = _NURBS_DEGREE + 1
    if h < order or w < order:
        return None, None, f"grid {w}x{h} too small for a degree-3 surface"
    if h * w > TERRAIN_SURFACE_MAX_CVS:
        return None, None, f"{h * w} control points exceed the memory guard"

    # Separable collocation: solve along u for every row (batched RHS), then
    # along v for every column, all three coordinate channels at once.
    data = np.dstack([xs, ys, grid.elev]).astype(float)        # (h, w, 3)
    try:
        X = np.linalg.solve(_collocation_matrix(w),
                            data.transpose(1, 0, 2).reshape(w, h * 3))
        cvs = np.linalg.solve(
            _collocation_matrix(h),
            X.reshape(w, h, 3).transpose(1, 0, 2).reshape(h, w * 3),
        ).reshape(h, w, 3)
    except np.linalg.LinAlgError:
        return None, None, "collocation system singular"

    srf = rhino3dm.NurbsSurface.Create(3, False, order, order, w, h)
    for i in range(w):
        for j in range(h):
            x, y, z = cvs[j, i]
            srf.Points[i, j] = rhino3dm.Point4d(
                float(x), float(y), float(z), 1.0)
    srf.KnotsU.CreateUniformKnots(1.0)
    srf.KnotsV.CreateUniformKnots(1.0)
    if not srf.IsValid:
        return None, None, "constructed surface failed validity"

    # Ringing check at every cell centre: how far the surface escapes the
    # [min, max] band of the cell's four corner samples.
    gu, gv = _greville(w), _greville(h)
    elev = grid.elev
    worst = 0.0
    for i in range(w - 1):
        tu = float((gu[i] + gu[i + 1]) / 2)
        for j in range(h - 1):
            z = srf.PointAt(tu, float((gv[j] + gv[j + 1]) / 2)).Z
            corners = (float(elev[j, i]), float(elev[j, i + 1]),
                       float(elev[j + 1, i]), float(elev[j + 1, i + 1]))
            over = max(z - max(corners), min(corners) - z)
            if over > worst:
                worst = over
    if worst > TERRAIN_SURFACE_MAX_RINGING_M:
        return None, worst, (
            f"interpolant rings {worst:.2f}m beyond the sampled relief "
            f"(limit {TERRAIN_SURFACE_MAX_RINGING_M}m) - steps too sharp")
    return srf, worst, None


def _build_mesh(grid: ElevationGrid, xs: np.ndarray, ys: np.ndarray) -> rhino3dm.Mesh:
    """Quad mesh over the grid: vertex (j, i) at (xs, ys, elev)."""
    h, w = grid.elev.shape
    mesh = rhino3dm.Mesh()
    for j in range(h):
        for i in range(w):
            mesh.Vertices.Add(
                float(xs[j, i]), float(ys[j, i]), float(grid.elev[j, i])
            )
    for j in range(h - 1):
        row, nxt = j * w, (j + 1) * w
        for i in range(w - 1):
            # CCW seen from +Z (x grows with i, y grows with j) -> normals up.
            mesh.Faces.AddFace(row + i, row + i + 1, nxt + i + 1, nxt + i)
    return mesh


# Marching-squares connection table. Cell corners: a=(j,i) b=(j,i+1)
# c=(j+1,i+1) d=(j+1,i); case bit set when corner >= level. Edges: S between
# a-b, E between b-c, N between d-c, W between a-d.
_MS_TABLE: dict[int, list[tuple[str, str]]] = {
    1: [("W", "S")], 2: [("S", "E")], 3: [("W", "E")], 4: [("E", "N")],
    5: [("W", "S"), ("E", "N")], 6: [("S", "N")], 7: [("W", "N")],
    8: [("N", "W")], 9: [("S", "N")], 10: [("S", "E"), ("N", "W")],
    11: [("E", "N")], 12: [("W", "E")], 13: [("S", "E")], 14: [("W", "S")],
}


def _contour_paths(
    grid: ElevationGrid, xs: np.ndarray, ys: np.ndarray, level: float
) -> list[list[tuple[float, float]]]:
    """All contour polylines (in UTM xy) for one elevation level."""
    elev = grid.elev
    inside = elev >= level
    case = (
        inside[:-1, :-1].astype(np.int8)
        + 2 * inside[:-1, 1:]
        + 4 * inside[1:, 1:]
        + 8 * inside[1:, :-1]
    )
    cells = np.argwhere((case != 0) & (case != 15))
    if len(cells) == 0:
        return []

    def edge_key(j: int, i: int, edge: str) -> tuple[str, int, int]:
        if edge == "S":
            return ("H", j, i)
        if edge == "N":
            return ("H", j + 1, i)
        if edge == "W":
            return ("V", j, i)
        return ("V", j, i + 1)  # E

    def crossing_xy(key: tuple[str, int, int]) -> tuple[float, float]:
        kind, j, i = key
        if kind == "H":
            z0, z1 = elev[j, i], elev[j, i + 1]
            t = (level - z0) / (z1 - z0)
            return (
                float(xs[j, i] + t * (xs[j, i + 1] - xs[j, i])),
                float(ys[j, i] + t * (ys[j, i + 1] - ys[j, i])),
            )
        z0, z1 = elev[j, i], elev[j + 1, i]
        t = (level - z0) / (z1 - z0)
        return (
            float(xs[j, i] + t * (xs[j + 1, i] - xs[j, i])),
            float(ys[j, i] + t * (ys[j + 1, i] - ys[j, i])),
        )

    # Collect segments as undirected key pairs, then chain into paths.
    adj: dict[tuple, list[tuple]] = defaultdict(list)
    for j, i in cells:
        for e1, e2 in _MS_TABLE[int(case[j, i])]:
            k1, k2 = edge_key(j, i, e1), edge_key(j, i, e2)
            adj[k1].append(k2)
            adj[k2].append(k1)

    visited: set[tuple] = set()
    paths: list[list[tuple[float, float]]] = []

    def walk(start: tuple) -> list[tuple]:
        path = [start]
        visited.add(start)
        prev: tuple | None = None
        cur = start
        while True:
            nxt = next(
                (k for k in adj[cur] if k != prev and k not in visited), None
            )
            if nxt is None:
                # Closed loop: link back to the start for a closed polyline.
                if len(path) > 2 and start in adj[cur] and prev != start:
                    path.append(start)
                return path
            visited.add(nxt)
            path.append(nxt)
            prev, cur = cur, nxt

    # Open lines first (their ends have degree 1), then remaining loops.
    for key in list(adj):
        if key not in visited and len(adj[key]) == 1:
            paths.append([crossing_xy(k) for k in walk(key)])
    for key in list(adj):
        if key not in visited:
            paths.append([crossing_xy(k) for k in walk(key)])

    return [p for p in paths if len(p) >= 2]


def add_terrain(
    model: rhino3dm.File3dm,
    grid: ElevationGrid,
    transformer: Any,
) -> dict[str, Any]:
    """Add ``TERRAIN/surface`` + ``TERRAIN/contours`` to ``model``; return stats."""
    parent = rhino3dm.Layer()
    parent.Name = "TERRAIN"
    parent.Id = uuid.uuid4()
    parent.Color = (*TERRAIN_COLOR, 255)
    model.Layers.Add(parent)

    def child(name: str, rgb: tuple[int, int, int]) -> int:
        lay = rhino3dm.Layer()
        lay.Name = name
        lay.Id = uuid.uuid4()
        lay.ParentLayerId = parent.Id
        lay.Color = (*rgb, 255)
        return model.Layers.Add(lay)

    surface_idx = child("surface", TERRAIN_COLOR)
    contours_idx = child("contours", CONTOUR_COLOR)

    xs, ys = _project_nodes(grid, transformer)

    # v6: attempt an editable interpolating NURBS surface first; keep the
    # honest mesh when the interpolant would invent ground between samples
    # (or can't be built).
    attrs = rhino3dm.ObjectAttributes()
    attrs.LayerIndex = surface_idx
    srf, ringing, fallback_reason = _try_nurbs_surface(grid, xs, ys)
    if srf is not None:
        model.Objects.AddSurface(srf, attrs)
        mesh_counts = {"surface_type": "nurbs",
                       "surface_cvs": f"{srf.Points.CountU}x{srf.Points.CountV}",
                       "surface_ringing_m": round(ringing, 3)}
    else:
        mesh = _build_mesh(grid, xs, ys)
        model.Objects.AddMesh(mesh, attrs)
        mesh_counts = {"surface_type": "mesh",
                       "surface_fallback_reason": fallback_reason,
                       "mesh_vertices": len(mesh.Vertices),
                       "mesh_faces": len(mesh.Faces)}
        if ringing is not None:
            mesh_counts["surface_ringing_m"] = round(ringing, 3)

    interval = select_contour_interval(grid.zmin, grid.zmax)
    contour_count = 0
    levels: list[float] = []
    if interval is not None:
        first = np.ceil(grid.zmin / interval) * interval
        levels = list(np.arange(first, grid.zmax, interval))
        for level in levels:
            for path in _contour_paths(grid, xs, ys, float(level)):
                length = sum(
                    ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                    for (x0, y0), (x1, y1) in zip(path, path[1:])
                )
                if length < MIN_CONTOUR_LEN_M:
                    continue
                pl = rhino3dm.Polyline()
                px = py = None
                for x, y in path:
                    # When the level passes exactly through a grid node, two
                    # cell edges yield the same crossing — Rhino rejects
                    # polylines with zero-length segments as invalid.
                    if px is not None and abs(x - px) < 1e-9 and abs(y - py) < 1e-9:
                        continue
                    pl.Add(x, y, float(level))
                    px, py = x, y
                if pl.Count < 2:
                    continue
                cattrs = rhino3dm.ObjectAttributes()
                cattrs.LayerIndex = contours_idx
                model.Objects.AddCurve(pl.ToPolylineCurve(), cattrs)
                contour_count += 1

    return {
        "grid": f"{grid.elev.shape[1]}x{grid.elev.shape[0]}",
        "zoom": grid.zoom,
        "zmin": round(grid.zmin, 1),
        "zmax": round(grid.zmax, 1),
        "contour_interval": interval,
        "contour_levels": len(levels),
        "contour_curves": contour_count,
        **mesh_counts,
    }


if __name__ == "__main__":
    # Standalone test on a site with real relief: Clifton, Bristol (Avon Gorge).
    import tracemalloc

    from fetch_core import get_transformer
    from fetch_elevation import fetch_elevation_grid

    s, w, n, e = 51.452, -2.633, 51.468, -2.605
    out = "test_terrain.3dm"

    tracemalloc.start()
    transformer, epsg = get_transformer(s, w, n, e)
    grid = fetch_elevation_grid(s, w, n, e)

    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
    stats = add_terrain(model, grid, transformer)
    model.Write(out, 0)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"EPSG    : {epsg}")
    for k, v in stats.items():
        print(f"{k:15}: {v}")
    print(f"tracemalloc peak: {peak / 1048576:.1f} MB")

    # Read back and verify: mesh present, contours at exact level elevations.
    check = rhino3dm.File3dm.Read(out)
    by_layer: dict[str, list] = defaultdict(list)
    layers = {lay.Index: lay.Name for lay in check.Layers}
    for obj in check.Objects:
        by_layer[layers[obj.Attributes.LayerIndex]].append(obj)
    meshes = by_layer.get("surface", [])
    contours = by_layer.get("contours", [])
    kinds = [type(o.Geometry).__name__ for o in meshes]
    print(f"read-back: {len(meshes)} object on surface ({kinds}), "
          f"{len(contours)} contour curves")
    assert len(meshes) == 1, "expected exactly one terrain surface object"
    assert kinds[0] == ("NurbsSurface" if stats["surface_type"] == "nurbs"
                        else "Mesh"), "read-back type != reported type"
    mbb = meshes[0].Geometry.GetBoundingBox()
    print(f"mesh bbox: X[{mbb.Min.X:.0f},{mbb.Max.X:.0f}] "
          f"Y[{mbb.Min.Y:.0f},{mbb.Max.Y:.0f}] Z[{mbb.Min.Z:.1f},{mbb.Max.Z:.1f}]")
    bad = 0
    for c in contours:
        bb = c.Geometry.GetBoundingBox()
        flat = abs(bb.Max.Z - bb.Min.Z) < 1e-6
        on_level = abs(bb.Min.Z / stats["contour_interval"]
                       - round(bb.Min.Z / stats["contour_interval"])) < 1e-6
        if not (flat and on_level):
            bad += 1
    print(f"contours flat & on a level multiple: {len(contours) - bad}/{len(contours)}")
    assert bad == 0
    print("OK")
