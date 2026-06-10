"""SiteGrab 3D Rhino writer.

Produces a LEAN .3dm massing model from OpenStreetMap data using rhino3dm.
Intentionally minimal: buildings (extruded solids), main roads (raised
polylines) and water (flat closed curves) only. No footpaths, trees, landuse
or amenities — a massing model must stay uncluttered.

The model sits at full real-world UTM coordinates (hundreds of thousands of
metres from the origin). That is correct and intentional; it is NOT recentred.
"""

from __future__ import annotations

from typing import Any

import rhino3dm

from fetch_core import fetch_overpass, get_transformer, resolve_area

DEFAULT_HEIGHT_M = 30.0
LEVEL_HEIGHT_M = 3.5
ROAD_Z = 0.5  # raise roads slightly above ground plane

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

_LAYERS: dict[str, tuple[int, int, int]] = {
    "BUILDINGS_extruded": (150, 150, 150),
    "ROADS_primary": (60, 60, 60),
    "ROADS_secondary": (120, 120, 120),
    "WATER": (90, 150, 220),
    "SITE_BOUNDARY": (255, 140, 0),
}


def _parse_height(tags: dict[str, str]) -> float:
    """Building height in metres: OSM ``height`` -> ``building:levels`` -> default."""
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
    return DEFAULT_HEIGHT_M


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
            curve = _closed_curve(pts, 0.0)
            if curve is None or not curve.IsClosed:
                continue
            height = _parse_height(tags)
            extrusion = rhino3dm.Extrusion.Create(curve, height, True)
            if extrusion is None:
                continue
            # Profile lies in the XY plane, so positive height extrudes +Z (upward).
            model.Objects.AddExtrusion(extrusion, attrs("BUILDINGS_extruded"))
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
