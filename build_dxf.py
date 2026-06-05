"""SiteGrab 2D AutoCAD writer.

Produces a DETAILED .dxf basemap from OpenStreetMap data using ezdxf, fully
headless (no QGIS / GIS desktop dependency). Features are classified into
granular per-subtype layers (CATEGORY_subtype_GEOM).
"""

from __future__ import annotations

from typing import Any

import ezdxf
from ezdxf.document import Drawing

from fetch_core import fetch_overpass, get_transformer

# Top-level category -> AutoCAD Color Index, used to colour each layer.
CATEGORY_COLORS: dict[str, int] = {
    "BLDG": 8,       # grey
    "ROADS": 7,      # white/black
    "RAIL": 6,       # magenta
    "WATER": 5,      # blue
    "NATURAL": 3,    # green
    "LANDUSE": 2,    # yellow
    "LEISURE": 4,    # cyan
    "AMENITY": 1,    # red
    "INFRA": 8,      # grey
    "ADMIN": 6,      # magenta
    "LABEL": 7,      # white/black
}


def classify(tags: dict[str, str], is_point: bool) -> tuple[str, bool] | None:
    """Map OSM tags to (layer_name, is_closed_area). Returns None to skip."""
    g = "PT" if is_point else None
    if "building" in tags:
        sub = {"yes": "outline", "residential": "residential", "apartments": "residential",
               "house": "residential", "commercial": "commercial", "retail": "commercial",
               "office": "commercial", "civic": "civic", "public": "civic",
               "school": "civic", "hospital": "civic"}.get(tags["building"], "outline")
        return f"BLDG_{sub}_{g or 'PL'}", not is_point
    if "highway" in tags:
        cls = {"motorway": "primary", "trunk": "primary", "primary": "primary",
               "secondary": "secondary", "tertiary": "secondary",
               "residential": "residential", "living_street": "residential",
               "service": "service", "unclassified": "service",
               "footway": "footpath", "path": "footpath", "pedestrian": "footpath",
               "cycleway": "footpath", "steps": "footpath"}.get(tags["highway"], "service")
        return f"ROADS_{cls}_LN", False
    if "railway" in tags:
        return "RAIL_track_LN", False
    if tags.get("natural") == "water" or "waterway" in tags:
        return f"WATER_{tags.get('waterway', 'water')}_{g or 'PL'}", not is_point
    if tags.get("natural") == "tree":
        return "NATURAL_tree_PT", False
    if "natural" in tags:
        return f"NATURAL_{tags['natural']}_{g or 'PL'}", not is_point
    if "landuse" in tags:
        return f"LANDUSE_{tags['landuse']}_{g or 'PL'}", not is_point
    if "leisure" in tags:
        return f"LEISURE_{tags['leisure']}_{g or 'PL'}", not is_point
    if "amenity" in tags:
        return f"AMENITY_{tags['amenity']}_{g or 'PL'}", not is_point
    if "barrier" in tags:
        return "INFRA_barrier_LN", False
    if tags.get("boundary") == "administrative":
        return "ADMIN_boundary_PL", not is_point
    if "place" in tags and is_point:
        return "LABEL_place_PT", False
    return None


# Detailed query: ways + selected nodes across the bbox.
_QUERY_TEMPLATE = """
[out:json][timeout:180];
(
  way["building"]({bbox});
  way["highway"]({bbox});
  way["railway"]({bbox});
  way["natural"]({bbox});
  way["waterway"]({bbox});
  way["landuse"]({bbox});
  way["leisure"]({bbox});
  way["amenity"]({bbox});
  way["barrier"]({bbox});
  node["amenity"]({bbox});
  node["natural"="tree"]({bbox});
  node["place"]({bbox});
);
out geom;
"""


def _ensure_layer(doc: Drawing, name: str) -> None:
    """Create a layer on first use, coloured by top-level category."""
    if name in doc.layers:
        return
    category = name.split("_", 1)[0]
    color = CATEGORY_COLORS.get(category, 7)
    doc.layers.add(name=name, color=color)


def build_dxf(area: str, out_path: str) -> dict[str, Any]:
    """Fetch + build a detailed DXF for ``area`` and write it to ``out_path``.

    Returns a small stats dict (layer count, entity count, EPSG, display name).
    """
    from fetch_core import geocode

    s, w, n, e, display_name = geocode(area)
    transformer, epsg = get_transformer(s, w, n, e)
    bbox = f"{s},{w},{n},{e}"
    data = fetch_overpass(_QUERY_TEMPLATE.format(bbox=bbox))

    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.M
    msp = doc.modelspace()

    entity_count = 0
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if not tags:
            continue
        etype = el.get("type")

        if etype == "node":
            result = classify(tags, is_point=True)
            if result is None:
                continue
            layer, _closed = result
            _ensure_layer(doc, layer)
            x, y = transformer.transform(el["lon"], el["lat"])
            msp.add_point((x, y), dxfattribs={"layer": layer})
            entity_count += 1

        elif etype == "way":
            geom = el.get("geometry")
            if not geom or len(geom) < 2:
                continue
            result = classify(tags, is_point=False)
            if result is None:
                continue
            layer, closed = result
            _ensure_layer(doc, layer)
            pts = [transformer.transform(p["lon"], p["lat"]) for p in geom]
            poly = msp.add_lwpolyline(pts, dxfattribs={"layer": layer})
            if closed:
                poly.close(True)
            entity_count += 1

    # Site boundary rectangle from the bbox corners (reprojected).
    doc.layers.add(name="SITE_BOUNDARY", color=7)
    corners_ll = [(w, s), (e, s), (e, n), (w, n)]
    corners = [transformer.transform(lon, lat) for lon, lat in corners_ll]
    boundary = msp.add_lwpolyline(corners, dxfattribs={"layer": "SITE_BOUNDARY"})
    boundary.close(True)
    entity_count += 1

    doc.saveas(out_path)

    # Read back to confirm the file is valid and report real counts.
    check = ezdxf.readfile(out_path)
    layer_count = len(check.layers)
    read_entities = len(check.modelspace())

    return {
        "display_name": display_name,
        "epsg": epsg,
        "layers": layer_count,
        "entities": read_entities,
        "written_entities": entity_count,
    }


if __name__ == "__main__":
    import sys

    area = " ".join(sys.argv[1:]) or "Dubai Marina"
    stats = build_dxf(area, "test_marina.dxf")
    print(f"Area     : {stats['display_name']}")
    print(f"EPSG     : {stats['epsg']}")
    print(f"Layers   : {stats['layers']}")
    print(f"Entities : {stats['entities']}")
