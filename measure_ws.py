"""Measure peak working set WITHOUT tracemalloc — the v6-comparable, prod-like
number (tracemalloc's own bookkeeping inflates peak_wset by ~100 MB on big
sites, which is why bench.py's numbers run high). One site per process so the
high-water mark is isolated. Usage: python measure_ws.py <site> [--no-lidar].
"""
import os
import sys
import time

import psutil

import build_combined as bc
from build_combined import build_combined

# Optional: force LiDAR on regardless of the governor, to measure its true cost
# on a megasite (SITEGRAB_FORCE_LIDAR=1).
if os.environ.get("SITEGRAB_FORCE_LIDAR") == "1":
    bc.lidar_budget_px = lambda n: bc.LIDAR_MAX_PX

SITES = {
    "shoreditch": ("Shoreditch, London", None),
    "clifton": (None, (51.452, -2.633, 51.468, -2.605)),
    "dubai": ("Dubai Marina", None),
}


def main() -> None:
    site = sys.argv[1]
    area, bbox = SITES[site]
    proc = psutil.Process()
    t0 = time.perf_counter()
    st = build_combined(area, f"ws_{site}.3dm", bbox, terrain=True)
    dt = time.perf_counter() - t0
    peak = getattr(proc.memory_info(), "peak_wset", 0) / 1048576
    li = st.get("lidar", {})
    print(f"{site}: peak_ws_NO_TRACEMALLOC = {peak:.1f} MB  ({dt:.0f}s)")
    print(f"  buildings {st['buildings']}  objects {st['objects']}")
    print(f"  provenance: osm {st.get('prov_osm')} lidar {st.get('prov_lidar')} "
          f"estimated {st.get('prov_estimated')}  covered {st.get('lidar_covered')}")
    print(f"  lidar: available={li.get('available')} "
          f"governed={li.get('governed')} budget_px={li.get('budget_px')} "
          f"res_m={li.get('res_m')}")


if __name__ == "__main__":
    main()
