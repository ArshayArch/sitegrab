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

---

# v7 — Site-analysis framework + Sun Path (the pivot begins)

Real user feedback is now data: *the geometry is useful but not pay-worthy; site
analysis is.* v7 is the first step of that pivot — a pluggable analysis framework
(ANALYSIS_FRAMEWORK.md) with **Sun Path** as its first module. Geometry is
untouched; this is built alongside it.

## What shipped (the honesty ledger)

- **The framework** (`analysis/`): a four-type contract (`SiteContext`,
  `AnalysisSpec`, `Param`, `AnalysisResult`) + registry + a single `runner` that
  is the only bridge to the existing pipeline. A module declares its data source,
  what it `requires` and `outputs`; the runner resolves the site with the
  *existing* `resolve_area`/`get_transformer` (same caps, same UTM zone -> automatic
  alignment) and lazily attaches only the declared data sources. DATA and
  REPRESENTATION are kept in separate files per module.
- **Sun Path data** (`solar.py`): sun azimuth/altitude **calculated** with
  `astral` — free, keyless, no API/billing. Verified against the exact identity
  `noon_alt = 90 - |lat - decl|` across both hemispheres and the equator
  (Greenwich 62.0/38.5/15.1 deg vs predicted 61.96/38.52/15.08). Hemisphere-aware
  summer/winter labels; hour marks in local solar time.
- **`astral`, not `pvlib`** (the deploy-risk dependency call): `pvlib` pulls
  `pandas`+`scipy` (>100 MB image, tens of MB resident); `astral` is pure-Python
  with one tiny dep (`tzdata`). The precision difference is invisible at a 250 m
  arc radius (0.1 deg ~ sub-cm). The 512 MB tier is the binding constraint, so we
  took the dep that cannot break the deploy; its import + a known sun altitude is
  asserted in the Docker build.
- **Sun Path geometry** (`sunpath.py`): 3D sun arcs + labelled hourly points for
  the three key dates, on granular `SUN/...` layers, in the same UTM as the model;
  DXF carries the plan projection. Verified on Clifton: summer arc peak 1287 m >
  winter 381 m (lower) and summer span wider (longer day).
- **True north, established and made visible**: `astral` gives true azimuth; the
  model is in UTM grid. We **measure** the meridian convergence (project a point
  due north through the same transformer) and rotate every azimuth into the grid
  by it, so arcs/points/shadows are physically consistent with the buildings.
  True north is drawn on `SUN/north` for visual confirmation (-0.30 deg at Clifton).
- **Cast shadows** (`shadows.py`): flat-ground projection (convex hull of the
  footprint and its roof-projection) is the shipped baseline — exact for
  rectangular plans, a documented slight over-estimate for concave ones; cast on
  both solstices at the chosen times. **Terrain-draped shadows succeeded** as the
  stretch (separate `shadow_draped_*` layers) but are a *drape* of the flat
  outline onto the DEM, not a true ray-vs-terrain intersection — labelled as such;
  flat is the fallback when no grid is present.
- **Honesty surfaced to the user**: notes ride back on the `X-SiteGrab-Notes`
  header and render in the UI — sun precise, shadows inherit the type-driven
  height estimate (never a daylight/rights-of-light tool), the free-tier caster
  cap and any skipped low-sun positions are reported, never silent.

## Memory (free-tier safety)

The analysis build is lighter than the v6 combined build: it fetches buildings
only (lean query), no terrain surface/solids/detailed linework. Measured peak
working set (Windows high-water, the conservative proxy for the container limit):

| site | buildings | casters (cap 6000) | shadows | peak WS | file |
|---|---|---|---|---|---|
| Clifton (draped on) | 4671 | 4671 (uncapped) | 36k (flat+draped) | 360 MB | 15.0 MB |
| Shoreditch (flat) | 13023 | 6000 (7023 capped) | 36k | 344 MB | 15.5 MB |

Both well under 512 MB. The cap is set by **file size**, not memory — at 6000
casters there is large headroom, so typical neighbourhood sites cast fully and
only a true megasite caps (and reports it). Uncapped full shadows are a clean
Pro-tier feature on the 2 GB tier.

## B. Conjecture-refutation cycle (standing method)

### 1. Problems in the current theory

- **P1 (the product).** "Wouldn't pay for geometry" makes geometry table stakes;
  value — and the paywall — must live in analysis. One indicative sun study will
  not convert on its own: Ladybug/Forma own rigorous solar and are free to
  students.
- **P2 (Sun Path's honesty ceiling).** Shadow *length* scales with height, and
  most OSM heights are estimates — so Sun Path is an early-design/communication
  aid, never an engineering daylight tool. Its paid value can only be alignment +
  one-click speed + (later) a credible data basis, not rigour we don't have.
- **P3 (big sites get least, again).** The shadow caster cap means a megasite's
  shadows are partial (Shoreditch: 7023 of 13023 omitted) — the same v6 pattern
  (the most impressive exports are the most degraded). Reported, but real.
- **P4 (solar vs civil time).** Hour marks are in solar time (correct, keyless)
  but can confuse a user expecting clock time; mapping to civil time needs a
  timezone database we deliberately avoid.
- **P5 (the framework is unproven).** Its shape is a *conjecture* until a second
  module plugs in without touching the runner/endpoint/frontend.
- **P6 (draped shadows are a drape).** Not a true ray-vs-terrain cast — a known
  approximation on slopes.
- **P7.** The equinox gets arcs/points but no shadows (solstices bound the range).
- **Still open from v6**, re-confirmed: OSM UserText attributes (unshipped — now
  the strongest *retention* lever), real LiDAR heights (unshipped — now also the
  honesty fix for shadows), terrace ridges, courtyards, street widths.

### 2-3. Conjectures, criticism, and what was refuted

Conjectures about (a) the next analyses and (b) what converts a free user to a
paying one. Passing a test is weak; failing is decisive.

- **C1 — Real LiDAR heights (DEFRA/regional DSM-DTM).** SURVIVES, and is now
  *doubly* motivated: it makes every model height real AND turns Sun Path's
  shadows from indicative into trustworthy — the link v6 didn't have. It is the
  feature CadMapper demonstrably *sells*. Falsifier: regional coverage too sparse,
  or tiles too heavy for 512 MB -> would force the paid 2 GB tier first.
- **C2 — Sunlight-hours ground heatmap** (how many daylight hours each ground
  point receives, with building occlusion). SURVIVES to the shortlist: this is the
  analysis students actually put in a crit/portfolio. Falsifier: it is
  O(grid x sun-times x buildings) with an occlusion test — a feasibility spike must
  show it stays inside the free tier, or it is capped to Pro/the 2 GB tier.
- **C3 — Wind rose / exposure from open climate data.** PROVISIONAL -> leaning
  REFUTED on free-data grounds: station data (Meteostat) is sparse and not
  site-granular; ERA5 (CDS) needs an API key, breaking the keyless rule. Parked
  unless a genuinely free, keyless, site-relevant source appears.
- **C4 — Viewshed / visibility** from a point over terrain+buildings. SURVIVES:
  feasible with data we already fetch; real demand in landscape/urban. Weaker
  monetisation alone than C1/C2.
- **C5 — Flood/water analysis.** REFUTED on free-data grounds (hydrology isn't in
  OSM/DEM reliably; jurisdiction-soup), same class as v5's FAR/zoning dead-end.
- **C6 — Noise mapping.** REFUTED: needs traffic-volume/source models with no free
  keyless source at usable quality.
- **C7 — Free geometry, paid analysis (the split).** SURVIVES as the monetisation
  spine: students pay for analysis *depth/quantity*, not the first taste.
  Falsifier: a paid waitlist / upgrade test — if nobody upgrades for fuller
  shadows + heatmap + real heights, the split is refuted and the paywall must move.
- **C8 — pvlib for precision.** REFUTED for this product (memory cost; precision
  invisible at massing scale). Recorded so it is not re-litigated.

### 4. Replacement — ranked shortlist for v8

Ranked by (real demand x how unserved-for-free x free-tier feasibility x
monetisation potential):

1. **Real LiDAR heights (C1).** Compounds geometry *and* makes Sun Path honest;
   the proven paid feature. Feasibility medium (per-region tiles, memory). **This
   should be v8** — it is the one upgrade that simultaneously raises the free
   product's trust and justifies a Pro tier.
2. **Sunlight-hours heatmap (C2).** The portfolio analysis; v9, or v8 if a
   feasibility spike clears it and LiDAR's data plumbing slips.
3. **Viewshed (C4).** Feasible now, good demand, modest standalone monetisation.
4. **OSM UserText attributes** (carried from v5/v6). Retention, not revenue;
   cheap; ship alongside whatever wins.

### 5. New problems the survivors will create

- **LiDAR** adds per-region data plumbing and large tiles to a 512 MB box — likely
  *requires* the paid 2 GB tier (the v6 C4/C5 infra path) and a per-building "real
  vs estimated" flag so shadows can declare which heights they trust.
- **Sunlight heatmap** needs a new output type (a ground grid/mesh of values) the
  representation layer must support, and the same governor discipline as house
  solids.
- **The free/Pro split** introduces auth, billing and entitlement — the first
  non-geometry infrastructure; both the v6 governor line and the analysis-depth
  line become paywalls to maintain.
- **The framework grows**: v8's LiDAR almost certainly needs a data source
  `SiteContext` lacks (DSM/DTM tiles) — extending the contract against a real
  second module, which is exactly the intended trigger (P5's test), but it means
  `SiteContext` is no longer minimal.

### Monetisation (sharpened by the real "wouldn't pay for geometry" signal)

- **Free** — all geometry (combined/3D/DXF/terrain) **plus** Sun Path at base
  depth (arcs + hourly points + one solstice's flat shadows). Enough to prove the
  analysis is real and aligned.
- **Pro** — full shadow sets (all times/dates, draped, uncapped on the 2 GB tier),
  the sunlight-hours heatmap, **real LiDAR heights**, and multiple/stacked
  analyses. The wedge is *the analysis that goes in the crit*: depth + a credible
  data basis, not the first taste.
- **The split is honest only if we don't oversell**: Sun Path is not a substitute
  for Ladybug/Forma engineering analysis; its paid value is alignment, one-click
  speed, and (with LiDAR) real heights — stated plainly in the notes.
- **Precedent holds**: CadMapper bills per-tile for static extracts and *sells*
  real building heights; SiteGrab's free tier already exceeds the former, and C1
  targets the latter as the anchor Pro feature.

---

# v8 — Wind (the framework's second module; the no-rework proof)

The v7 shortlist ranked **LiDAR heights** first. v8 deliberately took **Wind**
instead, and the reasoning is the honest part of this version. v7's whole reason
to exist was the *conjecture* that the analysis framework would take a second
module without rework (P5). LiDAR is the worst possible first test of that
conjecture: it almost certainly needs a data source `SiteContext` lacks
(DSM/DTM tiles) **and** likely the paid 2 GB tier first — so a LiDAR-first v8
would have changed the contract and the infra in the same step, proving nothing
about the framework's shape. Wind is the clean test: it needs only data the
contract already carries (`location` + `buildings`), stays inside the free tier,
and so isolates the one question v7 asked. LiDAR remains the standout *data* gap
and the anchor Pro feature; it is now the v9 candidate, to be built once the
framework is known-good.

## What shipped (the honesty ledger)

- **The framework's P5 test PASSED — decisively.** Wind plugged in by touching
  `analysis/__init__.py` (one `import`/register line) plus two new module files
  (`wind_data.py`, `wind.py`). `runner.py`, `framework.py`, **and `main.py` were
  not touched at all** (verified against the v8 commit range). The only frontend
  change was 20 lines of *per-analysis progress copy* in `index.html`, with a
  generic fallback — the menu logic, the param controls and the format toggles
  stayed fully spec-driven, so a third analysis still needs zero menu work. The
  contract did **not** have to grow: Wind needed no `SiteContext` field Sun Path
  hadn't already justified. The v7 conjecture (the framework is the right shape)
  survived its first real falsification attempt.
- **C3 (wind) un-refuted on its own terms.** v7 leaned C3 → REFUTED because
  "station data is sparse and ERA5 (CDS) needs an API key, breaking the keyless
  rule." That refutation rested on *one assumed access path*. **Open-Meteo's
  Historical Weather archive serves the same ERA5 reanalysis, keyless and free**,
  per lat/long, as hourly `wind_speed_10m` + `wind_direction_10m`. The data
  objection was about the gateway, not the data — so C3 returns, honestly, as a
  *measured climatology* (not the live/forecast wind the v7 note feared).
- **Wind DATA** (`wind_data.py`): a MEASURED 16-sector rose aggregated from 3
  complete calendar years (last whole years, to dodge the archive's few-day lag)
  — per-sector frequency, mean and max speed, with sub-`0.5 m/s` hours counted as
  calm and binned to no direction. Stdlib `urllib` only, **no new dependency**.
  Unit-tested on sector boundaries, frequency/calm aggregation, null-skipping.
  Verified live: Clifton 26 304 hourly records (2023–2025), prevailing **WSW
  18.3 %**, mean 5.2 m/s, calm 0.7 %; Shoreditch prevailing **SW 16.0 %**. A
  latitude-band global-circulation fallback (clearly flagged NOT site-specific)
  keeps the diagram honest if the API is unreachable, rather than failing blank.
- **Wind REPRESENTATION** (`wind.py`): an INDICATIVE diagram on `WIND/...` layers
  in the same UTM as the model — a prevailing-direction arrow field that **stops
  at building facades** (blockage reads at a glance), secondary directions each
  on their own hidden layer (toggle like the shadow hours), a corner 16-sector
  rose, and `WIND/channels` markers where a narrow open corridor (≤30 m) between
  buildings lines up with the prevailing wind (bolder = narrower). Same true-north
  basis as Sun Path: meridian convergence measured through the transformer and
  every bearing rotated into the grid (−0.30° at Clifton).
- **Honesty surfaced, loudly.** Both the module headers and the user notes state
  it in capitals: this is a DIAGRAM, **NOT CFD** — no airflow, speed-up, pressure
  or turbulence is computed; channels are a *geometric suggestion* from prevailing
  direction + gap width, not a flow solve. The rose is a multi-year average, not
  live or forecast. The obstacle cap and any fallback are reported, never silent.
- **Shadows tidy-up rode along** (Phase 1): cast shadows are now one-per-layer and
  hidden by default, so the analysis layers behave consistently across modules.

## Memory (free-tier safety)

Measured peak working set (Windows high-water via `psutil.peak_wset` — the same
conservative proxy bench.py uses, includes rhino3dm's C++ heap):

| site | obstacles (cap 4000) | channels | peak WS | 3dm |
|---|---|---|---|---|
| Clifton, Bristol | 4000 (+12 042 capped) | 25 | **174.8 MB** | 55 KB |
| Shoreditch, London | 4000 (+9 024 capped) | 39 | **209.3 MB** | 66 KB |

Both far under 512 MB — lighter than Sun Path (no shadow explosion) and a
fraction of the geometry build. The obstacle cap is set for *legibility and
file size*, not memory: arrows/channels off the 4000 largest footprints already
saturate a readable plan, and the skip count is reported. Uncapped obstacles are
a clean Pro-tier item on the 2 GB tier. (One Clifton run took 118.9 s wall — that
was Overpass building-fetch mirror variance, not the wind module; a second run
on the same code was 13 s.)

## B. Conjecture-refutation cycle (standing method)

### 1. Problems in the current theory

- **P1 (the analysis-vs-CFD honesty line).** Wind's value is alignment + a
  measured prevailing direction + a legible blockage/channel read. The danger is a
  user reading the channel markers as a flow result. The notes shout NOT CFD, but
  the prettier the arrows, the stronger the over-read — a standing communication
  risk, not a code bug.
- **P2 (channels are geometry, not flow).** A 30 m corridor aligned with the
  prevailing wind is *flagged*; whether it actually funnels depends on
  upstream/downstream geometry, height and approach angle the diagram ignores.
  Honest as a "look here," dishonest if read as "wind speeds up here."
- **P3 (the rose is a point climatology).** ERA5 at ~9–25 km resolution is a
  regional rose dropped on the site; real local wind is modified by exactly the
  buildings and terrain we draw. The diagram pairs a *regional* direction with
  *local* geometry — correct as indicative, not as microclimate.
- **P4 (the obstacle cap, again the big-site pattern).** Megasites cap at 4000
  obstacles for arrows/channels (Clifton dropped 12 042) — same "most impressive
  exports are the most degraded" shape as v6 solids and v7 shadows. Reported, real.
- **P5 (framework now proven — so what's the next stress?).** P5 passed for a
  module that needed no new data source. The *unproven* case is now LiDAR: a
  module that genuinely needs a `SiteContext` field the contract lacks. The
  framework is validated for additive modules, untested for contract growth.

### 2–3. Conjectures and criticism

- **C1 — LiDAR real heights (still the standout data gap).** SURVIVES, now the
  clear v9. Wind proved the framework holds for additive modules; LiDAR is the
  intended test of *contract growth* (DSM/DTM tiles SiteContext lacks) and the
  proven paid feature. Falsifier unchanged: regional coverage too sparse or tiles
  too heavy for 512 MB → forces the 2 GB tier first.
- **C2 — Sunlight-hours heatmap.** SURVIVES to the shortlist (the portfolio
  analysis). Still needs the new ground-grid output type and a feasibility spike
  against the free tier.
- **C3 — Wind.** SHIPPED. Recorded so the "ERA5 needs a key" refutation is not
  re-litigated: Open-Meteo's archive serves it keyless.
- **C9 — Pressure/funnelling *score* per channel** (a relative 0–1 funnel index
  from gap width + alignment + flanking height). PROVISIONAL → leaning REFUTED on
  honesty grounds: any single number invites exactly the CFD over-read P1 warns
  about, with no flow physics behind it. A measured speed-up needs a solver we
  won't ship on the free tier. Parked unless it can be framed as pure geometry
  without implying a wind speed.
- **C10 — Civil-time wind (diurnal rose: day vs night prevailing).** SURVIVES as a
  cheap Pro depth item — the archive already returns hourly data, so a
  day/night or seasonal split is free of new data and genuinely useful for
  ventilation/comfort framing. Falsifier: it must stay legible (two roses, not
  twelve) or it is clutter.

### 4. Replacement — ranked shortlist for v9

1. **LiDAR real heights (C1).** Now correctly sequenced: the framework is proven
   for additive modules, so v9 is the deliberate test of contract growth + the
   anchor Pro feature + the honesty fix for every shadow length.
2. **Sunlight-hours heatmap (C2).** The crit/portfolio analysis; needs the new
   grid output and a free-tier spike.
3. **Diurnal/seasonal wind depth (C10).** Cheap Pro depth on data already fetched.
4. **OSM UserText attributes** (carried from v5/v6, still unshipped). Retention,
   not revenue; ship alongside whatever wins.

### 5. New problems the survivors will create

- **LiDAR** is the first module that will *grow the contract* — `SiteContext`
  gains a DSM/DTM source and stops being minimal, and it likely needs the 2 GB
  tier (auth/billing/entitlement infra) before it fits. The clean v8 result does
  not transfer automatically; v9 is where "no rework" actually gets tested.
- **Per-analysis progress copy** (the one v8 frontend change) will accrete one
  entry per module; harmless with the generic fallback, but it is the first place
  the spec does *not* fully drive the UI. If it grows, move the copy into the spec.
- **Multiple wind depths** (C10) plus Sun Path's depths start to need a real
  free/Pro entitlement boundary — the analysis-depth paywall the v7 cycle named is
  now backed by two modules' worth of depth to gate.

### Monetisation (unchanged spine, now with a second free taste)

- **Free** — all geometry, Sun Path at base depth, **and** the Wind diagram
  (prevailing arrows + rose + channels). Two real, aligned analyses prove the
  pitch without giving away depth.
- **Pro** — uncapped obstacles/shadows, the sunlight heatmap, diurnal/seasonal
  wind, real LiDAR heights, and stacked/multiple analyses. The wedge is still
  *the analysis that goes in the crit*: depth + a credible data basis.
- **The honesty guardrail holds the line**: Wind is explicitly NOT CFD and Sun
  Path is explicitly NOT a daylight tool — the paid value is alignment, one-click
  speed, breadth, and (with LiDAR) real heights, never rigour we don't have.


# v9 — Real LiDAR heights (both v8 fears refuted)

v8 ranked LiDAR first for v9 and made two confident predictions about it (C1/P5):
that it would (a) **grow the `SiteContext` contract** with a DSM/DTM field, and
(b) **force the paid 2 GB tier first**. Building it refuted *both* — and the
refutations are the honest core of this version.

- **"LiDAR grows the analysis contract" — REFUTED, by re-categorising it.** LiDAR
  heights are not an *analysis* of the site; they are better *input geometry*. So
  they belong in the massing build, not the analysis framework — and v9 wires them
  into `build_combined`'s 3D pass, touching neither `framework.py`, `runner.py`,
  nor `SiteContext`. v8's whole "contract growth" framing mis-classified the
  feature. The framework was never the right home, so it never had to grow. (The
  cost: the standalone lean `.3dm`/`.dxf` keep v5 estimates — see P4.)
- **"LiDAR needs the 2 GB tier" — REFUTED, by a memory governor.** Instead of
  paid infra, the same cap-for-the-free-tier pattern from v6 solids / v7 shadows /
  v8 obstacles: `lidar_budget_px` sizes the raster from the building count and
  *skips* it on megasites, so the augmented build stays under 512 MB. LiDAR ships
  **free** on the existing tier; uncapped LiDAR on megasites is the clean Pro item.

## What shipped (the honesty ledger)

- **Per-building height resolution with provenance.** `resolve_height` (build_rhino)
  resolves each footprint as OSM tag > sanity-passed LiDAR > type estimate, and
  returns *which*. A real OSM tag still wins unconditionally (v5 never regresses).
- **The sanity gate is the point.** `_lidar_is_sane` rejects a LiDAR value that is
  too short (<2 m: demolished / mid-construction at fly-time / ground noise), absurd
  (>280 m), too slender (h/√area > 10), or tall-on-tiny (>25 m on <50 m²: a tree or
  aerial spike, not a tower). The design is to TRUST LiDAR when clearly good and
  REJECT it when obviously wrong — never to believe a raster blindly.
- **Option C — you can SEE the provenance.** Buildings split across four layers
  (`3D/BUILDINGS_{houses,blocks}_{real,estimated}`); estimated layers take a muted
  yellowed tint. Counts ride the `X-SiteGrab-Heights` header; the UI note states,
  per regime, *why* any height fell back — LiDAR ran / skipped by the governor on a
  large site / no coverage (England-only or service unreachable). Honest by the
  numbers AND about the gaps, not just the numbers.
- **Accuracy MEASURED, not asserted.** `validate_lidar_vs_osm.py` compares the
  LiDAR height to the *independent* OSM tag on every building carrying one: median
  |diff| **2.12 m** (central Shoreditch, 497 buildings), **2.30 m** (Clifton, 199),
  ~80 % within 5 m. Two independent sources agreeing to ~2 m is the evidence the
  heights are real, not decorative. (A draft "593 buildings / 2.1 m" claim written
  before the measurement was caught and replaced with the reproducible figures —
  the honesty discipline applied to our own documentation.)
- **Memory halved at source.** `fetch_lidar` holds ONE `DSM−DTM` array (NaN where
  either source was nodata), not both rasters, and frees it before the heavy
  geometry/write stages. No new dependency: float32 GeoTIFF via Pillow over the
  keyless EA WCS, the numpy/Pillow/pyproj wheels already in the image.
- **Build guard.** The Dockerfile now fails early if `fetch_lidar` won't import or
  the governor stops enforcing its invariant (full raster on a neighbourhood site,
  SKIP on a megasite) — the v9 analog of the Wind registry guard.

## Memory (free-tier safety)

Measured combined-build peak working set (`measure_ws.py`, `psutil.peak_wset`, no
tracemalloc — the prod-like high-water mark):

| site | buildings | OSM / LiDAR / est | LiDAR | peak WS |
|---|---|---|---|---|
| Clifton, Bristol | 4 670 | 199 / 4 431 / 40 | full 1 m raster | **235.9 MB** |
| Dubai Marina (non-England) | 1 482 | 415 / 0 / 1 067 | none (no coverage) | **180.5 MB** |
| Shoreditch, London | 13 023 | 4 275 / 0 / 8 749 | **governor-skipped** | **487.4 MB** |

The governor earns its place on the last row: un-governed, Shoreditch's LiDAR
raster stacked the build to **599.5 MB — over the 512 MB ceiling**; skipping it
holds 487.4 MB and keeps the 4 275 OSM-tagged heights. Covered mid-size England
(Clifton) is the win: **99 %** of buildings get a real height, the estimate is the
rare fallback (40 of 4 670), all inside the free tier.

## B. Conjecture-refutation cycle (standing method)

### 1. Problems in the current theory

- **P1 (LiDAR is a timestamp, not "now").** A height is from the fly-over date;
  a building demolished, re-clad taller, or mid-construction since reads wrong. The
  sanity gate catches gross cases (a 1 m "building"), not a real 20 m block that
  became 30 m last year. Honest as "surveyed at capture," not "current."
- **P2 (the per-building value has a tail).** 2.1–2.3 m *median* agreement is
  massing-grade, but the 90th percentile is 5.5–6.2 m. A user reading one
  building's height off the model can be 5 m+ out. Correct in aggregate, only
  indicative per building — the same analysis-not-truth line Wind/Sun Path hold.
- **P3 (the governor degrades the most-wanted sites).** LiDAR is skipped on exactly
  the megasites people demo first — central London gets ZERO LiDAR. Same "most
  impressive exports are the most degraded" shape as v6/v7/v8. Reported in the
  note, real, and the headline city is the worst case.
- **P4 (provenance is combined-only — a cross-output inconsistency).** The split
  and LiDAR heights apply to the combined model; the standalone `.3dm`/`.dxf` keep
  v5 estimates (LiDAR fetched once, for the headline output, to respect the tier).
  A user downloading only the lean `.3dm` gets neither real heights nor the
  provenance layers, with no in-file signal. Documented, but a silent gap.
- **P5 (coverage is England-only).** The "real heights" pitch is regional; most of
  the world still gets estimates. The per-region plumbing for Europe/US-state DSMs
  (each its own CRS, coverage mask, access path) is unbuilt — the data gap is
  *narrowed*, not closed.

### 2–3. Conjectures and criticism

- **C1 — LiDAR real heights.** SHIPPED (England, combined model, free-tier). The v7
  ranking-#1 data gap is closed for England; recorded so the "needs the 2 GB tier /
  grows the contract" framing is not re-litigated — a memory governor and correct
  categorisation refuted both.
- **C2 — Sunlight-hours heatmap.** SURVIVES, now the clear v10 and *compounded* by
  v9: shadow lengths are only honest where heights are real, and LiDAR makes them
  real across covered England. Still needs the new ground-grid output type and a
  free-tier spike. The natural next build.
- **C3 — Straight-skeleton house roofs** (carried from v6). SURVIVES. LiDAR gives a
  single height per footprint, not a roof form, so the 13–31 % concave-house mesh
  fallback is untouched; this still completes v6's promise. Independent of LiDAR.
- **C4 — Multi-region LiDAR** (Europe / US-state DSMs). NEW, from P5. Extends the
  real-heights win beyond England. Falsifier: per-source plumbing (CRS, coverage,
  access) may cost more than each region's user base returns — sequence by demand.
- **C5 — LiDAR for the standalone outputs** (from P4). PROVISIONAL: re-fetching the
  raster for the lean `.3dm`/`.dxf` doubles the data cost for outputs the combined
  model already supersedes. Leaning REFUTED unless users actually consume the lean
  `.3dm` as a primary — more likely just signal the estimate-only status in-file.
- **C10 — Diurnal/seasonal wind depth** (carried from v8). SURVIVES as cheap Pro
  depth on data already fetched; unbuilt, not urgent.

### 4. Replacement — ranked shortlist for v10

1. **Sunlight-hours heatmap (C2).** The crit/portfolio analysis, now compounded by
   real LiDAR heights — the honest shadow story finally pays off. Needs the new
   grid output type + a free-tier feasibility spike (its cost is per-cell, not
   per-building).
2. **Straight-skeleton house roofs (C3).** Kills the concave-house mesh fallback,
   completes v6's promise; pure geometry, no new data.
3. **Multi-region LiDAR (C4).** Widens the data win past England; sequence by where
   demand actually is.
4. **Diurnal/seasonal wind depth (C10), OSM UserText** — retention/depth carries;
   ship alongside whatever wins.

### 5. New problems the survivors will create

- **The sunlight heatmap will *depend* on LiDAR coverage.** Its honesty is now
  coupled to v9: on a governor-skipped megasite the heatmap rests on estimated
  heights and must say so, or it over-claims exactly where it looks most impressive.
  Two features now share one honesty boundary.
- **Multi-region LiDAR is the per-region plumbing v7/v8 kept deferring.** Each
  source multiplies the coverage-mask / CRS / access surface; the keyless-England
  simplicity will not generalise for free.
- **The Pro entitlement boundary is overdue.** Three "uncapped on the 2 GB tier"
  items now stack — obstacles (v8), shadows (v7), LiDAR on megasites (v9). The
  free/Pro line named since v6 needs real auth/billing before any of them ships.

### Monetisation (the data wedge finally has teeth)

- **Free** — all geometry, both analyses at base depth, **and real LiDAR heights
  where they fit the free tier** (covered mid-size England). The first time the
  free tier carries surveyed heights, not just estimates — the demo that sells.
- **Pro** — uncapped LiDAR on the megasites people most want (central London),
  multi-region coverage, the sunlight heatmap, analysis depth. The wedge sharpens:
  the most-wanted sites are precisely the governor-skipped ones.
- **The honesty guardrail holds the line**: LiDAR is a measured surface-minus-ground
  sample at a stated ~2 m massing tolerance with a *visible* real/estimated split —
  never sold as survey-grade per-building truth. The paid value is coverage, scale,
  and depth, never accuracy we can't stand behind.

---

## v10 — Landing page, pricing, waitlist (standing method: conjecture & refutation)

v10 is the first version that is not about geometry. It adds a public face: a
landing page at `/`, the tool moved to `/app`, and `/api/waitlist` — the first
piece of real monetisation infrastructure. The standing method now points at a
different target: not "is the model honest?" but "does the product convert a
stranger into a signup?"

### 1. Problem — what still reads as unfinished

- **The hero is a placeholder.** The single most persuasive asset — a real Rhino
  screenshot of a SiteGrab output — ships as a grey "[Add screenshot here]" box.
  Until it is replaced, the page *tells* an architect the output is good and then
  shows them nothing. The whole page's credibility rests on an image that does not
  exist yet. This is the largest single gap and it is not a code problem.
- **Zero social proof.** No names, no studios, no "used by", no count of files
  generated. A serious architect's default is scepticism; nothing on the page
  answers "has anyone I'd respect actually used this?"
- **No motion.** TikTok traffic arrives primed for a *demonstration*. The page is
  static; the one thing that actually sells this tool — watching a box on a map
  become a Rhino model in two minutes — happens only if they leave and try it.
- **The waitlist asks for faith, not money.** £8/mo "coming soon" with no card
  capture measures stated intent, not willingness to pay. A waitlist signup is a
  weak signal; we will over-read it.
- **The free/Pro split is still drawn, not enforced.** The pricing table promises
  LiDAR/area/priority tiers that no auth or billing system gates. The page now
  *publicly commits* to a boundary the backend cannot hold.

### 2. Conjectured solutions

- **C1 — Real hero screenshot (+ a second "layers" detail shot).** Replace the
  placeholder; add one close-up of the Rhino layer panel to prove the alignment claim.
- **C2 — A 15–30s screen-capture video/GIF on the landing page.** Box → outputs →
  Rhino. The demonstration TikTok promised, delivered above the fold.
- **C3 — Stripe Checkout behind the Pro CTA** (capture card / pre-authorise), turning
  the waitlist into a paid pre-order.
- **C4 — Social proof block**: real signup count ("N models generated"), and 2–3
  quotes from the LinkedIn/Reddit comments that already exist.
- **C5 — One more headline analysis (sunlight-hours heatmap or views)** to widen the
  "what it produces" story past sun/wind.
- **C6 — LiDAR terrain surface** (the v9 heights win, extended to the ground).

### 3. Criticism — attack each

- **C1** is necessary but not sufficient, and it is *not falsifiable by building it* —
  a great screenshot of a mediocre model still converts; a poor screenshot of a great
  model does not. Its risk is that it raises the expectation bar (see §5). Cheapest,
  highest-leverage, do first. **Falsifier:** signups don't move after a strong hero →
  the bottleneck was never the image.
- **C2** is the strongest conversion lever *for TikTok traffic specifically*, but it is
  the most effort, and a janky screen-grab is worse than no video. It also risks
  showing the cold-start wait honestly (a minus) or hiding it dishonestly (worse).
  **Falsifier:** scroll-depth/heat data shows people leave before the video, or a
  cohort with video converts no better than one without.
- **C3** assumes the constraint is *commitment friction*, not *belief*. If people don't
  yet believe the free tool, asking for a card converts worse than email and poisons
  the funnel. Pre-orders for a not-built feature also carry refund/trust risk.
  **Falsifier:** A/B card-capture vs email — if card-capture's absolute signup count is
  a fraction of email's, the constraint was belief, not friction.
- **C4** is high-trust-per-pixel but **fabrication-fragile**: invented testimonials or a
  vanity "10,000 architects" would be caught and would destroy the exact credibility it
  buys. Only deployable once *real* numbers/quotes exist, which is a chicken-and-egg
  with launch. **Falsifier:** real metrics stay too small to show — then it's premature.
- **C5/C6** deepen the product, not the conversion. They answer "is it good enough?"
  for users *already* in `/app` — a retention/depth lever, mis-aimed at a top-of-funnel
  problem. **Falsifier:** the drop-off is at the landing page, not inside the tool
  (analytics will say which).

### 4. Replacement — ranked shortlist for v11

Ranked by **conversion impact × feasibility × monetisation potential**:

1. **C1 — Real hero screenshot (+ layer detail).** Highest impact ÷ effort on the
   board. Nothing else on the page works until the hero is real. Pure asset work.
2. **C2 — Landing demo video/GIF.** The decisive lever for the actual traffic source
   (TikTok). Higher effort, but it is the medium the audience arrives expecting.
3. **C4 — Real social proof, deployed the moment metrics exist.** Cheap once the
   numbers are real; gated only by having launched. Wire the "models generated"
   counter now so the number is ready to show.
4. **C3 — Stripe card-capture, as an A/B against email — not a replacement.** Run it
   *after* C1/C2 lift belief, so the test measures friction in isolation. This is the
   only item that turns intent into revenue.
5. **C5/C6 — depth (sunlight heatmap / LiDAR terrain).** Real, but retention not
   acquisition; sequence after the funnel's top is fixed.

### 5. New problem — what a proper landing page creates

**A landing page this serious manufactures an expectation of quality the tool must now
live up to — and a public promise the backend has not yet kept.** Before v10 a visitor
met the tool with no priors. Now they meet a page that *claims* "ready in two minutes,"
"surveyed LiDAR heights," "open straight in Rhino" — and then they hit the free server's
cold start, the England-only LiDAR boundary, the ~5km cap. Every gap between the
landing-page promise and the `/app` reality is now a *felt* disappointment, not a
neutral discovery. The pricing table makes this concrete: it publicly draws a free/Pro
line that no auth or billing enforces, so the page is writing cheques v11's backend has
to cash. The landing page didn't just add a front door — it raised the floor the product
is now judged against.

### Monetisation note — read the waitlist honestly

The waitlist is the first conversion instrument, but it is a **weak signal measured in a
noisy window.** The number that matters is *signups in the first two weeks after the
TikTok push*. Interpret it as a diagnostic, not a scoreboard:

- **Lots of traffic, few signups →** the bottleneck is the **landing page or the
  product belief** (fix C1/C2 before anything else).
- **Signups but no eventual conversion to paid →** the bottleneck is **price or the
  Pro value proposition** (C3 tells you if it's friction or belief).
- **Few even reach the page →** it's **distribution**, and none of C1–C6 is the
  problem.

The trap is treating a free, no-card email as proof of willingness to pay. It is proof
of *curiosity*. Only C3 converts curiosity into a commitment we can bank — and it should
be run as a measured A/B, after C1/C2, so we learn whether the constraint was ever money
in the first place.

## v10.1 — The hang: fail fast, never spin forever (standing method: conjecture & refutation)

### 1. Problem — the promise the backend didn't keep

v10's §5 named it precisely: the landing page promises "ready in two minutes," but the
`/app` reality could deliver *no answer at all*. When generation failed or stalled
server-side — Overpass mirror timeout, Render cold start, or the process being OOM-killed
before the 440 MB solid governor's check ever fired — the frontend spun on "Aligning and
writing Rhino file…" **forever**: no error, no recovery, the box the user drew lost. The
worst failure a tool can have is not an error; it is *silence*. Three specific holes:

- **No total-time ceiling anywhere.** `fetch_overpass` allowed 6 attempts × 180 s +
  growing backoff — over 1000 s worst case. Terrain (`_fetch_tile`) and LiDAR
  (`_get_coverage`) had the same unbounded shape. A single stuck mirror hung the whole
  request past any human's patience, with the socket still nominally "open."
- **The client had no timeout at all.** `fetch("/generate")` with no `AbortController`:
  if the connection hung or was dropped mid-write by an OOM kill, the promise never
  settled, so the spinner never stopped.
- **The memory governor was a single upfront estimate.** The solid budget and the LiDAR
  `budget_px` were both computed *once* from the building count. A denser-than-modelled
  box (the classic 1.5 × 1.0 km urban tile) could sail past the upfront estimate and get
  OOM-killed by Render *between* the two checks — the fallback logic never ran.

### 2. Conjectured solutions

- **T1 — Client `AbortController`, 75 s ceiling.** Any request that doesn't settle in
  time aborts to a visible error + a "Try again" that replays the same params.
- **T2 — Hard total-time budgets on every fetch stage.** Overpass 30 s, terrain 25 s,
  LiDAR 15 s/coverage — measured against a monotonic deadline across *all* retries and
  backoff, not per-attempt. Each raises a typed error (`OverpassTimeoutError`, …).
- **T3 — Typed staged errors → uniform JSON body.** Every distinct failure converts to
  `{"error": <code>, "message": <human>, "stage": <overpass|terrain|lidar|validate|
  build|write>}`, logged by stage, never a raw 500 or a dropped connection.
- **T4 — Live memory governor in the building loop.** Check real RSS (psutil) every 400
  buildings: at a soft limit stop minting solids (fall to meshes), at a hard limit abort
  with a clean 507 *before* the OOM killer fires. Plus an upfront building-count reject
  (413) for sites beyond what the 512 MB tier can build.

### 3. Criticism — attack each

- **T1** cannot distinguish "server is slow but working" from "server is dead" — a 75 s
  abort will occasionally kill a request that would have succeeded at 80 s on a cold
  start. Accepted trade: a false-timeout the user can *retry* in one click beats an
  infinite spin they cannot escape. The ceiling sits deliberately *above* the backend's
  own 30/25 s budgets, so in practice the server returns its own typed error first and
  the client abort is the backstop, not the primary path.
- **T2** is falsifiable and was tested: with every mirror forced to error, `fetch_overpass`
  raises `OverpassTimeoutError` in 3.0 s against a 3 s budget (not 1000 s), and terrain
  the same. **Falsifier that passed:** a small live Clifton box still recovered from a
  *real* Overpass 504 on the first mirror by retrying the second within budget — the
  ceiling bounds failure without breaking transient-failure recovery.
- **T3**'s risk is leaking internal detail into user-facing messages. Mitigated: typed
  errors carry curated human copy; only the catch-all `write` path interpolates the raw
  exception, and that path is genuinely unexpected. `detail` mirrors `message` so the
  existing client field keeps working.
- **T4** is the subtle one. **LiDAR was deliberately *not* made a hard error** — it
  already degrades to type-estimated heights and *says so* in the provenance note, which
  is the honest v9 design; failing a whole build because an *optional* height source was
  slow would be a regression, so LiDAR timeout stays a graceful fallback, only *bounded*
  in time. The read-back verification (`File3dm.Read` loads a *second* full copy of the
  model) is itself an OOM spike on a large site, so it is now skipped when RSS is already
  near the ceiling — the file is written and served either way; only the self-check is
  sacrificed. **Falsifier that passed:** a live build stayed well under the soft limit,
  `readback_skipped=False`, real LiDAR present — the guards are dormant on normal sites
  and only bite the pathological ones.

### 4. Replacement — what shipped

All four, because they are one mechanism — *bound every wait, and make the boundary
visible* — not four features:

1. **Backend budgets + typed errors (T2/T3).** `errors.py` holds the staged exception
   hierarchy; `fetch_core`/`fetch_elevation`/`fetch_lidar` enforce monotonic deadlines;
   `/generate` maps them to JSON with a machine code, a human message and the failing
   stage, logged as `[generate] stage=… error=…` for blind Render-log triage.
2. **Live memory governor + upfront reject (T4).** `build_combined` watches RSS in the
   building loop (soft → meshes, hard → 507) and rejects > 24 000 buildings upfront (413,
   above Shoreditch's proven-deployable ~13 k). The read-back is guarded.
3. **Client ceiling + one-click retry (T1).** `fetchWithTimeout` aborts at 75 s; every
   failure path resolves to a visible status-line error, and total failure offers "Try
   again" with the same source + formats so the drawn box is never lost.

### 5. New problem — a bounded failure is still a failure

**Fail-fast converts silence into an honest error, but it does not make the area
generate.** A user who draws a dense 3 km box and gets "Area too large or dense — try a
smaller box" now *understands* what happened, which is strictly better than a spinner —
but they still didn't get their model, and the message is an admission that the free tier
can't serve the site their brief actually needs. Every ceiling here (30 s Overpass, 24 000
buildings, 512 MB) is a line drawn by the *server tier*, not the *use case*, so each
honest error is also an advertisement for the constraint Pro removes. That is the correct
next wedge — the 2 GB tier that lifts the building cap and runs full-resolution LiDAR on
megasites is now a *felt* need with a name attached to the error, not a hypothetical. The
risk to watch: if the *common* case (a normal neighbourhood box on a warm server) ever
trips these guards, they have become false positives that punish the very users the
landing page courted — so the thresholds are set above every measured-good site, and the
soft/hard split degrades gracefully before it ever refuses. The honest failure is the
floor; the job from here is to raise the ceiling, not lower the floor to meet it.

### Monetisation note — the error message is the upsell

Unlike geometry, an *honest constraint* is something people pay to remove. The "too large
for the free tier" 413 and the LiDAR-governed provenance note both name a limit and
implicitly price its removal. This is the cleanest monetisation signal the tool has
produced: not "pay for a nicer model," but "pay to model the site you actually have." The
counter to wire next is *how often real traffic hits each ceiling* — a guard tripped by
1 % of sessions is a fair Pro gate; one tripped by 30 % is a broken free tier masquerading
as a paywall, and the analytics must tell those apart before any billing line is drawn.

## v10.2 — SEO landing page: "free 3D site plan" (standing method: conjecture & refutation)

v10.2 is the first version aimed at *acquisition* rather than the product itself: a
dedicated page at `/free-3d-site-plan`, styled in its own dark/amber theme, targeting the
search query an architecture student actually types. The tool is unchanged; the bet is
that a purpose-built page for one keyword out-ranks and out-converts the generic `/`
homepage for that intent. The standing method now points at a third target — not "is the
model honest?" nor "does the page convert?" but "will anyone searching ever *see* this
page, and would we know if they did?"

### 1. Problem — what this addition leaves unfinished

- **The page carries no analytics at all.** The homepage ships GA4 (`G-6VSPZVNF5V`); this
  page ships nothing. Every CTA click, scroll, and bounce on the one page whose entire
  purpose is measurable acquisition is currently invisible. We cannot tell an indexed page
  with zero traffic from a page getting traffic that doesn't convert — the two failures
  that demand opposite fixes look identical. This is the single largest gap and, unlike a
  content gap, it is silent.
- **The demo asset is real but is the homepage's screenshot, and it is still.** Task 2 was
  satisfied honestly — `static/hero-model.png` (a *real* Rhino screenshot, not the grey
  placeholder) now fills the demo box, so nothing fake shipped. But it is (a) the *same*
  image the homepage hero uses, so a visitor arriving `/` → footer → landing sees one shot
  twice, and (b) a static frame where the placeholder copy literally promised a *screen
  recording of the generation happening*. For "free 3D site plan" search intent a credible
  still is adequate; it is not the demonstration the box described.
- **Indexing is assumed, not secured.** Nothing here makes Google crawl or rank the page.
  It lives on an `onrender.com` subdomain (borrowed authority, near zero), it is one thin
  page for a query with real commercial competition, and the sitemap has not been submitted
  to Search Console (I have no access to that console).
- **No variant, no experiment.** One page, one CTA, one headline. There is no A/B path, so
  even once analytics exist, the page can be measured but not *improved by comparison* —
  only replaced wholesale.

### 2. Conjectured solutions

- **C1 — Add the GA4 snippet to this page** (same measurement ID as `/`), plus an event on
  the CTA clicks. Make the acquisition page measurable before spending effort ranking it.
- **C2 — Submit the sitemap to Search Console and request indexing** for the three URLs;
  the internal footer link from `/` already gives a crawl path, this makes it active.
- **C3 — A real custom domain** (`sitegrab.com`) so the SEO bet accrues authority to an
  asset we own rather than a Render subdomain.
- **C4 — The motion demo GIF/video** (carried from v10's C2) embedded above the fold in
  place of, or beside, the static screenshot.
- **C5 — Programmatic per-city pages** ("free 3D site plan · <city>") generated from the
  same template, chasing long-tail intent at volume.
- **C6 — A/B the headline + CTA copy** once analytics can score the variants.

### 3. Criticism — attack each

- **C1** is not falsifiable by building it (a tag proves nothing on its own), but it is the
  precondition for every other conjecture being *testable at all* — without it C2–C6 are
  faith. Its only risk is triviality masquerading as progress: a tag that fires but that
  nobody reads is the same as no tag. **Falsifier that matters:** the GA4 real-time view
  shows zero events after a known visit → the snippet is misplaced or the ID is wrong.
- **C2** attacks the assumption that *publishing* equals *being found*. It is necessary but
  weak alone: submission gets a page *crawled*, not *ranked*. **Falsifier:**
  `site:sitegrab.onrender.com/free-3d-site-plan` returns nothing 2–3 weeks after
  submission → crawling itself stalled (robots, canonical, or thin-content suppression),
  not just ranking. **Fallback if indexing stalls:** the page still earns its keep as the
  link target for any off-site post (Reddit/LinkedIn/TikTok bio) — direct and referral
  traffic don't need Google — so the SEO bet failing does not strand the asset.
- **C3** is the real ranking lever and the honest diagnosis of why C2 will under-deliver:
  domain authority, not on-page content, is the binding constraint for a competitive
  commercial query. But it is the highest-friction item (purchase, DNS, HTTPS, updating
  every hard-coded `sitegrab.onrender.com` in the sitemap/robots/footer) and it is
  premature to pay for authority to a page we can't yet measure (C1) or prove crawlable
  (C2). **Falsifier:** even after a custom domain, rank for the target query doesn't move
  in 60–90 days → the constraint was content depth or backlinks, not the domain.
- **C4** is the strongest *conversion* lever but the weakest-justified *here*: it fights
  the v10 fight (TikTok wants motion) on a page whose traffic is search, which arrives
  wanting to read what it gets and click. A janky recording is worse than the honest still
  already shipped. **Falsifier:** once C1 exists, a cohort seeing the GIF converts no
  better than one seeing the screenshot → motion wasn't the bottleneck for search traffic.
- **C5** is the highest-ceiling monetisation play (long-tail volume compounds) and the
  most dangerous: templated near-duplicate "doorway" pages are exactly what Google's thin-
  content and doorway penalties target, and shipping 200 of them off one dataset could
  suppress the *whole* subdomain, not just the weak pages. **Falsifier:** a 5-page pilot's
  indexed pages hold rank for a month without dragging `/` down → the pattern is safe to
  scale. Do not scale before that pilot.
- **C6** is real but strictly downstream of C1 — an A/B with no analytics is theatre. It
  also needs traffic volume this page does not yet have to reach significance, so it is
  premature twice over.

### 4. Replacement — ranked shortlist for v11

Ranked by **real demand × how unserved × feasibility × monetisation potential** (the
standing rubric):

1. **C1 — GA4 on this page (+ CTA event).** Trivial feasibility, and it is the gate every
   other item's *evidence* passes through. Zero monetisation on its own, infinite as a
   multiplier: without it the whole acquisition bet is unfalsifiable. Do first, this week.
2. **C2 — Submit sitemap to Search Console, request indexing.** Low effort (a manual
   console action the human operator must do — I cannot), directly serves the page's one
   job. Ranked above the domain because a crawlable page on a weak domain still beats an
   uncrawled page on a strong one.
3. **C3 — Custom domain.** The genuine ranking constraint and a durable owned asset that
   compounds for *every* future SEO page. Higher friction, so sequenced after the cheap
   measurement/indexing steps prove the funnel is worth the domain's cost.
4. **C4 — Motion demo.** Real conversion lift, but mis-aimed at search intent and higher
   effort; run it as a measured swap *after* C1 can score it, not on faith.
5. **C5 — Programmatic city pages.** Highest monetisation ceiling via long-tail volume and
   genuinely unserved, but penalty-fragile; gated behind a 5-page pilot that must prove it
   doesn't poison the subdomain before scaling.
6. **C6 — A/B copy.** Downstream of both analytics (C1) and traffic; parked until both
   exist.

### 5. New problem — a second front door widens the promise-gap

The homepage already writes cheques the backend must cash (v10 §5: "two minutes,"
"surveyed LiDAR," the free/Pro line no billing enforces). This page **adds a second, more
specific promise to a more specific audience** — "free 3D site plan in under a minute, sun
path included" — and routes a *colder* visitor (a stranger from a search result, with no
context) straight at the same `/app` cold start, England-only LiDAR boundary, and ~5 km
cap. A search visitor is less forgiving than a referral: they did not come on a
recommendation, they came on a keyword, and the first friction confirms their default
scepticism. So the SEO win and the product's honesty limits are now in direct tension —
the better this page ranks, the more first-time strangers meet the free tier's ceilings
with no goodwill banked. The page also hard-codes `sitegrab.onrender.com` into the
sitemap, robots, and its own footer copy, quietly deepening the coupling to a domain the
C3 shortlist says we should leave — every SEO asset added now is one more thing to rewrite
when the domain moves.

### Monetisation note — you cannot price what you cannot count

This version's monetisation value is *entirely* contingent on C1. An acquisition page with
no analytics is not a weak monetisation instrument — it is a *zero* one, because the funnel
it feeds is unobservable end to end: we would see waitlist signups (v10's counter) but have
no way to attribute them to this page versus `/`, and so no way to know whether the SEO bet
returns anything. The honest reading is that v10.2 shipped the *asset* but not the
*instrument*: the page can convert, but until GA4 is on it and the CTA fires an event, any
claim that it does — or doesn't — is unfalsifiable, and an unfalsifiable conversion claim
is exactly the kind of vanity signal the v10 monetisation note warned against reading as
proof. Measure first; rank, restyle, and pay for a domain second.
