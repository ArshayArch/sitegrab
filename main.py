"""SiteGrab FastAPI application.

Serves the single-page frontend and exposes POST /generate, which runs the
shared pipeline and streams back the requested .3dm and/or .dxf file(s).
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

from build_dxf import build_dxf
from build_rhino import build_rhino

app = FastAPI(title="SiteGrab", description="OSM site data to Rhino / AutoCAD")

STATIC_DIR = Path(__file__).parent / "static"


class GenerateRequest(BaseModel):
    area: str = Field(..., min_length=1, description="Area name, e.g. 'Dubai Marina'")
    formats: list[str] = Field(..., description="Any of 'rhino', 'dxf'")


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
    wanted = [f for f in formats if f in ("rhino", "dxf")]
    if not wanted:
        raise HTTPException(
            status_code=422,
            detail="Select at least one format: 'rhino' and/or 'dxf'.",
        )

    slug = _slugify(req.area)
    tmpdir = tempfile.mkdtemp(prefix="sitegrab_")
    produced: list[tuple[str, str]] = []  # (path, download_name)

    try:
        if "dxf" in wanted:
            path = os.path.join(tmpdir, f"{slug}.dxf")
            build_dxf(req.area, path)
            produced.append((path, f"{slug}.dxf"))
        if "rhino" in wanted:
            path = os.path.join(tmpdir, f"{slug}.3dm")
            build_rhino(req.area, path)
            produced.append((path, f"{slug}.3dm"))
    except ValueError as ex:
        # Geocoding failure -> area not found.
        _cleanup(tmpdir)
        raise HTTPException(status_code=404, detail=str(ex))
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
