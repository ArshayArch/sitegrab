"""SiteGrab site-analysis framework — the pluggable contract.

The thinnest contract that lets a new analysis declare what it is, receive a
resolved site, and emit aligned CAD layers. See ANALYSIS_FRAMEWORK.md for the
design rationale and the DATA-vs-REPRESENTATION discipline every module follows.

Four small types — :class:`SiteContext` (what a module receives),
:class:`AnalysisSpec` (what it declares), :class:`Param` (one typed input) and
:class:`AnalysisResult` (what it returns) — plus a tiny registry. No base class
to inherit: a module is any object exposing a ``spec`` and a ``run`` method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# Data sources a module may declare in ``AnalysisSpec.requires``. The runner
# lazily attaches ONLY the ones a module asks for — the framework's free-tier
# discipline: pull exactly the data you declare, nothing more.
LOCATION = "location"     # always available (the resolved site itself)
BUILDINGS = "buildings"   # lean OSM building footprints + heights
TERRAIN = "terrain"       # the DEM elevation grid


@dataclass
class Building:
    """One OSM building footprint, ready for analysis.

    ``pts`` are the footprint vertices in UTM metres (the same projection as the
    geometry model). ``base`` is ground height at the footprint (0.0 with no
    terrain); ``height`` is the real-or-estimated height in metres and
    ``height_estimated`` records which — so an analysis can be honest about
    where its numbers came from.
    """

    pts: list[tuple[float, float]]
    height: float
    base: float
    height_estimated: bool


@dataclass
class SiteContext:
    """The resolved site every analysis receives.

    Built by the runner from the *existing* ``fetch_core`` pipeline, so an
    analysis never re-implements geocoding, the bbox caps, the UTM zone choice
    or the transformer — and so its output lands in the SAME coordinates as the
    geometry model automatically. ``grid`` and ``buildings`` are populated by
    the runner only when the module's ``spec.requires`` asks for them.
    """

    south: float
    west: float
    north: float
    east: float
    transformer: Any   # pyproj WGS84 -> UTM (the same one the geometry uses)
    epsg: int
    display_name: str
    grid: Any = None                       # ElevationGrid | None
    buildings: list[Building] | None = None

    @property
    def centroid(self) -> tuple[float, float]:
        """Site centroid as (lon, lat) — the natural anchor for an analysis."""
        return ((self.west + self.east) / 2.0, (self.south + self.north) / 2.0)

    @property
    def datum(self) -> float:
        """Flat ground datum: the site's lowest terrain elevation (floored to a
        whole metre), matching ``build_combined``; 0.0 with no terrain."""
        import math
        return float(math.floor(self.grid.zmin)) if self.grid is not None else 0.0


@dataclass
class Param:
    """One typed, defaulted input a module accepts (drives UI + validation)."""

    name: str
    label: str
    kind: str                 # "bool" | "choice" | "multichoice"
    default: Any
    choices: tuple[str, ...] = ()


@dataclass
class AnalysisSpec:
    """Everything a module declares about itself.

    The single source of truth the frontend reads to render the analysis menu
    and the runner reads to know what to prepare, validate and emit.
    """

    key: str
    name: str
    summary: str
    data_source: str
    data_is_free: bool
    requires: tuple[str, ...]
    outputs: tuple[str, ...]
    params: tuple[Param, ...] = ()

    def to_public(self) -> dict[str, Any]:
        """JSON-safe description for the frontend menu."""
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "data_source": self.data_source,
            "data_is_free": self.data_is_free,
            "requires": list(self.requires),
            "outputs": list(self.outputs),
            "params": [
                {"name": p.name, "label": p.label, "kind": p.kind,
                 "default": p.default, "choices": list(p.choices)}
                for p in self.params
            ],
        }


@dataclass
class AnalysisResult:
    """What a module's ``run`` returns."""

    files: list[tuple[str, str]] = field(default_factory=list)  # (path, name)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class AnalysisModule(Protocol):
    """Structural type a module satisfies — no inheritance required."""

    spec: AnalysisSpec

    def run(
        self,
        ctx: SiteContext,
        params: dict[str, Any],
        formats: list[str],
        out_dir: str,
    ) -> AnalysisResult: ...


# ---------------------------------------------------------------------------
# Registry. Modules self-register on import (see analysis/__init__.py).
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, AnalysisModule] = {}


def register(module: AnalysisModule) -> AnalysisModule:
    """Register a module under its ``spec.key`` (idempotent on re-import)."""
    _REGISTRY[module.spec.key] = module
    return module


def get(key: str) -> AnalysisModule:
    """Look up a module by key, or raise ValueError (a 404-shaped error)."""
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ValueError(f"Unknown analysis '{key}'.")


def list_specs() -> list[AnalysisSpec]:
    """All registered specs — the frontend's selectable analysis menu."""
    return [m.spec for m in _REGISTRY.values()]
