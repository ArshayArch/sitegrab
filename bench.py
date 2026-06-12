"""Canonical-site benchmark for SiteGrab builds.

Runs build_combined on the three canonical test sites and reports, for each:
tracemalloc peak (comparable with earlier versions' numbers), process RSS
growth (the number Render's 512MB limit actually sees, including rhino3dm's
C++ heap which tracemalloc cannot observe), wall time, output file size and
the headline object counts.

Each site runs in a FRESH subprocess so RSS measurements don't contaminate
each other. Usage:  python bench.py [site ...]   (default: all three)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

# Geocoded display names are routinely non-Latin (e.g. Dubai); never let the
# Windows cp1252 console kill a benchmark over them.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SITES: dict[str, dict] = {
    # Flat + dense towers.
    "dubai": {"area": "Dubai Marina", "bbox": None},
    # The building stress case (~13k buildings).
    "shoreditch": {"area": "Shoreditch, London", "bbox": None},
    # Hilly relief (Avon Gorge) + suburban housing; v5's default test bbox.
    "clifton": {"area": None, "bbox": (51.452, -2.633, 51.468, -2.605)},
}


def run_one(name: str) -> dict:
    """Benchmark one site in this process and return the measurements."""
    import tracemalloc

    import psutil

    from build_combined import build_combined

    cfg = SITES[name]
    out = f"bench_{name}.3dm"
    proc = psutil.Process()
    rss0 = proc.memory_info().rss

    tracemalloc.start()
    t0 = time.perf_counter()
    stats = build_combined(cfg["area"], out, cfg["bbox"])
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    mem = proc.memory_info()
    rss1 = mem.rss
    # Windows exposes the true high-water mark; that (not end RSS) is what a
    # 512MB container limit would kill on. Includes rhino3dm's C++ heap that
    # tracemalloc cannot see.
    peak_ws = getattr(mem, "peak_wset", 0)

    return {
        "site": name,
        "display_name": stats["display_name"],
        "tracemalloc_peak_mb": round(peak / 1048576, 1),
        "rss_start_mb": round(rss0 / 1048576, 1),
        "rss_end_mb": round(rss1 / 1048576, 1),
        "rss_growth_mb": round((rss1 - rss0) / 1048576, 1),
        "peak_workingset_mb": round(peak_ws / 1048576, 1),
        "time_s": round(dt, 1),
        "file_mb": round(os.path.getsize(out) / 1048576, 2),
        "objects": stats["objects"],
        "buildings": stats["buildings"],
        "houses": stats["houses"],
        "blocks": stats["buildings"] - stats["houses"],
        "greens": stats["greens"],
        "terrain": stats["terrain"],
        **{k: stats[k] for k in ("house_solids", "house_mesh_fallbacks",
                                 "greens_planar", "greens_mesh",
                                 "terrain_surface")
           if k in stats},
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--one":
        print(json.dumps(run_one(args[1])))
        sys.exit(0)

    names = args or list(SITES)
    results = []
    for name in names:
        r = subprocess.run(
            [sys.executable, __file__, "--one", name],
            capture_output=True, text=True, cwd=os.path.dirname(__file__),
        )
        if r.returncode != 0:
            print(f"== {name}: FAILED ==\n{r.stdout}\n{r.stderr}")
            continue
        data = json.loads(r.stdout.strip().splitlines()[-1])
        results.append(data)
        print(f"== {data['site']} ({data['display_name'][:50]}) ==")
        for k, v in data.items():
            if k not in ("site", "display_name"):
                print(f"  {k:20}: {v}")

    with open("bench_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print("\nsaved -> bench_results.json")
