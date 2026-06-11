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
# levels wins, like picking 1m/2m/5m contours on a real site plan.
_INTERVALS: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
MAX_CONTOURS = 40

# Below this elevation range the site is flat: contours would be DEM noise.
MIN_RELIEF_M = 1.5

TERRAIN_COLOR = (146, 116, 91)     # earth brown
CONTOUR_COLOR = (196, 148, 90)     # lighter sand


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

    mesh = _build_mesh(grid, xs, ys)
    attrs = rhino3dm.ObjectAttributes()
    attrs.LayerIndex = surface_idx
    model.Objects.AddMesh(mesh, attrs)

    interval = select_contour_interval(grid.zmin, grid.zmax)
    contour_count = 0
    levels: list[float] = []
    if interval is not None:
        first = np.ceil(grid.zmin / interval) * interval
        levels = list(np.arange(first, grid.zmax, interval))
        for level in levels:
            for path in _contour_paths(grid, xs, ys, float(level)):
                pl = rhino3dm.Polyline()
                for x, y in path:
                    pl.Add(x, y, float(level))
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
        "mesh_vertices": len(mesh.Vertices),
        "mesh_faces": len(mesh.Faces),
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
    print(f"read-back: {len(meshes)} mesh on surface, {len(contours)} contour curves")
    assert len(meshes) == 1, "expected exactly one terrain mesh"
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
