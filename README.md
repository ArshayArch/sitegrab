# SiteGrab

Type the name of an area (e.g. **Dubai Marina**) and SiteGrab generates downloadable
site-data files for it, built from **live OpenStreetMap data**:

- a **Combined Rhino `.3dm`** — the headline output: the 3D massing and the 2D linework
  in **one spatially aligned model** (linework flat at Z=0 as the ground plane, buildings
  extruded upward above it, both in the same UTM grid),
- a standalone Rhino **`.3dm`** 3D massing model (lean: buildings, main roads, water), and / or
- a standalone AutoCAD **`.dxf`** 2D drawing (detailed: a rich, layered basemap).

After the file(s) are ready, SiteGrab also generates a short, architect-focused **design
brief** for the area via the Anthropic Claude API (see `ANTHROPIC_API_KEY` below).

No database, no accounts. You type a name, tick the outputs you want, and the file(s)
stream back as a download. Everything is reprojected into the correct local **UTM survey
grid** so it lands in real-world metres.

---

## How it works

```
fetch_core.py    geocode (Nominatim) + UTM zone detection + Overpass fetch   (shared)
build_rhino.py   fetched data -> .3dm   (LEAN feature set, rhino3dm)
build_dxf.py     fetched data -> .dxf   (DETAILED feature set, ezdxf, fully headless)
build_combined.py both datasets -> one aligned .3dm (reuses build_rhino + build_dxf logic)
main.py          FastAPI app: serves the frontend + POST /generate + POST /brief
static/          the single-page frontend
```

1. **Geocode** the area name to a bounding box (Nominatim).
2. **Detect the UTM zone** from the bbox centre and build a WGS84 → UTM transformer.
3. **Fetch** the relevant OSM features from Overpass (with mirror failover + backoff).
4. **Build** the requested file(s), reprojecting every coordinate into local metres.
5. **Verify** each output by reading it back (object/layer counts) before returning it.

The 3D model deliberately stays uncluttered (a massing model). The 2D drawing is rich —
a dense city area like Dubai Marina produces ~90+ granular layers and a few MB of DXF.

> The model sits at full real-world UTM coordinates (hundreds of thousands of metres from
> the origin). That is intentional and correct — it is **not** recentred to the origin.

---

## Environment variables

| Variable            | Required?                | Purpose                                                            |
| ------------------- | ------------------------ | ----------------------------------------------------------------- |
| `ANTHROPIC_API_KEY` | For the design brief only | Calls the Anthropic Claude API for the `/brief` site analysis.     |
| `PORT`              | No (default `8000`)      | Port to bind. Render injects this automatically.                  |

`ANTHROPIC_API_KEY` is read **from the environment only — never hardcode it**. If it is not
set, file generation (`/generate`) still works fully; only the design-brief panel is
disabled, and the UI shows a clear note saying so. Get a key from
<https://console.anthropic.com>.

---

## Run it locally (without Docker)

Requires **Python 3.11+** (the geospatial wheels — `rhino3dm`, `pyproj`, `ezdxf` — do not
yet publish builds for Python 3.14, so use 3.11/3.12/3.13).

```bash
python3.11 -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# Set the Anthropic key so the design brief works (optional — /generate works without it):
# macOS/Linux:
export ANTHROPIC_API_KEY="sk-ant-..."
# Windows PowerShell:
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# Windows cmd.exe:
set ANTHROPIC_API_KEY=sk-ant-...

# Start the server (defaults to port 8000):
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>, type an area, tick the outputs, and click **Generate Site Data**.

### Test each piece from the command line

```bash
# Shared core: prints resolved name, detected EPSG and a building count.
python fetch_core.py "Dubai Marina"

# 2D: writes test_marina.dxf and reports layers/entities (expect ~90+ layers).
python build_dxf.py "Dubai Marina"

# 3D: writes test_marina.3dm and reports object/layer counts (5 layers).
python build_rhino.py "Dubai Marina"

# Combined: writes test_combined.3dm and verifies the aligned model
# (two layer groups, ~8900 objects for Dubai Marina, buildings Z>0, linework Z=0).
python build_combined.py "Dubai Marina"
```

### Test the HTTP endpoints

```bash
# The headline combined file -> one aligned .3dm:
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"area":"Dubai Marina","formats":["combined"]}' \
  -o dubai_marina_combined.3dm

# Multiple formats -> a ZIP (valid formats: "combined", "rhino", "dxf"):
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"area":"Dubai Marina","formats":["combined","rhino","dxf"]}' \
  -o dubai_marina_sitegrab.zip

# A single format -> just that file:
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"area":"Dubai Marina","formats":["dxf"]}' \
  -o dubai_marina.dxf

# Design brief (needs ANTHROPIC_API_KEY set on the server). mode: "short" | "long":
curl -X POST http://localhost:8000/brief \
  -H "Content-Type: application/json" \
  -d '{"area":"Dubai Marina","display_name":"Dubai Marina","mode":"short"}'
```

Errors come back as structured JSON: **404** if the area can't be geocoded, **503** if
Overpass is unavailable after retries (or, for `/brief`, if `ANTHROPIC_API_KEY` is not
configured). The frontend surfaces these messages in plain language.

---

## Run it with Docker

```bash
docker build -t sitegrab .
# Pass the Anthropic key through to the container so /brief works:
docker run --rm -e ANTHROPIC_API_KEY="sk-ant-..." -p 8000:8000 sitegrab
```

Then open <http://localhost:8000>. The image is built on `python:3.11-slim`; the build
step imports `pyproj`/`rhino3dm`/`ezdxf`/`anthropic` and runs a real reprojection so a
broken wheel (or missing bundled PROJ data) fails the build rather than the first request.

To mimic Render's injected port:

```bash
docker run --rm -e PORT=10000 -p 10000:10000 sitegrab
```

---

## Deploy to Render (free tier) from GitHub

1. Push this repository to GitHub.
2. In the [Render dashboard](https://dashboard.render.com): **New + → Blueprint**, and
   select this repo. Render reads [`render.yaml`](./render.yaml) and provisions a Docker
   **Web Service** on the **free** plan.
   - *Prefer to click through manually?* **New + → Web Service → Docker**, leave the build
     command empty, and Render uses the `Dockerfile`. The start command is baked into the
     image (`uvicorn ... --port $PORT`). Render injects `PORT` automatically.
3. **Set `ANTHROPIC_API_KEY`.** Because `render.yaml` declares it with `sync: false`,
   Render will **prompt you for the value** during the Blueprint setup. You can also set or
   change it later: open the service → **Environment** tab → **Add Environment Variable** →
   key `ANTHROPIC_API_KEY`, value `sk-ant-...` → **Save Changes** (this triggers a redeploy).
   Without it the site still generates files; only the design brief is disabled.
4. Wait for the first build to finish, then open the service URL.

### Free-tier behaviour (important)

The free plan **sleeps after ~15 minutes idle**. The first request after it sleeps waits
**~30–50 seconds** to wake the server — *on top of* the normal build time for a detailed
area. The frontend's loading message accounts for this, so just keep the tab open.

To remove the cold start later, upgrade to Render's **Starter** plan (~$7/month). It is
**not required** — the app runs fine on free.

---

## Data-source etiquette

SiteGrab relies on the **free public** OpenStreetMap services:

- **Nominatim** (`nominatim.openstreetmap.org`) for geocoding, and
- the **public Overpass API** (`overpass-api.de`, with `overpass.kumi.systems` as a mirror)
  for the map data.

These are fine for **personal and light use**, but their usage policies discourage heavy
automated public traffic. Please be a good citizen:

- All requests send the required custom `User-Agent` header (Nominatim/Overpass block
  requests without one).
- The shared core already retries **politely** with exponential backoff and mirror
  failover — keep that; do not hammer the servers or run high-volume batch jobs against
  the public endpoints.
- For heavier or production use, **self-host Overpass** (or use a paid instance) and swap
  the endpoint list in [`fetch_core.py`](./fetch_core.py) — the `OVERPASS` list is kept at
  the top of the module precisely so it is easy to replace.

Map data is **© OpenStreetMap contributors**, available under the
[Open Database License](https://www.openstreetmap.org/copyright).

---

## Notes & limits

- A single request can take **30 seconds to ~2 minutes** for a detailed area — this is a
  real, long-lived Python process (no serverless / edge pattern).
- Files are generated on demand into a temp directory and deleted right after the response
  is sent.
- Outputs use **metres** as their unit; coordinates are in the area's local UTM zone.
