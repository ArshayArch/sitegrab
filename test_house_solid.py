"""Unit test for v6 house_solid: validity + closedness across footprint shapes."""
import rhino3dm as r

from build_rhino import house_mesh, house_solid

CASES = {
    "rectangle": [(0, 0), (10, 0), (10, 6), (0, 6)],
    "rect_closed_ring": [(0, 0), (10, 0), (10, 6), (0, 6), (0, 0)],
    "square_pyramid": [(0, 0), (8, 0), (8, 8), (0, 8)],
    "L_shape": [(0, 0), (12, 0), (12, 5), (7, 5), (7, 9), (0, 9)],
    "irregular8": [(0, 0), (9, 0.3), (9.2, 4), (8.8, 7), (5, 7.2),
                   (1, 6.9), (-0.2, 4), (-0.1, 2)],
    "rot45_rect": [(0, 0), (7.07, 7.07), (2.83, 11.31), (-4.24, 4.24)],
    "concave_T": [(0, 0), (12, 0), (12, 3), (8, 3), (8, 8), (4, 8), (4, 3), (0, 3)],
    "dup_points": [(0, 0), (10, 0), (10, 0), (10, 6), (0, 6), (0, 0)],
    "sliver": [(0, 0), (10, 0), (10, 1.0), (0, 1.0)],  # expect None (flat top)
    "cw_winding": [(0, 6), (10, 6), (10, 0), (0, 0)],
}

fails = 0
for name, pts in CASES.items():
    b = house_solid(pts, base=12.5, height=6.0)
    m = house_mesh(pts, base=12.5, height=6.0)
    if b is None:
        status = "None (fallback)"
        if name != "sliver":
            fails += 1
            status += "  <-- UNEXPECTED"
        if (m is None) != (b is None) and name == "sliver":
            status += "  mesh also None: OK" if m is None else "  BUT mesh exists!"
    else:
        ok = b.IsValid and b.IsSolid
        bb = b.GetBoundingBox()
        z_ok = abs(bb.Min.Z - 12.5) < 1e-9 and abs(bb.Max.Z - 18.5) < 1e-6
        status = f"valid={b.IsValid} solid={b.IsSolid} faces={len(b.Faces)} z_ok={z_ok}"
        if not (ok and z_ok):
            fails += 1
            status += "  <-- FAIL"
    print(f"{name:18}: {status}")

print()
print("FAILURES:", fails)
raise SystemExit(1 if fails else 0)
