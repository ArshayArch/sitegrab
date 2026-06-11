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
