# Topography Rationale — why terrain, how much terrain, and for whom

Written before any terrain code, from the point of view of a working architect starting a
real project who has just found SiteGrab.

---

## 1. What do I actually need terrain for at the start of a project?

At concept stage — the stage SiteGrab serves — terrain answers questions, it doesn't
produce deliverables. Concretely:

- **Where does the site fall, and by how much?** A 4m fall across a 40m plot is a
  half-storey: it decides whether the scheme is split-level, whether parking goes under
  the high side, where the entrance can be at-grade. I need that number on day one.
- **How do buildings around me deal with the slope?** Seeing the *existing* massing
  sitting on the *real* ground tells me the local convention — stepped terraces, plinths,
  undercrofts. A flat-Z=0 massing model actively lies about this on any sloped site.
- **Water and access.** Which way the site drains (roughly), where level entrances and
  accessible routes are plausible, which streets climb and which run along contour.
- **Section thinking.** The first sketch sections need a believable ground line under
  them. Contours + a terrain surface give me that the moment I cut a section in Rhino.
- **Cut-and-fill *intuition*, not calculation.** At concept stage I need to see that a
  proposal digs into the hill; I do not need volumetric earthwork numbers.

What I do **not** need at this stage: survey-grade accuracy, retaining-wall design data,
or drainage engineering. That comes later, from a real topographic survey.

## 2. The minimum that is genuinely useful vs. gimmick

- **Resolution.** The free global DEMs underneath the AWS terrain tiles are ~30m data
  (SRTM-class) over most of the world, ~10m in parts of the US/Europe. So a sample
  grid of roughly **10–30m spacing** captures everything the data actually contains.
  Fetching a 1m-spaced grid would be interpolation theatre — heavier, no truer.
- **Contours at 2m/5m/10m** depending on relief, like a real site plan. An architect reads
  contours faster than a shaded mesh; they're also what gets traced into drawings. 2m is
  the floor — SRTM-class vertical noise is several metres, so 1m contours would be
  speckle, not information (and tiny noise loops are filtered out for the same reason).
- **Buildings must sit on the ground.** This is the single highest-value item. A terrain
  mesh under floating Z=0 buildings is a gimmick; massing stepping down a hill is the
  thing that makes the model *usable*.
- **Overkill (excluded):** NURBS terrain patches, cut/fill volume reports, draped aerial
  imagery, sub-metre DEM sources that need API keys, hydrological flow analysis.

## 3. The market-gap test, answered honestly

The honest landscape:

- **CadMapper** is the closest competitor and already does most of this: type a place,
  get a CAD/Rhino file with buildings *and* topography. It is polished and proven. But it
  is **paid above 1 km²**, requires an account, and its buildings sit on flat ground in
  the cheaper outputs; terrain and massing arrive as separate, not-draped layers.
- **Heron / Docofossor / Elk** (Grasshopper plugins) can produce strictly better terrain —
  if you install plugins, learn their component graphs, and assemble the pipeline
  yourself. That's an afternoon of GIS plumbing per site, and a skillset many architects
  don't have or want.
- **QGIS** is free and can do all of it, with the steepest workflow of all (download DEM,
  reproject, clip, generate contours, export, re-import, align by hand).
- **Google Earth** is view-only; nothing comes out of it into CAD legitimately.

**Honest verdict:** SiteGrab is *not* inventing a category — CadMapper proved the
category. What none of the free/instant routes give you is the combination: **zero
install, zero account, zero cost, one click → one Rhino file in true UTM metres where the
terrain, the contours, the 2D linework AND the massing are already aligned, and the
buildings actually sit on the ground.** CadMapper charges for the size of site this
handles and doesn't drape its massing; the plugin/GIS routes make you build the pipeline
yourself.

**The gap in one sentence:** *a free, no-signup, browser-to-Rhino site model where
terrain, contours, linework and ground-seated massing arrive pre-aligned in one file —
the part of CadMapper people pay for, minus the assembly work of the free alternatives.*

## 4. Scope decision for this build

Built to the concept-stage need identified above, and no further:

1. **Elevation source:** AWS Terrain Tiles (terrarium PNGs) — free, keyless, global.
   Zoom auto-picked so the sample grid is ~10–30m spacing and stays small in memory.
2. **Terrain surface:** one rhino3dm **mesh** on `TERRAIN/surface`. A mesh, not NURBS —
   it's honest about the data and cheap to render.
3. **Contours:** polylines at true elevation on `TERRAIN/contours`, interval picked
   automatically (1/2/5/10m…) from the site's relief. Flat sites (<1.5m range) get no
   contours rather than noise.
4. **Building drape:** each building's base is set to the **lowest terrain height under
   its footprint**, extruded upward by its OSM height. Buildings stay plumb — they do
   not tilt with the slope. On a slope the high side cuts into the hill and the low side
   reads as a plinth. This is the deliberate convention: it is how real buildings meet
   sloped ground, and it keeps wall geometry vertical and usable.
5. **Linework decision: kept FLAT, at a datum.** The 2D linework stays planar, placed at
   the site's lowest terrain elevation (a clean datum just under the terrain) rather
   than draped. Reasoning: the linework's job is to be a *drawing* — something you trace,
   measure and print in plan. Draped onto the mesh it becomes thousands of wobbly 3D
   polylines that are useless in plan view and double the build cost. The terrain
   surface and contours already describe the ground in 3D; the linework describes it in
   plan. Roads in the **3D group**, however, *are* draped (per-vertex), because they are
   read as objects in the massing model and floating roads through a hillside read as
   broken. The terrain-aware **3D massing roads draped, 2D linework flat at datum** split
   gives each dataset the form it is actually used in.
6. **Water reads as sea level.** The terrarium tiles include ocean *bathymetry*, which
   on any coastal site drags waterside ground (and the buildings on it) tens of metres
   below sea level — found and fixed in testing. The grid is clamped at 0m: water reads
   as a flat plane at quay level, which is what a site model wants. Known cost: the rare
   deep below-sea-level land site (Death Valley, Dead Sea shore) reads flat at 0.
7. **Terrain is optional** (UI toggle, default ON for the combined file) — it adds fetch
   time, and flat-city users (Dubai Marina) lose nothing by turning it off.
7. **Standalone outputs unchanged:** the lean `.3dm` massing-only and the `.dxf` remain
   flat/fast as before. Terrain ships in the combined file, which is the headline output.
