"""Wind Analysis — the REPRESENTATION half, plus the framework module object.

Turns the MEASURED wind rose from :mod:`analysis.wind_data` into an INDICATIVE
wind diagram on ``WIND/...`` layers, in the SAME UTM coordinates as the geometry
model so it drops straight onto the combined ``.3dm``/``.dxf``.

  - ``WIND/arrows_prevailing`` — a field of arrows for the prevailing direction,
    flowing across the site and STOPPING at building facades they hit head-on.
  - ``WIND/arrows_secondary``  — the next strongest directions, fewer/shorter,
    each its own layer, hidden by default (toggle like the shadow hours).
  - ``WIND/channels``          — narrow gaps between buildings, aligned to the
    prevailing wind, marked as channels (bolder = narrower gap).
  - ``WIND/rose``              — a wind rose in the corner: the full 16-sector
    distribution for reference.
  - ``WIND/north``             — the true-north arrow (same basis as Sun Path).

HONESTY (stated in code and in the output notes). This is a DIAGRAM, NOT a
SIMULATION. It is not CFD. The arrows show the *prevailing measured wind
direction and relative strength* (real climatology); the channels are a
*geometric suggestion* — where a narrow open corridor between buildings lines up
with the prevailing wind, it is marked, the narrower the bolder. It does not
compute real airflow, pressure, turbulence or speed-up; it indicates, from
geometry plus prevailing direction, where funnelling is plausible.

TRUE NORTH. Wind directions are meteorological TRUE bearings. The model's UTM
grid north differs from true north by the meridian convergence; we measure that
exactly as Sun Path does (project the centroid and a point due north through the
SAME transformer) and rotate every wind bearing into the grid by it, so arrows
and rose are physically consistent with the buildings.
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
    AnalysisResult,
    AnalysisSpec,
    Param,
    SiteContext,
    register,
)
from .wind_data import SECTORS, WindRose, fetch_wind_rose

# How many directions get a streamline field: the single prevailing one (bold,
# default on) plus the next few strongest as secondary layers (default off).
N_SECONDARY = 3
# A secondary sector must carry at least this share of the prevailing sector's
# strength to be drawn at all (skip negligible directions).
SECONDARY_MIN_REL = 0.25

# A gap between two buildings up to this wide (m), aligned to the prevailing
# wind, is read as a CHANNEL. Wider openings are plazas/streets, not funnels.
CHANNEL_MAX_WIDTH_M = 30.0
CHANNEL_MIN_WIDTH_M = 2.0          # below this, treat as buildings touching
CHANNEL_RAKE_STATIONS = 7         # cross-flow sampling lines across the site

# Arrow read height above the ground datum in the 3DM (a readable hover).
ARROW_Z_M = 12.0

# Free-tier discipline: cap obstacles considered for arrows/channels on a
# megasite (largest footprints kept — the obstacles that matter for wind), and
# report the cap so the drop is never silent. Generous: typical neighbourhoods
# (a few thousand buildings) are fully considered.
WIND_MAX_BUILDINGS = 4000


# ---------------------------------------------------------------------------
# Small 2D geometry helpers (pure).
# ---------------------------------------------------------------------------
def _rot(vx: float, vy: float, a: float) -> tuple[float, float]:
    c, s = math.cos(a), math.sin(a)
    return (vx * c - vy * s, vx * s + vy * c)


def _ray_first_hit(ox: float, oy: float, dx: float, dy: float, tmax: float,
                   edges: list[tuple[float, float, float, float]]) -> float | None:
    """Nearest t in (eps, tmax] where ray (o, d) crosses any edge, else None."""
    best = tmax
    hit = False
    for ax, ay, bx, by in edges:
        ex, ey = bx - ax, by - ay
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = ax - ox, ay - oy
        t = (wx * ey - wy * ex) / den      # along ray
        u = (wx * dy - wy * dx) / den      # along edge
        if 1e-6 < t < best and -1e-9 <= u <= 1.0 + 1e-9:
            best = t
            hit = True
    return best if hit else None


def _clip_ray_to_box(ox: float, oy: float, dx: float, dy: float,
                     box: tuple[float, float, float, float]
                     ) -> tuple[float, float] | None:
    """Liang-Barsky: param interval [t0, t1] of ray (o,d) inside an AABB."""
    minx, miny, maxx, maxy = box
    t0, t1 = -math.inf, math.inf
    for p, q in ((-dx, ox - minx), (dx, maxx - ox),
                 (-dy, oy - miny), (dy, maxy - oy)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
    if t0 > t1:
        return None
    return (max(t0, 0.0), t1)


def _line_polygon_intervals(ox: float, oy: float, px: float, py: float,
                            poly: list[tuple[float, float]]
                            ) -> list[tuple[float, float]]:
    """Inside-intervals (param s along direction p from o) of an infinite line
    crossing a simple polygon: sorted edge crossings paired up."""
    ss: list[float] = []
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        den = px * ey - py * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = ax - ox, ay - oy
        s = (wx * ey - wy * ex) / den       # along p
        u = (wx * py - wy * px) / den        # along edge
        if -1e-9 <= u <= 1.0 + 1e-9:
            ss.append(s)
    ss.sort()
    return [(ss[i], ss[i + 1]) for i in range(0, len(ss) - 1, 2)]


def _merge_intervals(ivs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not ivs:
        return []
    ivs = sorted(ivs)
    out = [list(ivs[0])]
    for a, b in ivs[1:]:
        if a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


# ---------------------------------------------------------------------------
# Geometry objects shared by both writers.
# ---------------------------------------------------------------------------
@dataclass
class _Arrow:
    pts: list[tuple[float, float, float]]            # shaft (2 pts) + head segs
    segs: list[list[tuple[float, float, float]]]     # head V (2 short segments)
    strength: float
    layer: str          # "arrows_prevailing" | "arrows_secondary_<lbl>" | "channels"


@dataclass
class _WindBuilt:
    centre: tuple[float, float, float]
    box: tuple[float, float, float, float]
    radius: float
    convergence_deg: float
    z: float
    north_end: tuple[float, float, float]
    arrows: list[_Arrow] = field(default_factory=list)
    rose: list[tuple[str, list[tuple[float, float, float]], bool]] = field(
        default_factory=list)   # (kind, pts, closed) on WIND/rose
    rose_centre: tuple[float, float, float] = (0.0, 0.0, 0.0)
    meta: dict[str, Any] = field(default_factory=dict)


def _site_frame(ctx: SiteContext):
    """(centre_xyz, box, radius, convergence_theta) in UTM — true north measured."""
    lon, lat = ctx.centroid
    cx, cy = ctx.transformer.transform(lon, lat)
    corners = [ctx.transformer.transform(lo, la)
               for lo, la in ((ctx.west, ctx.south), (ctx.east, ctx.south),
                              (ctx.east, ctx.north), (ctx.west, ctx.north))]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    box = (min(xs), min(ys), max(xs), max(ys))
    radius = 0.5 * math.hypot(box[2] - box[0], box[3] - box[1])
    dlat = min(0.005, (ctx.north - ctx.south) / 4 or 0.005)
    xn, yn = ctx.transformer.transform(lon, lat + dlat)
    theta = math.atan2(xn - cx, yn - cy)
    return (cx, cy, ctx.datum), box, radius, theta


def _flow_vec(from_bearing_deg: float, theta: float) -> tuple[float, float]:
    """Unit vector the wind FLOWS toward (grid), given the TRUE 'from' bearing."""
    g = math.radians(from_bearing_deg + 180.0) + theta
    return (math.sin(g), math.cos(g))


def _from_vec(from_bearing_deg: float, theta: float) -> tuple[float, float]:
    """Unit vector pointing toward where the wind COMES FROM (grid) — rose petal."""
    g = math.radians(from_bearing_deg) + theta
    return (math.sin(g), math.cos(g))


def _select_obstacles(ctx: SiteContext) -> tuple[list, int]:
    """Largest-footprint buildings up to the free-tier cap (biggest wind
    obstacles kept); returns (kept, skipped_count)."""
    bs = ctx.buildings or []
    if len(bs) <= WIND_MAX_BUILDINGS:
        return bs, 0

    def area(b) -> float:
        p = b.pts
        return abs(sum(p[i][0] * p[(i + 1) % len(p)][1]
                       - p[(i + 1) % len(p)][0] * p[i][1]
                       for i in range(len(p)))) / 2.0
    ordered = sorted(bs, key=area, reverse=True)
    return ordered[:WIND_MAX_BUILDINGS], len(bs) - WIND_MAX_BUILDINGS


def _make_arrow(sx: float, sy: float, ex: float, ey: float, z: float,
                strength: float, layer: str, head: float) -> _Arrow:
    dx, dy = ex - sx, ey - sy
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    back = (-ux * head, -uy * head)
    lft = _rot(back[0], back[1], math.radians(24))
    rgt = _rot(back[0], back[1], math.radians(-24))
    tip = (ex, ey, z)
    return _Arrow(
        pts=[(sx, sy, z), (ex, ey, z)],
        segs=[[tip, (ex + lft[0], ey + lft[1], z)],
              [tip, (ex + rgt[0], ey + rgt[1], z)]],
        strength=strength, layer=layer)


def _streamlines(centre, box, radius, f, strength, layer, z, edges, head,
                 n_lines, reach_frac) -> list[_Arrow]:
    """Arrows for one direction: parallel rays in flow dir f, clipped to the
    site box and STOPPED at the first building facade they meet."""
    cx, cy, _ = centre
    fx, fy = f
    px, py = -fy, fx                       # cross-flow axis
    arrows: list[_Arrow] = []
    span = radius
    for k in range(n_lines):
        frac = 0.0 if n_lines == 1 else (k / (n_lines - 1)) * 2 - 1
        # Seed on the upwind side, offset across the flow.
        ox = cx - fx * radius * 1.05 + px * frac * span
        oy = cy - fy * radius * 1.05 + py * frac * span
        clip = _clip_ray_to_box(ox, oy, fx, fy, box)
        if clip is None:
            continue
        t_in, t_out = clip
        t_out = min(t_out, t_in + 2 * radius * reach_frac)
        hit = _ray_first_hit(ox + fx * t_in, oy + fy * t_in, fx, fy,
                             t_out - t_in, edges)
        t_end = t_in + hit if hit is not None else t_out
        if t_end - t_in < radius * 0.05:    # immediate wall: a short facade stub
            t_end = t_in + radius * 0.05
        sx, sy = ox + fx * t_in, oy + fy * t_in
        ex, ey = ox + fx * t_end, oy + fy * t_end
        arrows.append(_make_arrow(sx, sy, ex, ey, z, strength, layer, head))
    return arrows


def _channels(centre, box, radius, f, z, obstacles, head) -> list[_Arrow]:
    """Mark narrow gaps between buildings that line up with the prevailing wind.

    RULE (geometric, honest): sample cross-flow rakes across the site; on each, a
    free interval flanked by buildings on both sides and narrower than
    CHANNEL_MAX_WIDTH_M is a channel. Strength = how narrow (narrower -> bolder).
    This SUGGESTS funnelling from geometry + prevailing direction; it does not
    compute airflow. LIMITATION: a 2D footprint rule — it ignores building
    height, roof flow and real pressure, so treat it as indicative only.
    """
    cx, cy, _ = centre
    fx, fy = f
    px, py = -fy, fx
    polys = [b.pts for b in obstacles]
    channels: list[_Arrow] = []
    seen: list[tuple[float, float]] = []
    arrow_len = radius * 0.22
    for j in range(CHANNEL_RAKE_STATIONS):
        # Stations spread along the flow through the central band of the site.
        a = (j / (CHANNEL_RAKE_STATIONS - 1)) * 2 - 1 if CHANNEL_RAKE_STATIONS > 1 else 0
        ox = cx + fx * a * radius * 0.6
        oy = cy + fy * a * radius * 0.6
        ivs: list[tuple[float, float]] = []
        for poly in polys:
            ivs.extend(_line_polygon_intervals(ox, oy, px, py, poly))
        merged = _merge_intervals(ivs)
        # Gaps BETWEEN consecutive covered intervals are flanked both sides.
        for i in range(len(merged) - 1):
            gap = merged[i + 1][0] - merged[i][1]
            if not (CHANNEL_MIN_WIDTH_M <= gap <= CHANNEL_MAX_WIDTH_M):
                continue
            mid = (merged[i][1] + merged[i + 1][0]) / 2.0
            mx, my = ox + px * mid, oy + py * mid
            if any(math.hypot(mx - qx, my - qy) < arrow_len * 0.6
                   for qx, qy in seen):
                continue                       # dedupe channels across stations
            seen.append((mx, my))
            strength = max(0.15, 1.0 - gap / CHANNEL_MAX_WIDTH_M)
            half = arrow_len * (0.6 + 0.6 * strength)
            channels.append(_make_arrow(
                mx - fx * half, my - fy * half, mx + fx * half, my + fy * half,
                z, strength, "channels", head * 1.3))
    return channels


def _rose_geometry(centre, box, radius, theta, rose: WindRose, z):
    """Wind rose in the top-left corner: 16 petals (length ~ frequency), rings,
    spoke labels and a true-north tick. Returns (items, rose_centre)."""
    minx, miny, maxx, maxy = box
    rose_r = max(20.0, radius * 0.22)
    margin = radius * 0.10 + rose_r
    rc = (minx - margin, maxy - rose_r, z)
    items: list[tuple[str, list[tuple[float, float, float]], bool]] = []

    maxf = max(rose.freq) or 1.0
    half = math.radians(360.0 / SECTORS / 2.0)
    for i in range(SECTORS):
        frac = rose.freq[i] / maxf
        if frac <= 0:
            continue
        r = rose_r * frac
        d = _from_vec(i * (360.0 / SECTORS), theta)
        lft = _rot(d[0], d[1], half)
        rgt = _rot(d[0], d[1], -half)
        petal = [rc,
                 (rc[0] + lft[0] * r, rc[1] + lft[1] * r, z),
                 (rc[0] + d[0] * r * 1.04, rc[1] + d[1] * r * 1.04, z),
                 (rc[0] + rgt[0] * r, rc[1] + rgt[1] * r, z),
                 rc]
        items.append(("petal", petal, True))

    # Reference rings at 1/3, 2/3, full of the max frequency.
    for ring in (1 / 3, 2 / 3, 1.0):
        circ = [(rc[0] + rose_r * ring * math.cos(t),
                 rc[1] + rose_r * ring * math.sin(t), z)
                for t in [k / 48 * 2 * math.pi for k in range(49)]]
        items.append(("ring", circ, False))

    # True-north tick (up = model true north) + the four cardinal spokes.
    for lbl, deg in (("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0)):
        d = _from_vec(deg, theta)
        tip = (rc[0] + d[0] * rose_r * 1.18, rc[1] + d[1] * rose_r * 1.18, z)
        items.append((f"label:{lbl}", [tip], False))
    items.append(("ringlabel:%d%%" % round(maxf * 100),
                  [(rc[0], rc[1] + rose_r, z)], False))
    return items, rc


def build_geometry(ctx: SiteContext, rose: WindRose,
                   params: dict[str, Any]) -> _WindBuilt:
    """Precompute all wind geometry once; both writers consume it."""
    centre, box, radius, theta = _site_frame(ctx)
    z = ctx.datum + ARROW_Z_M
    obstacles, skipped = _select_obstacles(ctx)
    edges: list[tuple[float, float, float, float]] = []
    for b in obstacles:
        p = b.pts
        for i in range(len(p)):
            ax, ay = p[i]
            bx, by = p[(i + 1) % len(p)]
            edges.append((ax, ay, bx, by))

    head = max(4.0, radius * 0.035)
    built = _WindBuilt(centre=centre, box=box, radius=radius,
                       convergence_deg=math.degrees(theta), z=z,
                       north_end=(centre[0] + radius * math.sin(theta),
                                  centre[1] + radius * math.cos(theta), z))

    strengths = rose.strength
    order = sorted(range(SECTORS), key=lambda i: strengths[i], reverse=True)
    prevailing = order[0]

    # Prevailing field: bold, many, full reach.
    n_prev = 13
    built.arrows += _streamlines(
        centre, box, radius, _flow_vec(prevailing * 22.5, theta),
        1.0, "arrows_prevailing", z, edges, head,
        n_lines=n_prev, reach_frac=1.0)

    # Secondary fields: each its own layer, fewer/shorter, hidden by default.
    secondary: list[str] = []
    for idx in order[1:1 + N_SECONDARY]:
        rel = strengths[idx] / (strengths[prevailing] or 1.0)
        if rel < SECONDARY_MIN_REL:
            break
        lbl = rose.labels[idx]
        layer = f"arrows_secondary_{lbl}"
        secondary.append(layer)
        built.arrows += _streamlines(
            centre, box, radius, _flow_vec(idx * 22.5, theta),
            rel, layer, z, edges, head,
            n_lines=max(4, round(4 + 6 * rel)), reach_frac=0.5 + 0.4 * rel)

    # Channels along the prevailing direction.
    channels = _channels(centre, box, radius, _flow_vec(prevailing * 22.5, theta),
                         z, obstacles, head)
    built.arrows += channels

    built.rose, built.rose_centre = _rose_geometry(
        centre, box, radius, theta, rose, z)

    built.meta = {
        "prevailing_dir": rose.labels[prevailing],
        "prevailing_freq_pct": round(rose.freq[prevailing] * 100, 1),
        "prevailing_mean_ms": round(rose.mean_speed[prevailing], 1),
        "calm_pct": round(rose.calm_fraction * 100, 1),
        "wind_source": rose.source,
        "wind_period": rose.period,
        "wind_is_fallback": rose.is_fallback,
        "wind_hours": rose.hours,
        "secondary_layers": secondary,
        "channels": len(channels),
        "obstacles": len(obstacles),
        "obstacles_skipped_capped": skipped,
        "obstacle_cap": WIND_MAX_BUILDINGS,
    }
    return built


# ---------------------------------------------------------------------------
# Writers.
# ---------------------------------------------------------------------------
ARROW_COLORS = {
    "arrows_prevailing": (46, 134, 222),
    "channels": (214, 74, 58),
}
SECONDARY_COLOR = (120, 170, 210)
ROSE_COLOR = (90, 160, 120)
NORTH_COLOR = (214, 74, 58)


def _layer(model, name, parent, rgb, cache, visible=True) -> int:
    if name in cache:
        return cache[name]
    lay = rhino3dm.Layer()
    lay.Name = name
    lay.Id = uuid.uuid4()
    if parent is not None:
        lay.ParentLayerId = parent
    lay.Color = (*rgb, 255)
    lay.Visible = visible
    cache[name] = model.Layers.Add(lay)
    return cache[name]


def _attrs(idx: int):
    a = rhino3dm.ObjectAttributes()
    a.LayerIndex = idx
    return a


def _pl(pts):
    if len(pts) < 2:
        return None
    p = rhino3dm.Polyline()
    for x, y, zz in pts:
        p.Add(x, y, zz)
    return p.ToPolylineCurve()


def write_3dm(path: str, built: _WindBuilt) -> dict[str, int]:
    """3D wind arrows + rose as real curves in UTM, aligned with the model."""
    model = rhino3dm.File3dm()
    model.Settings.ModelUnitSystem = rhino3dm.UnitSystem.Meters
    parent = rhino3dm.Layer()
    parent.Name = "WIND"
    parent.Id = uuid.uuid4()
    parent.Color = (46, 134, 222, 255)
    model.Layers.Add(parent)
    cache: dict[str, int] = {}
    counts = {"arrows": 0, "channels": 0, "rose": 0}

    for ar in built.arrows:
        if ar.layer == "channels":
            rgb, vis = ARROW_COLORS["channels"], True
        elif ar.layer == "arrows_prevailing":
            rgb, vis = ARROW_COLORS["arrows_prevailing"], True
        else:
            rgb, vis = SECONDARY_COLOR, False     # secondary hidden by default
        idx = _layer(model, ar.layer, parent.Id, rgb, cache, visible=vis)
        c = _pl(ar.pts)
        if c is not None:
            model.Objects.AddCurve(c, _attrs(idx))
        for seg in ar.segs:
            cs = _pl(seg)
            if cs is not None:
                model.Objects.AddCurve(cs, _attrs(idx))
        counts["channels" if ar.layer == "channels" else "arrows"] += 1

    r_idx = _layer(model, "rose", parent.Id, ROSE_COLOR, cache)
    for kind, pts, closed in built.rose:
        if kind.startswith("label:") or kind.startswith("ringlabel:"):
            txt = kind.split(":", 1)[1]
            model.Objects.AddTextDot(txt, rhino3dm.Point3d(*pts[0]), _attrs(r_idx))
            continue
        c = _pl(pts + (pts[:1] if closed else []))
        if c is not None:
            model.Objects.AddCurve(c, _attrs(r_idx))
            counts["rose"] += 1

    n_idx = _layer(model, "north", parent.Id, NORTH_COLOR, cache)
    c = _pl([built.centre, built.north_end])
    if c is not None:
        model.Objects.AddCurve(c, _attrs(n_idx))
    model.Objects.AddTextDot("N (true)", rhino3dm.Point3d(*built.north_end),
                             _attrs(n_idx))
    model.Write(path, 0)
    return counts


def write_dxf(path: str, built: _WindBuilt) -> dict[str, int]:
    """2D wind plan: arrows + channels + corner rose on WIND_... layers."""
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.M
    msp = doc.modelspace()

    def ensure(name, aci, visible=True, lw=None):
        if name not in doc.layers:
            lay = doc.layers.add(name=name, color=aci)
            if lw is not None:
                lay.dxf.lineweight = lw
            if not visible:
                lay.off()
                lay.freeze()

    counts = {"arrows": 0, "channels": 0, "rose": 0}
    for ar in built.arrows:
        if ar.layer == "channels":
            layer, aci, vis = "WIND_channels", 1, True
            lw = int(30 + 70 * ar.strength)            # narrower gap -> bolder
        elif ar.layer == "arrows_prevailing":
            layer, aci, vis, lw = "WIND_arrows_prevailing", 5, True, 50
        else:
            layer, aci, vis, lw = f"WIND_{ar.layer}", 4, False, 18
        ensure(layer, aci, visible=vis, lw=lw)
        msp.add_lwpolyline([(x, y) for x, y, _ in ar.pts],
                           dxfattribs={"layer": layer, "lineweight": lw})
        for seg in ar.segs:
            msp.add_lwpolyline([(x, y) for x, y, _ in seg],
                               dxfattribs={"layer": layer, "lineweight": lw})
        counts["channels" if ar.layer == "channels" else "arrows"] += 1

    ensure("WIND_rose", 3)
    label_h = max(1.5, built.radius * 0.012)
    for kind, pts, closed in built.rose:
        if kind.startswith("label:") or kind.startswith("ringlabel:"):
            txt = kind.split(":", 1)[1]
            t = msp.add_text(txt, height=label_h, dxfattribs={"layer": "WIND_rose"})
            t.set_placement((pts[0][0], pts[0][1]))
            continue
        pl = msp.add_lwpolyline([(x, y) for x, y, _ in pts],
                                dxfattribs={"layer": "WIND_rose"})
        if closed:
            pl.close(True)
        counts["rose"] += 1

    ensure("WIND_north", 1)
    cx, cy, _ = built.centre
    nx, ny, _ = built.north_end
    msp.add_lwpolyline([(cx, cy), (nx, ny)], dxfattribs={"layer": "WIND_north"})
    t = msp.add_text("N (true)", height=label_h, dxfattribs={"layer": "WIND_north"})
    t.set_placement((nx, ny))

    doc.saveas(path)
    return counts


# ---------------------------------------------------------------------------
# The framework module.
# ---------------------------------------------------------------------------
class WindAnalysis:
    spec = AnalysisSpec(
        key="wind",
        name="Wind Analysis",
        summary="Indicative wind diagram: prevailing direction and relative "
                "strength from measured climatology, with suggested channelling "
                "through gaps. A diagram, not a CFD simulation.",
        data_source="Open-Meteo Historical Weather archive (ERA5 reanalysis), "
                    "10 m wind aggregated to a 16-sector rose — free, keyless. "
                    "Climatology, not live wind; the diagram is not a simulation.",
        data_is_free=True,
        requires=(LOCATION, BUILDINGS),
        outputs=("dxf", "3dm"),
        params=(
            Param("show_secondary", "Include secondary directions (own layers, "
                  "hidden by default)", "bool", True),
        ),
    )

    def run(self, ctx: SiteContext, params: dict[str, Any], formats: list[str],
            out_dir: str) -> AnalysisResult:
        lon, lat = ctx.centroid
        rose = fetch_wind_rose(lat, lon)
        built = build_geometry(ctx, rose, params)

        slug = _slug(ctx.display_name)
        files: list[tuple[str, str]] = []
        counts: dict[str, int] = {}
        if "3dm" in formats:
            p = os.path.join(out_dir, f"{slug}_wind.3dm")
            counts = write_3dm(p, built)
            files.append((p, f"{slug}_wind.3dm"))
        if "dxf" in formats:
            p = os.path.join(out_dir, f"{slug}_wind.dxf")
            counts = write_dxf(p, built)
            files.append((p, f"{slug}_wind.dxf"))

        notes = _notes(built)
        stats = {
            "display_name": ctx.display_name,
            "epsg": ctx.epsg,
            "convergence_deg": round(built.convergence_deg, 3),
            "radius_m": round(built.radius, 1),
            **built.meta,
            **counts,
        }
        return AnalysisResult(files=files, stats=stats, notes=notes)


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return s or "site"


def _notes(built: _WindBuilt) -> list[str]:
    m = built.meta
    notes = [
        "This is an INDICATIVE wind DIAGRAM, not a simulation. It is NOT CFD: it "
        "shows the prevailing measured wind direction and relative strength, and "
        "SUGGESTS channelling from geometry — it does not compute airflow, speed-"
        "up, pressure or turbulence.",
    ]
    if m.get("wind_is_fallback"):
        notes.append(
            "WIND DATA FELL BACK to a generic latitude-band climatology because "
            "Open-Meteo was unreachable — it is NOT site-specific. Re-run later "
            "for the measured rose. " + m.get("wind_source", ""))
    else:
        notes.append(
            f"Wind rose is MEASURED climatology: {m.get('wind_source','')} "
            f"Period {m.get('wind_period','')}, {m.get('wind_hours',0)} hourly "
            f"records. Prevailing wind is FROM {m.get('prevailing_dir')} "
            f"({m.get('prevailing_freq_pct')}% of non-calm hours, mean "
            f"{m.get('prevailing_mean_ms')} m/s); calm {m.get('calm_pct')}% of "
            f"hours. This is a multi-year average, not live or forecast wind.")
    notes.append(
        f"Oriented to TRUE north. UTM grid north differs from true north here by "
        f"{built.convergence_deg:+.2f}° (meridian convergence); wind bearings are "
        f"rotated into the grid by it and true north is drawn on WIND/north.")
    notes.append(
        "Arrows flow across the site FROM the prevailing direction and STOP where "
        "they meet a building facade head-on (a short stub), so blockage vs. "
        "open lanes reads at a glance. The prevailing direction is bold and on by "
        "default; secondary directions are each on their own layer, hidden by "
        "default — switch one on to study it (like the shadow hours).")
    if m.get("channels"):
        notes.append(
            f"{m['channels']} CHANNEL marker(s) on WIND/channels. RULE: a narrow "
            f"open corridor (≤{int(CHANNEL_MAX_WIDTH_M)} m) between two buildings "
            f"that lines up with the prevailing wind is flagged; the narrower the "
            f"gap, the bolder the marker. LIMITATION: this is a 2D footprint rule "
            f"— it ignores building height, roof flow and real pressure, so it "
            f"only SUGGESTS where funnelling is plausible, never proves it.")
    else:
        notes.append(
            "No channels were flagged: no qualifying narrow gaps between "
            "buildings aligned with the prevailing wind were found on this site.")
    if m.get("obstacles_skipped_capped"):
        notes.append(
            f"Free-tier cap: the {m['obstacle_cap']} largest-footprint buildings "
            f"were used as wind obstacles; {m['obstacles_skipped_capped']} smaller "
            f"ones were omitted to stay within the free tier.")
    return notes


register(WindAnalysis())


if __name__ == "__main__":
    # Synthetic check (no network): two boxes with a narrow slot between them,
    # wind forced from the west, must flag a channel in the slot.
    import tempfile

    from fetch_core import get_transformer

    from .framework import Building
    from .wind_data import COMPASS_16, WindRose

    S, W, N, E = 51.50, -0.13, 51.51, -0.11
    tr, epsg = get_transformer(S, W, N, E)
    cx, cy = tr.transform((W + E) / 2, (S + N) / 2)
    # Two 40m-deep blocks north/south of a 10m slot, centred.
    north_block = [(cx - 60, cy + 5), (cx + 60, cy + 5),
                   (cx + 60, cy + 45), (cx - 60, cy + 45)]
    south_block = [(cx - 60, cy - 45), (cx + 60, cy - 45),
                   (cx + 60, cy - 5), (cx - 60, cy - 5)]
    ctx = SiteContext(south=S, west=W, north=N, east=E, transformer=tr,
                      epsg=epsg, display_name="slot test")
    ctx.buildings = [Building(north_block, 20.0, 0.0, True),
                     Building(south_block, 20.0, 0.0, True)]

    # Force a due-west prevailing wind.
    freq = [0.0] * SECTORS
    freq[COMPASS_16.index("W")] = 1.0
    rose = WindRose(labels=COMPASS_16, freq=freq, mean_speed=[8.0] * SECTORS,
                    max_speed=[12.0] * SECTORS, calm_fraction=0.0, hours=1,
                    source="synthetic", period="test", is_fallback=False)
    built = build_geometry(ctx, rose, {"show_secondary": True})
    print(f"EPSG {epsg}  convergence {built.convergence_deg:+.3f}°  "
          f"radius {built.radius:.0f}m")
    print("meta:", {k: built.meta[k] for k in
                    ("prevailing_dir", "channels", "obstacles")})
    assert built.meta["prevailing_dir"] == "W"
    assert built.meta["channels"] >= 1, "the slot between the blocks wasn't flagged"
    with tempfile.TemporaryDirectory() as d:
        c3 = write_3dm(os.path.join(d, "w.3dm"), built)
        cd = write_dxf(os.path.join(d, "w.dxf"), built)
        print("3dm counts", c3, "dxf counts", cd)
    print("OK")
