"""Typed pipeline errors — fail fast with a machine code, not a hung request.

Every stage of the generate pipeline (Overpass, terrain, LiDAR, memory, write)
can fail in a distinct way. Rather than letting a failure hang the connection or
surface as a raw 500, each stage raises one of these so the endpoint can return a
clean JSON body ``{"error": <code>, "message": <human>, "stage": <stage>}`` and
log *which* stage failed.

All subclass :class:`RuntimeError` so existing ``except RuntimeError -> 503``
handlers (e.g. the /analysis endpoint) keep working unchanged; the /generate
endpoint catches :class:`PipelineError` first for the richer body.
"""

from __future__ import annotations


class PipelineError(RuntimeError):
    """A staged pipeline failure carrying a machine code + HTTP status.

    Subclasses set ``error_code``, ``stage``, ``http_status`` and a
    ``default_message``; the message may be overridden per-instance.
    """

    error_code: str = "generation_failed"
    stage: str = "unknown"
    http_status: int = 503
    default_message: str = "Generation failed."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)

    @property
    def message(self) -> str:
        return str(self)


class OverpassTimeoutError(PipelineError):
    error_code = "overpass_timeout"
    stage = "overpass"
    http_status = 504
    default_message = (
        "The map-data service (OpenStreetMap / Overpass) took too long to respond. "
        "This can happen on large or dense areas, or when the public servers are "
        "busy. Try again, or draw a smaller box."
    )


class OverpassUnavailableError(PipelineError):
    error_code = "overpass_unavailable"
    stage = "overpass"
    http_status = 503
    default_message = (
        "The map-data service (OpenStreetMap / Overpass) is unavailable right now. "
        "Try again in a moment."
    )


class TerrainTimeoutError(PipelineError):
    error_code = "terrain_timeout"
    stage = "terrain"
    http_status = 504
    default_message = (
        "The elevation-tile service took too long to respond. Try again, or turn "
        "off Include terrain to generate without topography."
    )


class TerrainUnavailableError(PipelineError):
    error_code = "terrain_unavailable"
    stage = "terrain"
    http_status = 503
    default_message = (
        "The elevation-tile service is unavailable right now. Try again, or turn "
        "off Include terrain to generate without topography."
    )


class AreaTooLargeError(PipelineError):
    error_code = "area_too_large"
    stage = "validate"
    http_status = 413
    default_message = (
        "Area too large or dense for the current server limits. Try a smaller box, "
        "or disable terrain."
    )


class MemoryLimitError(PipelineError):
    error_code = "memory_limit"
    stage = "build"
    http_status = 507
    default_message = (
        "This area is too large or dense to build within the server's memory limit. "
        "Try a smaller box, or disable terrain."
    )
