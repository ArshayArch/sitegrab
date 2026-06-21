"""Phase 1 read-back: shadow moments are one-per-layer, all HIDDEN by default,
while arcs/points/north stay VISIBLE. No network: a synthetic box at Bristol.

Run: ``python test_shadow_layers.py``.
"""

from __future__ import annotations

import os
import tempfile

import ezdxf
import rhino3dm

from fetch_core import get_transformer

from analysis.framework import Building, SiteContext
from analysis.solar import sun_tracks
from analysis.sunpath import build_geometry, write_3dm, write_dxf

S, W, N, E = 51.452, -2.633, 51.468, -2.605


def _ctx_with_box() -> SiteContext:
    tr, epsg = get_transformer(S, W, N, E)
    lon, lat = (W + E) / 2, (S + N) / 2
    cx, cy = tr.transform(lon, lat)
    box = [(cx - 5, cy - 5), (cx + 5, cy - 5), (cx + 5, cy + 5), (cx - 5, cy + 5)]
    ctx = SiteContext(south=S, west=W, north=N, east=E, transformer=tr,
                      epsg=epsg, display_name="box test")
    ctx.buildings = [Building(pts=box, height=20.0, base=0.0,
                              height_estimated=True)]
    return ctx


def _built():
    ctx = _ctx_with_box()
    lon, lat = ctx.centroid
    tracks = sun_tracks(lat, lon)
    # Two times x two solstices -> several distinct shadow moments.
    return build_geometry(ctx, tracks,
                          {"shadow_times": ["09:00", "12:00", "15:00"],
                           "date_set": ("summer_solstice", "winter_solstice")})


def test_3dm_shadow_layers_hidden_arcs_visible() -> None:
    built = _built()
    assert built.shadows, "no shadows produced — test cannot check layering"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.3dm")
        write_3dm(p, built)
        m = rhino3dm.File3dm.Read(p)
        shadow, arc, point, north = [], [], [], []
        for lay in m.Layers:
            if lay.Name.startswith("shadow_"):
                shadow.append(lay)
            elif lay.Name.startswith("arc_"):
                arc.append(lay)
            elif lay.Name.startswith("points_"):
                point.append(lay)
            elif lay.Name == "north":
                north.append(lay)

        assert shadow, "no shadow layers written"
        # Each moment is its OWN layer (unique name) and EVERY one is hidden.
        names = [l.Name for l in shadow]
        assert len(names) == len(set(names)), "shadow moments share a layer"
        for l in shadow:
            assert l.Visible is False, f"shadow layer {l.Name} is visible"
        # Arcs, points and north remain visible.
        for group, lab in ((arc, "arc"), (point, "points"), (north, "north")):
            assert group, f"no {lab} layers written"
            for l in group:
                assert l.Visible is True, f"{lab} layer {l.Name} is hidden"
    print(f"  3dm: {len(shadow)} shadow layers (all hidden, unique), "
          f"{len(arc)} arc + {len(point)} point + {len(north)} north visible")


def test_dxf_shadow_layers_off_arcs_on() -> None:
    built = _built()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.dxf")
        write_dxf(p, built)
        doc = ezdxf.readfile(p)
        shadow = [l for l in doc.layers if l.dxf.name.startswith("SUN_shadow_")]
        arc = [l for l in doc.layers if l.dxf.name.startswith("SUN_arc_")]
        point = [l for l in doc.layers if l.dxf.name.startswith("SUN_points_")]

        assert shadow, "no shadow layers in DXF"
        names = [l.dxf.name for l in shadow]
        assert len(names) == len(set(names)), "shadow moments share a DXF layer"
        for l in shadow:
            assert l.is_off() and l.is_frozen(), \
                f"DXF shadow layer {l.dxf.name} not off+frozen"
        for group, lab in ((arc, "arc"), (point, "points")):
            assert group, f"no {lab} layers in DXF"
            for l in group:
                assert not l.is_off() and not l.is_frozen(), \
                    f"DXF {lab} layer {l.dxf.name} is off/frozen"
    print(f"  dxf: {len(shadow)} shadow layers (all off+frozen, unique), "
          f"{len(arc)} arc + {len(point)} point on")


if __name__ == "__main__":
    test_3dm_shadow_layers_hidden_arcs_visible()
    test_dxf_shadow_layers_off_arcs_on()
    print("all shadow-layer tests passed")
