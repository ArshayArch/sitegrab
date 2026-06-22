# SiteGrab — slim Python image. pyproj/rhino3dm/ezdxf all ship manylinux wheels
# for cp311, so no system build toolchain is required.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Verify the geospatial stack (and its bundled PROJ data) imports cleanly
# inside the image — fail the build early if a wheel is broken. numpy/Pillow
# power the elevation/terrain pipeline.
RUN python -c "import pyproj, rhino3dm, ezdxf, anthropic, numpy, PIL.Image; \
import astral; from astral.sun import elevation; \
from astral import Observer; \
from pyproj import Transformer; \
t = Transformer.from_crs('EPSG:4326', 'EPSG:32640', always_xy=True); \
import datetime as _dt; \
alt = elevation(Observer(latitude=51.48, longitude=0.0), \
_dt.datetime(2026, 6, 21, 12, tzinfo=_dt.timezone.utc)); \
assert 60 < alt < 64, alt; \
print('deps OK', t.transform(55.14, 25.08), 'sun_alt', round(alt, 1))"

COPY . .

# Fail the build early if the analysis package can't import or a module forgot
# to register. Wind adds NO new dependency (its data layer is stdlib urllib over
# the keyless Open-Meteo API), so this just guards the registry wiring.
RUN python -c "import analysis; \
keys = {s.key for s in analysis.list_specs()}; \
assert {'sun_path', 'wind'} <= keys, keys; \
print('analyses OK', sorted(keys))"

# Guard the v9 LiDAR-heights path: confirm the height sampler imports (it leans
# on the already-verified numpy/Pillow/pyproj wheels, no new dependency) and that
# the memory governor still enforces its safety invariant — full raster on a
# neighbourhood site, SKIP on a megasite — so the augmented build can never blow
# the 512 MB tier. A regression here fails the image build, not production.
RUN python -c "import fetch_lidar; \
from build_combined import lidar_budget_px, LIDAR_MAX_PX; \
assert lidar_budget_px(2000) == LIDAR_MAX_PX, lidar_budget_px(2000); \
assert lidar_budget_px(13023) == 0, lidar_budget_px(13023); \
print('lidar guard OK', LIDAR_MAX_PX)"

EXPOSE 8000

# Render injects $PORT; default to 8000 for local runs. Use shell form so the
# environment variable is expanded at container start.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
