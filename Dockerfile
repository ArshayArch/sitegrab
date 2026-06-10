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
# inside the image — fail the build early if a wheel is broken.
RUN python -c "import pyproj, rhino3dm, ezdxf, anthropic; \
from pyproj import Transformer; \
t = Transformer.from_crs('EPSG:4326', 'EPSG:32640', always_xy=True); \
print('deps OK', t.transform(55.14, 25.08))"

COPY . .

EXPOSE 8000

# Render injects $PORT; default to 8000 for local runs. Use shell form so the
# environment variable is expanded at container start.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
