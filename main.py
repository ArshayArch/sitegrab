"""SiteGrab FastAPI application.

Serves the single-page frontend and exposes:
  - POST /generate : runs the shared pipeline and streams back the requested
    .3dm and/or .dxf file(s).
  - POST /brief    : calls the Anthropic Claude API for a short or long,
    architect-focused design brief about the area.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from build_combined import build_combined
from build_dxf import build_dxf
from build_rhino import build_rhino
from errors import PipelineError

app = FastAPI(title="SiteGrab", description="OSM site data to Rhino / AutoCAD")

STATIC_DIR = Path(__file__).parent / "static"

# Serve everything under static/ (the hero image, any assets) at /static/*.
# Drop the real Rhino screenshot in static/ as hero-model.png to replace the
# placeholder the landing page ships with.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---- waitlist (Pro early-access signups) -------------------------------------
# Signups are appended to a Google Sheet (one row of [email, timestamp]) when the
# GOOGLE_SHEETS_CREDENTIALS + GOOGLE_SHEET_ID env vars are set. If Sheets is not
# configured or the connection fails, we fall back to a local waitlist.json file
# (gitignored — never committed) so a signup is never silently lost.
WAITLIST_PATH = Path(__file__).parent / "waitlist.json"
# Deliberately permissive but real: one @, a dot in the domain, no whitespace.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Simple in-memory rate limit: max 3 signups per IP per hour. Resets on restart,
# which is fine — this only blunts casual abuse, not a determined attacker.
_WAITLIST_HITS: dict[str, list[float]] = defaultdict(list)
WAITLIST_RATE_LIMIT = 3
WAITLIST_RATE_WINDOW = 3600  # seconds

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
    terrain: bool = Field(
        True,
        description="Include real topography (terrain mesh + contours, draped "
        "buildings) in the combined file. Ignored for 'rhino'/'dxf'.",
    )


class AnalysisRequest(BaseModel):
    analysis: str = Field(..., description="Analysis key, e.g. 'sun_path'")
    area: str | None = Field(None, description="Area name (name-search path)")
    bbox: BBox | None = Field(None, description="Explicit bounding box (preferred)")
    formats: list[str] = Field(..., description="Any of the analysis's outputs")
    params: dict = Field(default_factory=dict, description="Analysis parameters")


class BriefRequest(BaseModel):
    area: str = Field(..., min_length=1, description="Area name, e.g. 'Dubai Marina'")
    display_name: str = Field(
        "", description="Full resolved name from geocoding (falls back to area)"
    )
    mode: str = Field("short", description="'short' or 'long'")


class WaitlistRequest(BaseModel):
    email: str = Field(..., description="Email address to add to the Pro waitlist")


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


def _error_response(
    status: int, error: str, message: str, stage: str
) -> JSONResponse:
    """A consistent JSON error body for the frontend.

    Carries a machine-readable ``error`` code, a human ``message`` and the
    ``stage`` that failed. ``detail`` mirrors ``message`` so any client reading
    the FastAPI-standard ``detail`` field still works.
    """
    return JSONResponse(
        status_code=status,
        content={"error": error, "message": message, "stage": stage,
                 "detail": message},
    )


def _provenance_headers(stats: dict | None) -> dict[str, str]:
    """Build the height-provenance response header for the combined model.

    Honest, plain-language summary of how many heights are surveyed (OSM tag or
    sanity-passed LiDAR) vs estimated, plus the LiDAR coverage/fallback reason —
    the v9 Option-C provenance, surfaced to the UI. Empty dict if no combined
    file was built (the header is irrelevant for rhino/dxf-only requests).
    """
    if not stats:
        return {}
    import json
    import urllib.parse as _ul

    osm = stats.get("prov_osm", 0)
    lidar = stats.get("prov_lidar", 0)
    est = stats.get("prov_estimated", 0)
    real = osm + lidar
    total = real + est
    info = stats.get("lidar", {}) or {}
    if lidar:
        basis = f"{real} surveyed ({osm} OSM + {lidar} LiDAR), {est} estimated"
    else:
        basis = f"{real} surveyed (OSM), {est} estimated"
    note = (f"Heights of {total} buildings: {basis}. Real heights sit on "
            f"3D/BUILDINGS_*_real layers, estimates on 3D/BUILDINGS_*_estimated. "
            f"{info.get('reason', '')}").strip()
    payload = {
        "note": note,
        "prov_osm": osm, "prov_lidar": lidar, "prov_estimated": est,
        "lidar_available": bool(info.get("available")),
        "lidar_governed": bool(info.get("governed")),
        "lidar_reason": info.get("reason", ""),
    }
    return {
        "X-SiteGrab-Heights": _ul.quote(json.dumps(payload, ensure_ascii=False)),
        "Access-Control-Expose-Headers": "X-SiteGrab-Heights",
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Landing page — sells the product, captures Pro waitlist signups."""
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/app", response_class=HTMLResponse)
def app_page() -> HTMLResponse:
    """The tool itself — map, generate, analysis, downloads, design brief."""
    return HTMLResponse((STATIC_DIR / "app.html").read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class _DuplicateEmail(Exception):
    """Raised when an email is already present in the destination store."""


def _sheets_configured() -> bool:
    """True if both Google Sheets env vars are set (credentials + sheet id)."""
    return bool(
        os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
        and os.environ.get("GOOGLE_SHEET_ID")
    )


def _open_waitlist_worksheet():
    """Authorize gspread from the service-account JSON and return the worksheet.

    Reads GOOGLE_SHEETS_CREDENTIALS (a JSON string) and GOOGLE_SHEET_ID. Imported
    lazily so the rest of the app runs even if gspread isn't installed.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    creds_dict = json.loads(os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1


def _append_to_waitlist_sheet(email: str, timestamp: str) -> None:
    """Append [email, timestamp] as a new row to the Google Sheet.

    Raises _DuplicateEmail if the email is already in column A. Any other
    exception signals a Sheets failure and is left for the caller to handle.
    """
    worksheet = _open_waitlist_worksheet()
    existing = {value.strip().lower() for value in worksheet.col_values(1)}
    if email in existing:
        raise _DuplicateEmail()
    worksheet.append_row([email, timestamp], value_input_option="USER_ENTERED")


def _append_to_waitlist_json(email: str, timestamp: str) -> int:
    """Append a signup to waitlist.json (the fallback store).

    Tolerates a missing or corrupt file by starting fresh. Raises _DuplicateEmail
    if the email is already present. Returns the new total count.
    """
    entries: list[dict] = []
    if WAITLIST_PATH.exists():
        try:
            loaded = json.loads(WAITLIST_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                entries = loaded
        except (json.JSONDecodeError, OSError):
            entries = []

    if any(isinstance(e, dict) and e.get("email") == email for e in entries):
        raise _DuplicateEmail()

    entries.append({"email": email, "timestamp": timestamp})
    WAITLIST_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return len(entries)


def _store_signup(email: str, timestamp: str) -> str:
    """Persist a signup. Prefer Google Sheets; fall back to waitlist.json.

    Returns the backend used ("sheet" or "json"). Re-raises _DuplicateEmail so
    the caller can map it to a 409. A genuine duplicate is *not* a connection
    failure, so it does not trigger the fallback.
    """
    if _sheets_configured():
        try:
            _append_to_waitlist_sheet(email, timestamp)
            return "sheet"
        except _DuplicateEmail:
            raise
        except Exception as ex:  # noqa: BLE001 — any Sheets failure -> JSON fallback
            print(
                f"[waitlist] Google Sheets unavailable ({ex!r}); "
                "falling back to waitlist.json"
            )
    _append_to_waitlist_json(email, timestamp)
    return "json"


@app.post("/api/waitlist")
def waitlist(req: WaitlistRequest, request: Request) -> dict[str, bool]:
    """Add an email to the Pro early-access waitlist.

    Validates format, rejects duplicates, and rate-limits to 3/hour per IP.
    Each signup is appended with a UTC timestamp to a Google Sheet, falling back
    to waitlist.json if Sheets is unconfigured or unreachable.
    """
    email = req.email.strip().lower()
    if not email or len(email) > 254 or not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Please enter a valid email address.")

    # Rate limit per client IP (in-memory; prune expired hits as we go).
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = [t for t in _WAITLIST_HITS[ip] if now - t < WAITLIST_RATE_WINDOW]
    if len(hits) >= WAITLIST_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many signups from this network. Please try again later.",
        )

    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        backend = _store_signup(email, timestamp)
    except _DuplicateEmail:
        raise HTTPException(status_code=409, detail="You're already on the list.")

    hits.append(now)
    _WAITLIST_HITS[ip] = hits
    print(f"[waitlist] {timestamp}  {email}  (via {backend})")
    return {"success": True}


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
    combined_stats: dict | None = None

    try:
        if "combined" in wanted:
            path = os.path.join(tmpdir, f"{slug}_combined.3dm")
            combined_stats = build_combined(req.area, path, bbox, terrain=req.terrain)
            produced.append((path, f"{slug}_combined.3dm"))
        if "rhino" in wanted:
            path = os.path.join(tmpdir, f"{slug}.3dm")
            build_rhino(req.area, path, bbox)
            produced.append((path, f"{slug}.3dm"))
        if "dxf" in wanted:
            path = os.path.join(tmpdir, f"{slug}.dxf")
            build_dxf(req.area, path, bbox)
            produced.append((path, f"{slug}.dxf"))
    except PipelineError as ex:
        # A stage failed fast with a machine code (overpass/terrain/memory/...):
        # log which stage, return a clean JSON error rather than hanging or 500.
        _cleanup(tmpdir)
        print(f"[generate] stage={ex.stage} error={ex.error_code}: {ex.message}")
        return _error_response(ex.http_status, ex.error_code, ex.message, ex.stage)
    except ValueError as ex:
        # Name path: geocoding failure -> 404. Bbox path: an invalid or
        # oversized drawn box is a bad request -> 422.
        _cleanup(tmpdir)
        status = 422 if bbox else 404
        code = "invalid_area" if bbox else "area_not_found"
        print(f"[generate] stage=validate error={code}: {ex}")
        return _error_response(status, code, str(ex), "validate")
    except RuntimeError as ex:
        # Any other service unavailability after retries (non-typed).
        _cleanup(tmpdir)
        print(f"[generate] stage=fetch error=service_unavailable: {ex}")
        return _error_response(503, "service_unavailable", str(ex), "fetch")
    except Exception as ex:  # noqa: BLE001
        # Unhandled failure during the Rhino/DXF write (or anywhere else): still
        # a clean JSON error, never a dropped connection or raw 500 page.
        _cleanup(tmpdir)
        print(f"[generate] stage=write error=generation_failed: {ex!r}")
        return _error_response(500, "generation_failed",
                               f"Generation failed: {ex}", "write")

    # Height-provenance note for the combined file (Option C): surfaced to the
    # browser via a header so the UI can show which heights are surveyed.
    prov_headers = _provenance_headers(combined_stats)

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
            headers=prov_headers,
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
        headers=prov_headers,
        background=BackgroundTask(_cleanup, tmpdir),
    )


@app.get("/analyses")
def analyses() -> dict:
    """List the available site analyses (the frontend's selectable menu)."""
    import analysis

    return {"analyses": [spec.to_public() for spec in analysis.list_specs()]}


@app.post("/analysis")
def analysis_run(req: AnalysisRequest):
    """Run one site-analysis module and stream back its .3dm and/or .dxf."""
    import analysis
    from analysis.runner import run_analysis

    bbox: tuple[float, float, float, float] | None = None
    if req.bbox is not None:
        bbox = (req.bbox.south, req.bbox.west, req.bbox.north, req.bbox.east)
        slug = "custom_area"
    elif req.area:
        slug = _slugify(req.area)
    else:
        raise HTTPException(
            status_code=422, detail="Provide either an area name or a bounding box.")

    tmpdir = tempfile.mkdtemp(prefix="sitegrab_analysis_")
    try:
        result = run_analysis(
            req.analysis, req.area, bbox, req.params, req.formats, tmpdir)
    except ValueError as ex:
        _cleanup(tmpdir)
        # Unknown analysis / bad format / geocode miss -> bad request or 404.
        raise HTTPException(status_code=422 if bbox else 404, detail=str(ex))
    except RuntimeError as ex:
        _cleanup(tmpdir)
        raise HTTPException(status_code=503, detail=str(ex))
    except Exception as ex:  # noqa: BLE001
        _cleanup(tmpdir)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {ex}")

    produced = result.files
    if not produced:
        _cleanup(tmpdir)
        raise HTTPException(status_code=500, detail="Analysis produced no output.")

    # Surface the honesty notes + headline stats to the browser via headers
    # (the body is the file). URL-encoded UTF-8 so non-ASCII (°, —) is header-safe.
    import json
    import urllib.parse as _ul
    headers = {
        "X-SiteGrab-Notes": _ul.quote(json.dumps(result.notes, ensure_ascii=False)),
        "X-SiteGrab-Stats": _ul.quote(json.dumps(result.stats, default=str)),
        "Access-Control-Expose-Headers": "X-SiteGrab-Notes, X-SiteGrab-Stats",
    }

    if len(produced) == 1:
        path, name = produced[0]
        media = "application/octet-stream" if name.endswith(".3dm") else "image/vnd.dxf"
        return FileResponse(path, media_type=media, filename=name, headers=headers,
                            background=BackgroundTask(_cleanup, tmpdir))

    zip_path = os.path.join(tmpdir, f"{slug}_{req.analysis}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in produced:
            zf.write(path, arcname=name)
    return FileResponse(zip_path, media_type="application/zip",
                        filename=f"{slug}_{req.analysis}.zip", headers=headers,
                        background=BackgroundTask(_cleanup, tmpdir))


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
