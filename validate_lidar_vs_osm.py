"""Measure how well LiDAR-derived heights agree with surveyed OSM tags.

The accuracy backbone for HEIGHTS_PROVENANCE.md: on a LiDAR-covered England site,
for every building that ALSO carries a real OSM height/levels tag, sample the
LiDAR height (DSM-DTM 85th percentile inside the footprint) and compare it to the
tag. The tag is the independent ground truth; the median absolute difference is
the honest, reproducible accuracy figure quoted in the doc. One site per process.
Usage: python validate_lidar_vs_osm.py [clifton|shoreditch_small]
"""
import sys

import numpy as np

import build_rhino as rh
from fetch_core import fetch_overpass
from fetch_lidar import fetch_lidar_heights

# Small, LiDAR-covered England bboxes (S, W, N, E). Must be under the governor's
# megasite skip so LiDAR actually runs.
BBOXES = {
    "clifton": (51.452, -2.633, 51.468, -2.605),
    "shoreditch_small": (51.523, -0.085, 51.531, -0.072),
}


def main() -> None:
    key = sys.argv[1] if len(sys.argv) > 1 else "clifton"
    s, w, n, e = BBOXES[key]
    bbox = f"{s},{w},{n},{e}"
    lean = fetch_overpass(rh._QUERY_TEMPLATE.format(bbox=bbox))
    lidar, info = fetch_lidar_heights(s, w, n, e)
    if lidar is None:
        print(f"{key}: no LiDAR ({info.get('reason')})")
        return

    diffs = []
    tagged = 0
    for el in lean.get("elements", []):
        if el.get("type") != "way" or "building" not in el.get("tags", {}):
            continue
        tags = el.get("tags", {})
        real = rh._real_height(tags)
        if real is None:
            continue
        geom = el.get("geometry") or []
        if len(geom) < 4:
            continue
        tagged += 1
        lh, npx = lidar.height_for([(p["lon"], p["lat"]) for p in geom])
        if lh is None:
            continue
        diffs.append(abs(lh - real))

    d = np.array(diffs)
    print(f"site={key}  bbox={bbox}")
    print(f"  OSM-tagged buildings: {tagged}")
    print(f"  with a LiDAR sample : {len(diffs)}")
    if len(diffs):
        print(f"  median |LiDAR - OSM|: {np.median(d):.2f} m")
        print(f"  mean   |LiDAR - OSM|: {np.mean(d):.2f} m")
        print(f"  90th pctl          : {np.percentile(d, 90):.2f} m")
        print(f"  within 3 m         : {100*np.mean(d <= 3):.0f}%")
        print(f"  within 5 m         : {100*np.mean(d <= 5):.0f}%")
    lidar.release()


if __name__ == "__main__":
    main()
