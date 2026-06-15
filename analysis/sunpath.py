"""Sun Path — the REPRESENTATION half, plus the framework module object.

Turns the calculated solar numbers from :mod:`analysis.solar` into Rhino/CAD
geometry on granular ``SUN/...`` layers, in the SAME UTM coordinates as the
geometry model so the output drops straight onto the combined ``.3dm``.

  - ``SUN/arc_<date>``     — the sun's path sunrise->sunset, a 3D curve arcing
                             over the site (DXF: its plan projection).
  - ``SUN/points_<date>``  — hourly sun positions (local solar time), labelled.
  - ``SUN/north``          — a true-north arrow (how north is established, made
                             visible and checkable).
  - ``SUN/shadow_<date>_<hhmm>`` — cast building shadows (added in Phase 3).

TRUE NORTH. astral returns azimuth clockwise from TRUE north. The model lives in
a UTM grid whose +Y is GRID north, which differs from true north by the meridian
convergence. We measure that convergence directly (project the centroid and a
point due north of it through the SAME transformer; the angle between the result
and grid +Y is the convergence) and rotate every solar azimuth into the grid by
it — so the arcs, points and shadows are physically consistent with the
buildings, and true north is drawn as an explicit arrow for visual confirmation.
"""

from __future__ import annotations

import math
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import rhino3dm

from .framework import (
    BUILDINGS,
    LOCATION,
    TERRAIN,
    AnalysisResult,
    AnalysisSpec,
    Param,
    SiteContext,
    register,
)
from .solar import DayTrack, sun_tracks

# Per-key-date colours: warm gold high sun, cool blue low sun, neutral equinox.
SUN_COLORS: dict[str, tuple[int, int, int]] = {
    "summer_solstice": (232, 168, 38),
    "equinox": (150, 150, 150),
    "winter_solstice": (86, 138, 208),
}
NORTH_COLOR = (214, 74, 58)
SHADOW_COLOR = (74, 74, 90)
SHADOW_DRAPED_COLOR = (110, 96, 120)

# Dome radius: enclose the site corners so the arcs sweep over the model.
ARC_RADIUS_DIAG_FRAC = 0.55
ARC_RADIUS_MIN_M = 150.0
HOUR_DOT_PREFIX = ""  # TextDots already read as labels; keep them terse


@dataclass
class _TrackGeom:
    key: str
    label: str
    arc: list[tuple[float, float, float]]
    hourly: list[tuple[float, float, float, int]]  # x, y, z, hour
    noon_altitude: float


@dataclass
class _Built:
    centre: tuple[float, float, float]
    radius: float
    convergence_deg: float
    north_end: tuple[float, float, float]
    tracks: list[_TrackGeom]
    shadows: list[tuple[str, str, list[list[tuple[float, float, float]]]]] = field(
        default_factory=list)  # (layer_suffix, color_key, polygons)
    shadow_meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Site frame: centre, radius, and the measured true-north convergence.
# ---------------------------------------------------------------------------
def _site_frame(ctx: SiteContext) -> tuple[tuple[float, float, float], float, float]:
    """Return (centre_xyz, radius_m, convergence_radians) for the site."""
    lon, lat = ctx.centroid
    cx, cy = ctx.transformer.transform(lon, lat)
    centre = (cx, cy, ctx.datum)

    # Radius from the projected bbox diagonal.
    corners = [ctx.transformer.transform(lo, la)
               for lo, la in ((ctx.west, ctx.south), (ctx.east, ctx.south),
                              (ctx.east, ctx.north), (ctx.west, ctx.north))]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    radius = max(ARC_RADIUS_MIN_M, ARC_RADIUS_DIAG_FRAC * diag)

    # Meridian convergence: grid bearing of TRUE north, measured (no formula).
    dlat = min(0.005, (ctx.north - ctx.south) / 4 or 0.005)
    xn, yn = ctx.transformer.transform(lon, lat + dlat)
    theta = math.atan2(xn - cx, yn - cy)  # clockwise from grid +Y
    return centre, radius, theta


def _sky_xyz(
    centre: tuple[float, float, float], radius: float, theta: float,
    azimuth_deg: float, altitude_deg: float,
) -> tuple[float, float, float]:
    """Point on the sky dome for a TRUE-north azimuth + altitude, in the grid."""
    bearing = math.radians(azimuth_deg) + theta   # true az -> grid bearing
    a = math.radians(altitude_deg)
    cx, cy, cz = centre
    return (cx + radius * math.cos(a) * math.sin(bearing),
            cy + radius * math.cos(a) * math.cos(bearing),
            cz + radius * math.sin(a))


def build_geometry(
    ctx: SiteContext, tracks: list[DayTrack], params: dict[str, Any],
) -> _Built:
    """Precompute all 3D geometry once; both writers consume it."""
    centre, radius, theta = _site_frame(ctx)
    track_geoms: list[_TrackGeom] = []
    for tr in tracks:
        arc = [_sky_xyz(centre, radius, theta, s.azimuth, s.altitude)
               for s in tr.arc]
        hourly = [(*_sky_xyz(centre, radius, theta, s.azimuth, s.altitude),
                   int(round(s.hour))) for s in tr.hourly]
        track_geoms.append(_TrackGeom(tr.key, tr.label, arc, hourly,
                                      tr.noon_altitude))

    north_end = (centre[0] + radius * math.sin(theta),
                 centre[1] + radius * math.cos(theta),
                 centre[2])

    built = _Built(centre=centre, radius=radius,
                   convergence_deg=math.degrees(theta),
                   north_end=north_end, tracks=track_geoms)

    # Cast shadows from ctx.buildings at the sampled sun positions.
    from .shadows import cast_shadows  # local import: representation-only dep
    built.shadows, built.shadow_meta = cast_shadows(
        ctx, tracks, params, centre, radius, theta)
    return built


# ---------------------------------------------------------------------------
# Writers.
# ---------------------------------------------------------------------------
def _layer(model: rhino3dm.File3dm, name: str, parent: uuid.UUID | None,
           rgb: tuple[int, int, int], cache: dict[str, int]) -> int:
    if name in cache:
        return cache[name]
    lay = rhino3dm.Layer()
    lay.Name = name
    lay.Id = uuid.uuid4()
    if parent is not None:
        lay.ParentLayerId = parent
    lay.Color = (*rgb, 255)
    cache[name] = model.Layers.Add(lay)
    return cache[name]


def _attrs(idx: int) -> rhino3dm.ObjectAttributes:
    a = rhino3dm.ObjectAttributes()
    a.LayerIndex = idx
    return a


def _polyline_curve(pts: list[tuple[float, float, float]]) -> rhino3dm.PolylineCurve | None:
    if len(pts) < 2:
        return None
    pl = rhino3dm.Polyline()
    for x, y, z in pts:
        pl.Add(x, y, z)
    return pl.ToPolylineCurve()


def write_3dm(path: str, built: _Built) -> dict[str, int]:
    """Write the 3D reading: arcs as real curves, points in space, shadows on
    the ground — all in UTM, aligned with the combined model."""
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters

    parent = rhino3dm.Layer()
    parent.Name = "SUN"
    parent.Id = uuid.uuid4()
    parent.Color = (240, 200, 80, 255)
    model.Layers.Add(parent)
    cache: dict[str, int] = {}

    counts = {"arcs": 0, "points": 0, "shadows": 0}
    for tg in built.tracks:
        rgb = SUN_COLORS.get(tg.key, (180, 180, 180))
        arc_idx = _layer(model, f"arc_{tg.key}", parent.Id, rgb, cache)
        curve = _polyline_curve(tg.arc)
        if curve is not None:
            model.Objects.AddCurve(curve, _attrs(arc_idx))
            counts["arcs"] += 1
        pt_idx = _layer(model, f"points_{tg.key}", parent.Id, rgb, cache)
        for x, y, z, hr in tg.hourly:
            model.Objects.AddPoint(x, y, z, _attrs(pt_idx))
            model.Objects.AddTextDot(f"{hr:02d}", rhino3dm.Point3d(x, y, z),
                                     _attrs(pt_idx))
            counts["points"] += 1

    # Cast shadows (Phase 3) — closed ground polygons.
    for suffix, color_key, polygons in built.shadows:
        rgb = SHADOW_DRAPED_COLOR if suffix.startswith("draped") else SHADOW_COLOR
        s_idx = _layer(model, f"shadow_{suffix}", parent.Id, rgb, cache)
        for poly in polygons:
            curve = _polyline_curve(poly + poly[:1])  # close it
            if curve is not None:
                model.Objects.AddCurve(curve, _attrs(s_idx))
                counts["shadows"] += 1

    # True-north arrow + label.
    n_idx = _layer(model, "north", parent.Id, NORTH_COLOR, cache)
    arrow = _polyline_curve([built.centre, built.north_end])
    if arrow is not None:
        model.Objects.AddCurve(arrow, _attrs(n_idx))
    model.Objects.AddTextDot("N (true)", rhino3dm.Point3d(*built.north_end),
                             _attrs(n_idx))

    model.Write(path, 0)
    return counts


def write_dxf(path: str, built: _Built) -> dict[str, int]:
    """Write the 2D plan reading: arcs and shadows projected to plan, points,
    on the same granular SUN/... layer names."""
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.M
    msp = doc.modelspace()

    def ensure(name: str, aci: int) -> None:
        if name not in doc.layers:
            doc.layers.add(name=name, color=aci)

    label_h = max(1.5, built.radius * 0.012)
    counts = {"arcs": 0, "points": 0, "shadows": 0}
    for tg in built.tracks:
        aci = {"summer_solstice": 30, "equinox": 8, "winter_solstice": 150}.get(
            tg.key, 7)
        arc_layer = f"SUN_arc_{tg.key}"
        ensure(arc_layer, aci)
        if len(tg.arc) >= 2:
            msp.add_lwpolyline([(x, y) for x, y, _ in tg.arc],
                               dxfattribs={"layer": arc_layer})
            counts["arcs"] += 1
        pt_layer = f"SUN_points_{tg.key}"
        ensure(pt_layer, aci)
        for x, y, _, hr in tg.hourly:
            msp.add_point((x, y), dxfattribs={"layer": pt_layer})
            t = msp.add_text(f"{hr:02d}", height=label_h,
                             dxfattribs={"layer": pt_layer})
            t.set_placement((x + label_h, y + label_h))
            counts["points"] += 1

    for suffix, color_key, polygons in built.shadows:
        layer = f"SUN_shadow_{suffix}"
        ensure(layer, 251 if suffix.startswith("draped") else 8)
        for poly in polygons:
            if len(poly) >= 3:
                pl = msp.add_lwpolyline([(x, y) for x, y, _ in poly],
                                        dxfattribs={"layer": layer})
                pl.close(True)
                counts["shadows"] += 1

    ensure("SUN_north", 1)
    cx, cy, _ = built.centre
    nx, ny, _ = built.north_end
    msp.add_lwpolyline([(cx, cy), (nx, ny)], dxfattribs={"layer": "SUN_north"})
    t = msp.add_text("N (true)", height=label_h, dxfattribs={"layer": "SUN_north"})
    t.set_placement((nx, ny))

    doc.saveas(path)
    return counts


# ---------------------------------------------------------------------------
# The framework module.
# ---------------------------------------------------------------------------
_DATE_KEYS = ("summer_solstice", "equinox", "winter_solstice")


class SunPath:
    spec = AnalysisSpec(
        key="sun_path",
        name="Sun Path",
        summary="Solar arcs, hourly sun positions and cast building shadows for "
                "the key dates — oriented to true north, aligned with your model.",
        data_source="Calculated solar geometry (astral) — free, keyless, no API "
                    "or billing. Building heights from OSM where present, else "
                    "the same type-driven estimates as the massing model.",
        data_is_free=True,
        requires=(LOCATION, BUILDINGS, TERRAIN),
        outputs=("dxf", "3dm"),
        params=(
            Param("date_set", "Key dates", "multichoice", list(_DATE_KEYS),
                  _DATE_KEYS),
            Param("shadow_times", "Shadow times", "multichoice",
                  ["09:00", "12:00", "15:00"],
                  ("06:00", "09:00", "12:00", "15:00", "18:00")),
            Param("draped_shadows", "Drape shadows on terrain (stretch)", "bool",
                  False),
        ),
    )

    def run(self, ctx: SiteContext, params: dict[str, Any], formats: list[str],
            out_dir: str) -> AnalysisResult:
        lon, lat = ctx.centroid
        wanted_keys = set(params.get("date_set") or _DATE_KEYS)
        tracks = [t for t in sun_tracks(lat, lon) if t.key in wanted_keys]
        if not tracks:
            tracks = sun_tracks(lat, lon)

        built = build_geometry(ctx, tracks, params)

        slug = _slug(ctx.display_name)
        files: list[tuple[str, str]] = []
        counts: dict[str, int] = {}
        if "3dm" in formats:
            p = os.path.join(out_dir, f"{slug}_sunpath.3dm")
            counts = write_3dm(p, built)
            files.append((p, f"{slug}_sunpath.3dm"))
        if "dxf" in formats:
            p = os.path.join(out_dir, f"{slug}_sunpath.dxf")
            counts = write_dxf(p, built)
            files.append((p, f"{slug}_sunpath.dxf"))

        notes = _notes(ctx, built, tracks, params)
        stats = {
            "display_name": ctx.display_name,
            "epsg": ctx.epsg,
            "convergence_deg": round(built.convergence_deg, 3),
            "radius_m": round(built.radius, 1),
            "datum_m": ctx.datum,
            "key_dates": [{"key": t.key, "label": t.label,
                           "noon_altitude": round(t.noon_altitude, 1),
                           "day_length_h": round(t.day_length_h, 1)}
                          for t in tracks],
            "buildings": len(ctx.buildings or []),
            **built.shadow_meta,
            **counts,
        }
        return AnalysisResult(files=files, stats=stats, notes=notes)


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return s or "site"


def _notes(ctx: SiteContext, built: _Built, tracks: list[DayTrack],
           params: dict[str, Any]) -> list[str]:
    notes = [
        "Sun geometry is calculated and precise (astral): azimuth and altitude "
        "are accurate to a small fraction of a degree.",
        f"Oriented to TRUE north. The model's UTM grid north differs from true "
        f"north here by {built.convergence_deg:+.2f}° (meridian convergence); "
        f"solar azimuths are rotated into the grid by that angle, and true "
        f"north is drawn on SUN/north.",
        "Hour marks are in local solar (sundial) time — 12:00 is the sun on the "
        "meridian; this differs from clock time by the timezone offset and the "
        "equation of time.",
    ]
    meta = built.shadow_meta
    if meta.get("shadow_casters"):
        notes.append(
            "Cast shadows are real projections of the buildings, but shadow "
            "LENGTH scales with building height — and OSM lacks real heights for "
            "most buildings, so those lengths inherit the model's type-driven "
            "height ESTIMATE (see MASSING_NOTES.md). Treat shadow extents as "
            "indicative, not as a rights-of-light or daylight assessment.")
        notes.append(
            "Shadows are cast on the summer and winter solstices (the extremes "
            "that bound the year) at the chosen times, flat on the ground plane. "
            "Flat projection is exact for rectangular footprints and slightly "
            "over-estimates concave/L-shaped plans.")
        if meta.get("shadow_skipped_capped"):
            notes.append(
                f"Free-tier cap: shadows were cast for the tallest "
                f"{meta['shadow_building_cap']} buildings (the dominant casters); "
                f"{meta['shadow_skipped_capped']} smaller buildings were omitted "
                f"to keep the file and memory within the free tier.")
        if meta.get("draped"):
            notes.append(
                "Terrain-draped shadows (SUN/shadow_draped_*) lift the flat "
                "outline onto the DEM — a drape of the flat shadow, not a true "
                "ray-vs-terrain intersection; read them as approximate on slopes.")
        if meta.get("shadow_positions_skipped_low_sun"):
            notes.append(
                f"{meta['shadow_positions_skipped_low_sun']} requested shadow "
                f"time(s) had the sun below {3}° and were skipped (near-horizon "
                f"sun casts effectively unbounded shadows).")
    return notes


register(SunPath())


if __name__ == "__main__":
    # Standalone check on Clifton, Bristol (real relief): winter arc must sit
    # lower and shorter than summer. No server needed.
    import tempfile

    from fetch_core import get_transformer

    s, w, n, e = 51.452, -2.633, 51.468, -2.605
    transformer, epsg = get_transformer(s, w, n, e)
    ctx = SiteContext(south=s, west=w, north=n, east=e, transformer=transformer,
                      epsg=epsg, display_name="Clifton test")
    lon, lat = ctx.centroid
    tracks = sun_tracks(lat, lon)
    built = build_geometry(ctx, tracks, {"shadow_times": [], "date_set": _DATE_KEYS})

    def arc_zmax(key: str) -> float:
        tg = next(t for t in built.tracks if t.key == key)
        return max(z for _, _, z in tg.arc)

    def arc_span(key: str) -> float:
        tg = next(t for t in built.tracks if t.key == key)
        xs = [p[0] for p in tg.arc]
        return max(xs) - min(xs)

    sz, wz = arc_zmax("summer_solstice"), arc_zmax("winter_solstice")
    print(f"EPSG {epsg}  convergence {built.convergence_deg:+.3f}°  "
          f"radius {built.radius:.0f}m  centre {built.centre}")
    print(f"summer arc peak Z = {sz:.1f}   winter arc peak Z = {wz:.1f}   "
          f"(summer must be higher: {sz > wz})")
    print(f"summer arc E-W span = {arc_span('summer_solstice'):.0f}m   "
          f"winter span = {arc_span('winter_solstice'):.0f}m")
    assert sz > wz, "winter arc not lower than summer"

    with tempfile.TemporaryDirectory() as d:
        c3 = write_3dm(os.path.join(d, "t.3dm"), built)
        cd = write_dxf(os.path.join(d, "t.dxf"), built)
        print("3dm counts", c3, "dxf counts", cd)
    print("OK")
