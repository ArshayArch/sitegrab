"""SiteGrab FastAPI application.

Serves the single-page frontend and exposes:
  - POST /generate : runs the shared pipeline and streams back the requested
    .3dm and/or .dxf file(s).
  - POST /brief    : calls the Anthropic Claude API for a short or long,
    architect-focused design brief about the area.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from build_combined import build_combined
from build_dxf import build_dxf
from build_rhino import build_rhino

app = FastAPI(title="SiteGrab", description="OSM site data to Rhino / AutoCAD")

STATIC_DIR = Path(__file__).parent / "static"

# The spec named claude-sonnet-4-20250514, but that model is deprecated and
# retires 2026-06-15 — its documented drop-in replacement is claude-sonnet-4-6.
# Kept as a single constant so the model is trivial to change.
BRIEF_MODEL = "claude-sonnet-4-6"
BRIEF_MAX_TOKENS = 800

BRIEF_SYSTEM_PROMPT = (
    "You are a senior architect and urban designer with deep knowledge of cities "
    "worldwide.\n"
    "A student or early-career designer has just pulled site data for the area described.\n"
    "Write them a concise, insightful site analysis that feels like advice from a thoughtful\n"
    "tutor — not a Wikipedia summary. Cover: what kind of urban fabric this is, its key spatial\n"
    "characteristics, the grain and scale of the city here, any significant edges, landmarks,\n"
    "or thresholds, and two or three genuine design opportunities or tensions a designer\n"
    "starting here should hold in mind. Be specific to this place. Write in a direct,\n"
    "intelligent tone. For 'short' mode: 150–200 words. For 'long' mode: 400–500 words."
)


class BBox(BaseModel):
    south: float = Field(..., ge=-90, le=90)
    west: float = Field(..., ge=-180, le=180)
    north: float = Field(..., ge=-90, le=90)
    east: float = Field(..., ge=-180, le=180)


class GenerateRequest(BaseModel):
    area: str | None = Field(
        None, description="Area name, e.g. 'Dubai Marina' (name-search path)"
    )
    bbox: BBox | None = Field(
        None, description="Explicit bounding box drawn on the map (preferred if present)"
    )
    formats: list[str] = Field(
        ..., description="Any of 'combined', 'rhino', 'dxf'"
    )


class BriefRequest(BaseModel):
    area: str = Field(..., min_length=1, description="Area name, e.g. 'Dubai Marina'")
    display_name: str = Field(
        "", description="Full resolved name from geocoding (falls back to area)"
    )
    mode: str = Field("short", description="'short' or 'long'")


def _slugify(name: str) -> str:
    """Filesystem-safe slug derived from the area name."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return slug or "site"


def _cleanup(tmpdir: str) -> None:
    """Remove the temp directory and everything generated inside it."""
    try:
        for f in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)
    except OSError:
        pass


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate")
def generate(req: GenerateRequest):
    formats = [f.lower() for f in req.formats]
    # Preserve a stable, sensible order (headline combined first) and de-dupe.
    wanted = [f for f in ("combined", "rhino", "dxf") if f in formats]
    if not wanted:
        raise HTTPException(
            status_code=422,
            detail="Select at least one format: 'combined', 'rhino' and/or 'dxf'.",
        )

    # Resolve the input source: an explicit drawn bbox takes precedence over a
    # typed name. Pass the bbox straight through to the pipeline (no geocoding).
    bbox: tuple[float, float, float, float] | None = None
    if req.bbox is not None:
        bbox = (req.bbox.south, req.bbox.west, req.bbox.north, req.bbox.east)
        slug = "custom_area"
    elif req.area:
        slug = _slugify(req.area)
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either an area name or a bounding box.",
        )

    tmpdir = tempfile.mkdtemp(prefix="sitegrab_")
    produced: list[tuple[str, str]] = []  # (path, download_name)

    try:
        if "combined" in wanted:
            path = os.path.join(tmpdir, f"{slug}_combined.3dm")
            build_combined(req.area, path, bbox)
            produced.append((path, f"{slug}_combined.3dm"))
        if "rhino" in wanted:
            path = os.path.join(tmpdir, f"{slug}.3dm")
            build_rhino(req.area, path, bbox)
            produced.append((path, f"{slug}.3dm"))
        if "dxf" in wanted:
            path = os.path.join(tmpdir, f"{slug}.dxf")
            build_dxf(req.area, path, bbox)
            produced.append((path, f"{slug}.dxf"))
    except ValueError as ex:
        # Name path: geocoding failure -> 404. Bbox path: an invalid or
        # oversized drawn box is a bad request -> 422.
        _cleanup(tmpdir)
        raise HTTPException(status_code=422 if bbox else 404, detail=str(ex))
    except RuntimeError as ex:
        # Overpass unavailable after retries.
        _cleanup(tmpdir)
        raise HTTPException(status_code=503, detail=str(ex))
    except Exception as ex:  # noqa: BLE001
        _cleanup(tmpdir)
        raise HTTPException(status_code=500, detail=f"Generation failed: {ex}")

    if len(produced) == 1:
        path, name = produced[0]
        media = (
            "application/octet-stream"
            if name.endswith(".3dm")
            else "image/vnd.dxf"
        )
        return FileResponse(
            path,
            media_type=media,
            filename=name,
            background=BackgroundTask(_cleanup, tmpdir),
        )

    # Both formats -> bundle into a ZIP.
    zip_path = os.path.join(tmpdir, f"{slug}_sitegrab.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in produced:
            zf.write(path, arcname=name)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{slug}_sitegrab.zip",
        background=BackgroundTask(_cleanup, tmpdir),
    )


@app.post("/brief")
def brief(req: BriefRequest):
    """Generate an architect-focused design brief via the Anthropic Claude API."""
    mode = req.mode.lower().strip()
    if mode not in ("short", "long"):
        raise HTTPException(
            status_code=422, detail="mode must be 'short' or 'long'."
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Design briefs are unavailable: ANTHROPIC_API_KEY is not configured "
            "on the server.",
        )

    # Imported lazily so /generate works even if the anthropic package is absent.
    import anthropic

    display = req.display_name.strip() or req.area
    client = anthropic.Anthropic(api_key=api_key, timeout=60.0)

    try:
        message = client.messages.create(
            model=BRIEF_MODEL,
            max_tokens=BRIEF_MAX_TOKENS,
            system=BRIEF_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Area: {display}. Generate a {mode} design brief for this site.",
                }
            ],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(
            status_code=502,
            detail="The server's ANTHROPIC_API_KEY was rejected. Check the key.",
        )
    except anthropic.RateLimitError:
        raise HTTPException(
            status_code=429,
            detail="The design-brief service is rate limited right now. Try again shortly.",
        )
    except anthropic.APITimeoutError:
        raise HTTPException(
            status_code=504,
            detail="The design-brief request timed out. Try again.",
        )
    except anthropic.APIConnectionError:
        raise HTTPException(
            status_code=502,
            detail="Could not reach the design-brief service. Try again.",
        )
    except anthropic.APIStatusError as ex:
        raise HTTPException(
            status_code=502,
            detail=f"The design-brief service returned an error ({ex.status_code}).",
        )

    text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise HTTPException(
            status_code=502, detail="The design-brief service returned an empty response."
        )
    return {"brief": text}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
