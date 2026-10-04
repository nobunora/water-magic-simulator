# Water Magic Simulator

Simulate water magic on real Japanese terrain with SFINCS: place a spell, set its water volume, footprint and initial motion, then inspect water depth, velocity and flow over time.

[日本語ガイド](README.ja.md) · [Implementation and validation](docs/specs/water-magic-implementation.md) · [Initial water and momentum](docs/specs/water-magic-initial-momentum.md)

## Water magic workflow

1. Start the local review application using the canonical environment described below.
2. Open `/?mode=water-magic` on the launcher's local URL.
3. Choose a location and a spell or comparison scenario. Adjust its water volume, dimensions, direction, initial velocity and observation time.
4. Run the simulation and inspect depth, native flow vectors, particles and timeline results. Save and reopen runs using ZIP archives.

The water magic variant uses native 0.5 m hydraulic cells. It supports initial water placement and continued water supply; existing rainfall runs and 1 m archives retain their compatibility behavior. Game and kaiju quantities are estimated scenario inputs, not official measurements. The model approximates ground-level water motion; it does not simulate combat effects, creature motion or structural destruction. Adaptive grids remain disabled.

## Repository relationship

This is an independent repository derived from [Urban Pluvial Flood Simulator](https://github.com/nobunora/urban-pluvial-flood-simulator), preserving its Git history. It is a source-derived fork, not a GitHub fork-network repository. The original repository remains the rainfall simulator; water magic development is published here.

The preserved upstream baseline is `3fea888773575b288033faa2f67e12ee645609cb` (`baseline/main-before-water-magic-2026-10-02`). Earlier documents describing only a local branch/worktree record the state before this repository was separated on 2026-10-04.

## Inherited rainfall simulator documentation

A compact **Rain-on-Grid urban pluvial flood simulator** with automatic Japanese terrain and urban-data preparation.

## Current local review build

The current development priority is a **user-reviewable Full 1 m vertical slice** before completing Adaptive.

Use the canonical environment in `environment.yml`:

```bash
python -m scripts.bootstrap_local_review --run-review
```

Node.js >=22.12 is required when rebuilding the frontend; Node.js 24 is supported. For a permitted existing SFINCS 2.4.0 Galibier executable, pass `--sfincs-bin <path>`.

Detailed setup and review acceptance criteria: `docs/local-review.md`.

## Product direction

The repository currently contains the original native Local-Inertial reference solver and its preprocessing workflow.

The proposed v0.1 product direction is to use **SFINCS** as the primary hydraulic engine while retaining the existing data-acquisition work and evolving it toward an accuracy-first Full 1 m / Adaptive workflow.

Draft specifications:

- product specification (canonical English): `docs/PRODUCT_SPEC_DRAFT.md`
- product specification (Japanese reference translation): `docs/PRODUCT_SPEC_DRAFT.ja.md`
- detailed implementation-specification template (canonical English): `docs/IMPLEMENTATION_SPEC_TEMPLATE.md`
- detailed implementation-specification template (Japanese reference translation): `docs/IMPLEMENTATION_SPEC_TEMPLATE.ja.md`

The English files are the source of truth. The Japanese files are maintained as human-readable reference translations; if the two versions conflict, the English version takes precedence.

These documents are intentionally drafts. They distinguish what v0.1 will implement, what is deferred, and what the project should never claim or become.

The preprocessing pipeline can build a simulation area from only a latitude/longitude and size:

- elevation: **GSI public elevation tiles**, preferring DEM1A (~1 m)
- buildings/roads: **Project PLATEAU CityGML** first
- vector fallback: **OpenStreetMap / Overpass** when PLATEAU is unavailable
- hydraulic rasterization: building no-flow cells, lower road roughness, roof-rainfall redistribution

The repository contains no private location data or bundled source datasets.

## Quick start: fully automatic input preparation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Prepare a 2 km x 2 km area at 1 m grid spacing:

```bash
python -m scripts.prepare_area \
  --center-lat <LATITUDE> \
  --center-lon <LONGITUDE> \
  --half-size-m 1000 \
  --grid-m 1 \
  --out-dir area
```

This performs:

```text
latitude / longitude
        ↓
GSI DEM1A elevation tiles
        ↓  NoData fallback
DEM5A -> DEM5B -> DEM5C -> DEM10B
        ↓
local metric DEM
        ↓
PLATEAU CityGML range query (bldg, tran)
        ↓
LOD0 building footprints + road geometry
        ↓  if PLATEAU unavailable
OpenStreetMap fallback
        ↓
raster hydraulic inputs
```

Outputs:

```text
area/
├─ dem_1m.npz
├─ dem_1m.json
├─ manifest.json
├─ cache/
├─ vectors/
│  ├─ buildings.npz
│  ├─ basemap_vectors.npz
│  └─ vectors_manifest.json
└─ hydraulic_inputs/
   ├─ z.bin
   ├─ manning.bin
   ├─ rain_weight.bin
   ├─ building.bin
   ├─ road.bin
   └─ metadata.npz
```

`manifest.json` records the providers actually used. Do not assume every area has DEM1A or PLATEAU coverage.

### Vector provider selection

Default `--vector-provider auto` tries PLATEAU first and uses OSM only if PLATEAU cannot provide usable building data.

```bash
python -m scripts.prepare_area ... --vector-provider plateau
python -m scripts.prepare_area ... --vector-provider osm
```

## GSI elevation acquisition

The automatic downloader uses the public GSI PNG elevation tile service, so a GSI Fundamental Geospatial Data account is **not required** for the normal automatic workflow.

Provider priority:

1. DEM1A (`dem1a_png`, zoom 17)
1. DEM5A (`dem5a_png`, zoom 15)
1. DEM5B (`dem5b_png`, zoom 15)
1. DEM5C (`dem5c_png`, zoom 15)
1. DEM10B (`dem_png`, zoom 14)

Tiles are decoded from GSI's signed 24-bit RGB elevation representation, mosaicked in Web Mercator, and reprojected to a local azimuthal-equidistant metric grid.

- https://maps.gsi.go.jp/development/ichiran.html
- https://maps.gsi.go.jp/development/demtile.html

## PLATEAU building/road acquisition

The automatic vector downloader queries the official PLATEAU distribution API by bounding box with `types=bldg,tran` and downloads only intersecting CityGML files.

- API endpoint: `https://api.plateauview.mlit.go.jp`
- docs: https://docs.plateauview.mlit.go.jp/datasets/citygml/

PLATEAU CityGML commonly uses EPSG:6697 and stores coordinate tuples as `latitude longitude elevation`. Buildings prefer `lod0FootPrint`, then `lod0RoofEdge`, then `GroundSurface`. Roads prefer surface polygons when available and fall back to line geometry.

The PLATEAU API is currently documented as a trial service, which is why `auto` mode has an OSM fallback.

## Legacy/manual GSI GML workflow

The original converters remain available when a version-pinned GML dataset is required:

```bash
python scripts/gsi_dem1a_to_npz.py \
  --zip DEM1A_A.zip \
  --center-lat <LATITUDE> \
  --center-lon <LONGITUDE> \
  --half-size-m 1000 \
  --grid-m 1 \
  --out dem_1m.npz
```

```bash
python scripts/gsi_basic_to_vectors.py \
  --zip BASIC_A.zip \
  --center-lat <LATITUDE> \
  --center-lon <LONGITUDE> \
  --half-size-m 1000 \
  --out-dir vectors
```

See `docs/data_download.md` for automatic vs version-pinned acquisition.

## Current reference solver

The current native solver remains available as a research/reference implementation while the SFINCS product integration is designed.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

or:

```bash
g++ -O3 -march=native -fopenmp -std=c++17 src/solver.cpp -o solver
```

Example run: `2001 x 2001`, `dx=1 m`, `1 h`, `115 mm/h`:

```bash
OMP_NUM_THREADS=8 ./build/local_inertial_solver \
  2001 1.0 3600 115 area/hydraulic_inputs result
```

Outputs:

```text
result_h.bin      final water depth
result_hmax.bin   maximum water depth
result_qx.bin     final x-face unit-width discharge
result_qy.bin     final y-face unit-width discharge
```

## Plot maximum depth

```bash
python scripts/plot_results.py \
  --metadata area/hydraulic_inputs/metadata.npz \
  --prefix result \
  --out max_depth_rainbow.png
```

## Current native hydraulic model

The reference implementation includes:

- 2D Local-Inertial approximation
- adaptive global CFL
- de Almeida-style discharge stabilization
- semi-implicit Manning friction
- wet/dry handling
- positivity-preserving donor-cell limiter
- buildings as no-flow cells
- lower road Manning roughness
- roof-rainfall redistribution with rainfall-mass conservation
- OpenMP parallelization

Water-surface elevation:

```math
\eta = z + h
```

Continuity:

```math
\frac{\partial h}{\partial t}
+\frac{\partial q_x}{\partial x}
+\frac{\partial q_y}{\partial y}
=R
```

Reference Local-Inertial face update:

```math
q^{n+1}
=
\frac{
\bar q - g h_f \Delta t\, \partial\eta/\partial x
}{
1 + g\Delta t n^2 |\bar q|/h_f^{7/3}
}
```

## Web demo mode

The same API and frontend can run as a review-only web demo. The server, not
the browser, enforces the mode: new simulations and arbitrary ZIP uploads are
disabled, while allowlisted prepared results can be opened from a local data
directory.

```powershell
$env:FLOODSIM_APP_MODE = "demo"
$env:FLOODSIM_DEMO_RESULTS_DIR = "C:\path\to\demo-results"
$env:FLOODSIM_DOWNLOAD_URL = "https://github.com/owner/repository/releases/latest"
python -m uvicorn floodsim.api.app:app
```

Generate the allowlisted Full 1 m archives sequentially with an already
permitted local SFINCS executable. The publication set uses the existing
Yokkaichi (`±2000 m`) and Chiba (`±1000 m`) results, plus Saga and both Nagoya
cases at `±500 m`:

```powershell
$env:SFINCS_BIN = "C:\path\to\sfincs.exe"
python -m scripts.generate_demo_results `
  --output-dir C:\path\to\demo-results `
  --work-dir C:\path\to\demo-work
```

The generator preserves the hydraulic solver configuration. It saves map and
history frames every 15 minutes to keep portable review archives bounded;
SFINCS maximum-depth output remains independent. Coastal demo generation uses
the production nearest-neighbour elevation fill with a documented 5% coverage
ceiling, without changing the normal application's 2% default.

The Windows one-folder launcher automatically discovers `demo-results` and a
self-built `sfincs/sfincs.exe` placed beside
`UrbanPluvialFloodSimulator.exe`. The public archive includes the runtime DLLs,
GPL-3.0 license and exact corresponding SFINCS source archive, so neither a
separate SFINCS download nor a Python installation is required. An explicit
`--sfincs-bin` still overrides the bundled engine. A reproducible package can
be built from an activated canonical environment with:

```powershell
.\scripts\build_windows_release.ps1 `
  -Version v0.1.20 `
  -DemoResultsDir C:\path\to\demo-results `
  -SfincsSourceDir C:\path\to\SFINCS
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Network integration and solver smoke-test instructions for Codex are in `CODEX_TEST_PLAN.md`.

## Important limitations

The current implementation is a research/reference implementation, not an operational flood-warning product. It does not currently model sewer networks, storm-drain inlet capacity, infiltration, detailed curbs/walls, building-entry flooding, river-stage boundaries, or spatially varying forecast rainfall.

The proposed product specification requires these omissions to be clearly visible to users. In particular, v0.1 intentionally ignores infiltration and sewer/drain capacity, and treats roof rainfall using mass-conserving redistribution rather than detailed building drainage.

At 1 m resolution, DEM uncertainty and urban geometry can materially affect results. Grid spacing is not vertical accuracy.

## Documentation

- product specification draft (canonical English): `docs/PRODUCT_SPEC_DRAFT.md`
- product specification draft (Japanese reference translation): `docs/PRODUCT_SPEC_DRAFT.ja.md`
- implementation specification template (canonical English): `docs/IMPLEMENTATION_SPEC_TEMPLATE.md`
- implementation specification template (Japanese reference translation): `docs/IMPLEMENTATION_SPEC_TEMPLATE.ja.md`
- data acquisition: `docs/data_download.md`
- primary references: `docs/references.md`
- Codex validation: `CODEX_TEST_PLAN.md`

## License

No license has been selected yet. Choose an appropriate license before broad redistribution. External engine and data-source licenses/terms must be handled independently as described in the product specification.
