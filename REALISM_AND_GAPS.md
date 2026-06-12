# Realism Audit & Market-Gap Discovery — v5

Closing pass for the v5 "believable massing" build. Part A is an honest audit of what
still reads as unrealistic; Part B is the standing market-gap discovery, run over the
decision points of this build. (For what heights mean — and don't mean — see the honesty
caveat in MASSING_NOTES.md: estimates are legibility aids, never surveyed data.)

## A. Realism audit

### Fixed during the build

- **Coplanar nested greens.** A pitch or playground inside a park sat at exactly the same
  +50mm as its parent and z-fought. Nested green types (pitch, playground, garden,
  dog_park) now sit at +80mm. *(Fixed.)*
- **Roofed mega-ways.** A single `building=terrace` way covering a whole row (>400 m²)
  would have got one absurd mega-hip; it stays a flat extrusion. Same at the other end:
  sub-30 m² outbuildings stay flat. *(Guarded from the start.)*
- **Sliver and squat footprints.** Footprints under 1.6m wide, or buildings too low to
  keep a storey of wall under the roof, fall back to flat tops rather than producing
  knife-edge or all-roof geometry. *(Guarded.)*
- **Roads floating half a metre in the air.** The old ROAD_Z = +0.5m visibility hack is
  gone; carriageways now sit 50mm below local ground, under the pavements, as they do in
  a real street section. *(Fixed.)*

### Found, deferred, and why

1. **Terraces don't read as a row.** Each terraced house gets its own hip, ridge along
   its *own* long axis — which for a narrow-deep terrace plot runs front-to-back, while a
   real terrace ridge runs parallel to the street, continuous across party walls. Mid-rows
   get V-notches between neighbours. Fixing this needs adjacency detection (find shared
   party walls, merge ridge direction across the run) — a real algorithm, not a tweak.
   *Deferred; the varied-heights read still works, and detached/semi houses are correct.*
2. **Courtyards filled solid.** Buildings with internal courtyards are OSM multipolygon
   relations; we fetch ways only, so the outer ring extrudes solid. Visible on urban
   blocks (e.g. mansion blocks, colleges). Needs relation fetching + hole-aware geometry
   throughout. *Deferred — the single biggest correctness gap left in the massing.*
3. **Large greens on hilly ground can cut or float.** Green meshes drape only their
   boundary vertices; triangle interiors are planar, so a big park over a knoll can dip
   under the terrain (or bridge a hollow). Kerb-scale offset hides it on gentle ground.
   Proper fix is interior Steiner points sampled from the grid. *Deferred — cost/benefit
   poor at site scale; revisit if parks get bigger billing.*
4. **Pavements are centrelines, not surfaces.** A draped polyline a kerb up reads
   correctly at a glance but has no width; same for carriageways. Real ribbons need
   width data (OSM `width`/`lanes`) and offset geometry. *Deferred — see gap #2 below.*
5. **L-shaped house roofs are approximate.** The generalised hip projects eaves onto one
   straight ridge, so an L-plan gets a slightly odd (but watertight and plausible) roof
   over the notch rather than two intersecting gables. *Accepted — per-building roof
   topology is exactly the slow path the brief ruled out.*
6. **Estimates can fight local context.** An untagged `building=yes` of ~500 m² in a
   2-storey terraced street reads as 13m mid-rise. Footprint inference softens this but
   only context (neighbour heights) would fix it. *Deferred — context-aware smoothing is
   a v6-scale idea, noted below.*
7. **Churches are boxes.** A 15m extrusion is the right *mass* for a church but no spire/
   tower. OSM rarely splits the tower as its own way. *Accepted at massing scale.*

## B. Market-gap discovery (standing pass)

Method: for each decision this build made, list the alternatives not taken, check who
already serves them well (CadMapper, Blosm, Heron/Elk, Docofossor, QGIS, ArcGIS, Forma,
Rhino.Inside, OSM Buildings…), and keep only the genuinely unserved ones a designer
would actually want.

### Decisions examined and alternatives ruled OUT (already well served / weak demand)

- **Heights by type** vs *ML height regression* — academic, opaque, and still fake;
  no designer demand for a cleverer guess. Ruled out.
- **Simple hips** vs *full `roof:shape` reconstruction* — Blosm (Blender) and OSM
  Buildings already do this well where tags exist (rarely, outside Germany). Low
  marginal value in Rhino massing. Ruled out.
- **Terrain mesh + contours** vs *richer terrain tooling* — Docofossor, Heron, and QGIS
  serve power users; our one-click terrain is the right scope. Ruled out.
- **Sun/shadow studies** — Ladybug/Forma own this space and do it properly. Ruled out.
- **Lowest-point building seating** vs *cut/fill pads + retaining walls* — real but
  niche; nobody serves it AND few ask. Parked, not shortlisted.

### Candidate gaps, ranked (demand × unserved × free-tier feasibility)

1. **OSM attributes carried onto Rhino objects as user text.** Every object could carry
   its tags (name, building type, levels, highway class, OSM id) as Rhino UserText, so
   designers filter/select/schedule by attribute in Rhino/Grasshopper without a Heron/Elk
   pipeline. No one-click exporter does this; Heron/Elk require building a GH definition.
   Trivially feasible (rhino3dm supports attribute user strings). *Highest ratio of value
   to effort on this list.*
2. **Real road/pavement widths → ground surfaces.** Offset carriageway ribbons from OSM
   `width`/`lanes` (with sensible class defaults) and kerb-line pavements, draped. The 2D
   tools (CadMapper) give flat road areas; nobody gives draped street-section surfaces in
   a one-click 3DM. Feasible: centreline offset + the existing drape machinery.
3. **Real per-building heights from open LiDAR (regional).** England (DEFRA 1m DSM/DTM),
   much of Europe and some US states publish free, keyless LiDAR. Sampling DSM−DTM under
   each footprint gives *real* heights — the honest upgrade to this whole version, where
   coverage exists. CadMapper sells this idea city-by-city; nobody does it free/automatic
   with graceful fallback to type estimates. Feasibility: medium (per-region sources,
   tile formats, memory care) — the standout *data* gap.
4. **Courtyard-true buildings (multipolygon relations).** Half correctness fix, half
   feature: relation fetching + hole-aware extrusion. Blosm handles it in Blender; no
   one-click Rhino pipeline does. Feasibility: medium (Overpass relation query + hole
   triangulation/Brep).
5. **Context-aware height smoothing.** Untagged buildings inherit the height *character*
   of their tagged/estimated neighbours instead of a global lookup, so estimation errors
   stop fighting the street. Unserved anywhere; moderate demand (it's a polish gap);
   cheap-ish (spatial bucket + blend). Ranked last because it improves guesses rather
   than replacing them with data.

### Dead-ends checked so the list stays honest

FAR/setback modelling (zoning data isn't in OSM and is jurisdiction-soup), GIS exports
(QGIS exists), Minecraft-style full-detail city export (Blosm exists), photoreal texturing
(out of scope for massing). None shortlisted.

---

# v6 — Clean solid geometry (Breps over meshes)

Single focused change: geometry *type* upgraded to editable solids/surfaces where cheap
and useful, mesh kept (and reported) where not. Measured throughout with bench.py on the
three canonical sites; "peak WS" is Windows peak working set without tracemalloc — the
conservative proxy for what a 512MB container limit sees, including rhino3dm's C++ heap
that tracemalloc cannot observe.

## What converted (the honesty ledger)

- **Houses → closed solid Breps** (walls + hip roof + floor, watertight, boolean-able).
  Read-back verified `IsValid && IsSolid`. Concave footprints whose single-ridge hip
  self-overlaps fall back to the v6 welded display mesh and are counted per reason:
  Shoreditch 1453/1678 solid (213 concave + 12 not-closed), Clifton 342/499 (31% — 
  suburban L-plans). rhino3dm exposes no `SetTolerancesBoxesAndFlags`, so unset Brep edge
  tolerances (which make Rhino reject the object) are zeroed by patching the
  ON_UNSET_VALUE sentinel in the serialized archive — works, but couples us to the
  pinned rhino3dm 8.17 serialization (re-verify on any upgrade).
- **Blocks**: were already capped solid extrusions; read-back now *proves* it
  (Shoreditch 11344/11344 solid).
- **Greens → planar trimmed Brep faces** where boundary ground varies ≤0.75m (within DEM
  noise); real slopes keep the honest draped mesh. Shoreditch 842 planar/135 draped;
  Clifton 51/73 — the threshold visibly working.
- **Roads/pavements: left as draped polyline linework, deliberately.** They are already
  light, CAD-correct curves; converting centrelines to "surfaces" without real widths
  would fake data (that's v5 gap #2, a *feature*, not a type conversion).
- **Terrain → interpolating NURBS surface** (degree-3 collocation solve at the Greville
  abscissae; passes through every grid node exactly — a naive CVs=nodes fit smooths
  relief by metres and never once qualified). Honesty threshold is *ringing*: if the
  cubic escapes the [min,max] band of any cell's four corner samples by >1.0m it would
  be inventing ground, and the mesh stays. Clifton rings 0.30m and Shoreditch 0.74m →
  surfaces; Dubai Marina rings 1.69m (quay/water steps in the urban DEM) → mesh, reported.

## The memory discipline finding (why this build was isolated)

A house Brep archives at ~50KB vs ~1.2KB as mesh (40×); 500-house spike: +43.6MB RSS as
Breps vs +4.2MB as meshes. Un-governed, Shoreditch peaked **587.8MB** (v5 baseline:
**474.6MB** — already 93% of the 512MB tier). The live v5 deployment generates Shoreditch
successfully (HTTP 200, 188s), so v5's Linux peak fits the tier; shipping +113MB of Brep
transient would have OOM'd the exact case that works in production today.

**Resolution: a measured memory governor** (`house_solid_budget`). Baseline peak fits
~160MB + ~25KB/building (fitted to the three sites); a house solid costs ~90KB; solids
are converted until estimated peak reaches 440MB on this metric (at-or-below v5's
proven-deployable level), then houses keep the display mesh and the stats report
`house_budget_skips` + the budget — never a silent downgrade. Budgets: Dubai 2699
(uses 20), Clifton 1813 (uses 342), Shoreditch **0** (all 1678 houses stay mesh).
Measured governed Shoreditch: **485.1MB** — within ~10MB of the v5 baseline (the
remainder is planar greens + the NURBS terrain + a footpath-richer Overpass response,
not Breps).

| site | peak WS v5 → v6 governed | tracemalloc | time | file v5 → v6 |
|---|---|---|---|---|
| Dubai Marina | 194.6 → 196.3 MB | 34.2 MB | ~10–24s | 5.25 → 6.65 MB |
| Shoreditch | 474.6 → 485.1 MB (587.8 un-governed) | ~192 MB | ~90–243s, Overpass variance | 29.0 → 35.5 MB |
| Clifton | 206.8 → 224.9 MB | 35.9 MB | ~29s | 7.3 → 18.0 MB |

(Time deltas are dominated by Overpass mirror variance — one identical-code Shoreditch
run took 243s, another 143s. One v5 bench run also returned ~4.7k fewer footpath ways
than every other run: same buildings, different mirror state. File-size deltas above
quote matched-data runs.)

## B. Conjecture-refutation cycle (standing method, first full pass)

### 1. Problems in the current theory

- **P1.** The 512MB free tier is now the *binding constraint on geometry quality*: the
  stress site ran at 93% of ceiling before v6 even started.
- **P2.** 13–31% of houses cannot get a clean hip solid (concave footprints).
- **P3.** Big-site users get the least of v6 — Shoreditch's budget is 0 solids. The most
  impressive exports are the most degraded. (This is also the monetisation signal.)
- **P4.** Urban DEM artifacts (Dubai) deny terrain surfaces — a data problem, not an
  algorithm problem.
- **P5.** Objects still carry no attributes (v5 gap #1, unshipped) — now *more* valuable,
  since editable solids invite filtering/scheduling workflows.
- **P6.** Files are ~2.5× bigger where solids land; 72MB over mobile is real friction.
- Still open from v5, re-confirmed this cycle: terrace-row ridges, courtyard
  multipolygons, street widths, LiDAR heights, context height smoothing.

### 2–3. Conjectures, criticism, and what was refuted

- **C1 (P1): stream objects to disk during the build.** REFUTED: rhino3dm's
  `File3dm.Write` is monolithic; there is no incremental archive API.
- **C2 (P1): leaner Breps via the untrimmed conversion mode.** REFUTED by measurement:
  untrimmed archives are *bigger* (57KB vs 51KB per house), and rhino3dm lacks
  JoinBreps/manual-topology tools to hand-build anything leaner.
- **C3 (P1/P3): a measured memory governor.** SURVIVED — shipped this version (above).
- **C4 (P1/P3): lift the ceiling with money.** SURVIVED as the monetisation route:
  Render's $7/mo tier is 2GB — the governor would never bind on any realistic site.
  CadMapper charges per-export for less than this tool gives free. Falsifiable by
  demand: a Pro waitlist signal, before any infra is built.
- **C5 (P1): job queue + disk-backed builds.** Parked, not refuted — real but heavy;
  only worth it if C4 shows demand.
- **C6 (P2): straight-skeleton roofs for concave plans.** SURVIVED to the v7 shortlist.
  Falsifiable: a pure-python skeleton spike must produce watertight solids on the 213
  Shoreditch concave failures within budget, or it dies.
- **C7 (P2): decompose concave plans into convex pieces, hip each.** REFUTED on sight:
  multi-ridge nonsense across single dwellings — worse than the honest mesh.
- **C8 (P4): swap DEM to Copernicus GLO-30.** REFUTED: it is also a DSM (buildings baked
  in — same artifact class) and coarser (30m) than terrarium z13 here. 
- **C9 (P4): pre-smooth the DEM until the surface qualifies.** REFUTED on honesty
  grounds: filtering real data to pass our own honesty threshold is circular. The mesh
  *is* the honest answer on stepped ground.

### 4. Replacement — ranked shortlist for v7

1. **OSM tags as Rhino UserText** (carried v5 #1; trivial, unserved, compounds with
   editable solids; the stickiness feature). Cost: near-zero memory.
2. **Real per-building heights from free LiDAR** (England DEFRA DSM−DTM first; graceful
   fallback to estimates). The standout data gap; the honest upgrade to every height in
   the model. Medium feasibility; clear Pro-tier feature.
3. **Straight-skeleton house roofs** — kills the 13–31% fallback, completes v6's promise.
4. **Courtyard-true buildings** (multipolygon relations; biggest remaining correctness gap).
5. **Street-width ground surfaces** (now cheaper: the planar/drape machinery exists).
6. **Free/Pro split exploration** — the governor line *is* the natural paywall line
   ("full solids + full areas = Pro").

### 5. New problems the survivors will create

- The governor makes geometry type depend on site size — deterministic, but users will
  eventually ask why one export has Brep houses and another doesn't; needs UI surfacing.
- The tolerance patch and the pinned rhino3dm version must be re-verified together on
  every dependency bump.
- Interpolating terrain: the CV hull slightly exceeds the surface (ZoomExtents margin);
  contours still derive from the raw grid (honest, but two representations coexist).
- LiDAR heights (if built) add per-region data plumbing and large tile downloads to a
  512MB box — it may *require* the C4/C5 path first.
- UserText adds per-object strings: measure before assuming it's free at 80k objects.

### Monetisation flags (standing requirement)

- **Chargeable now precedent**: CadMapper bills per-tile for static extracts; this tool's
  free tier already exceeds that on alignment + terrain + editability.
- **The governor is the paywall**: full-solid mega-sites need the 2GB tier ($7/mo) — a
  Pro plan funds itself at a handful of subscribers.
- **LiDAR real heights** is the feature people demonstrably pay for (CadMapper sells
  "real building heights" city packs).
- **UserText/attributes**: not directly chargeable, but the retention feature that makes
  a Pro plan defensible.
- Lovely but unmonetisable: straight-skeleton roofs, courtyards — correctness work that
  earns trust, not revenue.
