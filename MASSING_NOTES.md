# Massing Notes — type-based heights, roofs, and the honesty caveat

## The honesty caveat (governs everything below)

**OSM does not contain real heights for most buildings, and this model does not pretend
otherwise.** Where a building carries a real `height` or `building:levels` tag, that value
is used and ALWAYS overrides any estimate. Everywhere else, heights are **plausible,
type-driven estimates** whose only job is to make the model *legible and believable* —
a 2-storey terrace should read differently from a tower. The output must never be
mistaken for surveyed height data; do not measure, daylight-test, or rights-of-light a
scheme against estimated neighbours.

## 1. What a designer actually reads a city by

- **The storey is the unit.** ~3–3.5m per storey (more for ground floors, retail and
  industrial). A designer doesn't read "16.3m"; they read "five storeys".
- **The scale jumps carry the information:** 2-storey terrace (~6–7m) vs 4–6-storey
  mansion block / mid-rise (~12–18m) vs point tower (20m+) vs big-box shed (one tall
  storey, ~8–9m, huge footprint) vs civic landmark (tall volume, 15–30m, often
  freestanding). Getting *these categories* right matters far more than per-building
  accuracy.
- **Form seals the read:** houses have pitched roofs; blocks, sheds and offices are flat.
  A low pitched roofscape against a flat-topped core is how you instantly see where the
  residential grain ends.
- **Footprint betrays type:** 40 m² is never an office block; 5,000 m² is never a house.
  Footprint area is the cross-check on every estimate.

## 2. The mapping used (estimates only — real tags always win)

Priority per building:
1. `height` tag → used as-is.
2. `building:levels` → levels × 3.3m.
3. Otherwise, estimate from `building=` type:

| OSM `building=`                          | Estimate | Read as              |
| ---------------------------------------- | -------- | -------------------- |
| `bungalow`                               | 3.8m     | 1 storey             |
| `house`, `detached`, `semidetached_house`| ~5.8–6m  | 2 storeys            |
| `terrace`                                | 6.8m     | 2 storeys + parapet grain |
| `residential`                            | 12m      | walk-up block        |
| `apartments`, `dormitory`                | 14–16m   | 4–5 storeys          |
| `commercial`                             | 18m      | 5–6 storeys          |
| `office`                                 | 22m      | 6–7 storeys          |
| `retail`, `supermarket`, `kiosk`         | 3–7m     | 1–2 tall storeys     |
| `warehouse`, `industrial`, `factory`     | 9m       | one tall volume      |
| `shed`, `hut`, `garage(s)`, `carport`    | ~2.5–3m  | outbuilding          |
| `civic`, `public`, `government`          | 14–16m   | landmark volume      |
| `school`                                 | 11m      | 2–3 generous storeys |
| `hospital`, `university`                 | 16–18m   | institutional slab   |
| `church`, `mosque`, `temple`             | 14–16m   | tall single volume   |
| `cathedral`                              | 30m      | landmark             |
| `hotel`                                  | 28m      | tall slab            |
| `tower`                                  | 40m      | point tower          |
| `yes` / unknown                          | by footprint: <90 m² → 5.5m (house-scale); <300 m² → 9m; <1,500 m² → 13m (mid-rise); larger → 9m (shed-scale) |

**Footprint sanity clamps (estimates only):** under 100 m² → capped at 10m; over
3,000 m² → floored at 8m (a big box is a shed, not a bungalow).

**Deterministic jitter:** estimated heights vary ±4%, seeded by the OSM way id, so
identical neighbours don't extrude to the same millimetre and re-runs are stable.
Real-data heights get no jitter.

## 3. Roof rule

Pitched roofs ONLY for clearly-residential houses — `house`, `detached`,
`semidetached_house`, `terrace`, `bungalow` — with a footprint between ~30 and 400 m²
(a 1,000 m² "terrace" way is a whole row; it stays flat rather than getting one absurd
mega-gable). The roof is a cheap hip: ridge along the footprint's principal axis at a
modest pitch (~30°, rise clamped 1.2–4m), built as a small mesh. Ridge tops out at the
building's (real or estimated) height; eaves sit below it. Everything else remains a
flat-topped extrusion. Houses go on `3D/BUILDINGS_houses`, the rest on
`3D/BUILDINGS_blocks` — the layer split makes the residential grain selectable, and the
old `BUILDINGS_extruded` name would have been a lie for roofed meshes.
