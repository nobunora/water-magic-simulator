# Water magic variant specification

Water magic defaults to a ±100 m (200×200 m, 160,000 half-metre cells) analysis domain, including New Analysis; saved-result loading preserves its original domain. Short-cast output prefers 0.2 s, then 0.5 s or an integer divisor to preserve the 600-frame budget and exact casting/end boundaries. Casts >=300 s still prefer 10 s. Solver maximum step remains 0.01 s. SFINCS solves on 0.5 m cells, subdividing each former 1 m cell into 2×2 independent hydraulic cells. Vector and particle display density uses the original zoom-dependent spacing only. Native 0.5 m hydraulics are retained; the bounded native velocity field is requested only for particle display.

Latest maximum-value instruction: all 16 presets select their estimated maximum volume. Nami uses forward length 7 m / transverse width 30 m; FFIII Tsunami uses 15 / 20 m. Tales Tidal Wave uses a finite 9.8 m radius circle, not the analysis domain. Dimensions and area display one decimal while calculation and latitude/longitude precision are preserved. See [range evidence and assumptions](water-magic-range-estimates.md).

> Status: User approved implementation of all 16 catalog entries as explicit water-deposition approximations, with game icons and per-spell GIF assignments.
>
> Latest visual instruction (2026-10-03): extract the actual spells from official sites/stores or YouTube. The local review packages 16 individually reviewed gameplay GIFs, with source links, cut times, publisher/uploader credits and version/combination caveats. This replaces the earlier similar-effect-only assignments. Copyright is retained; this local-review instruction does not establish release redistribution permission.
>
> Contract shape: standard
>
> Selection camera behavior: every magic-list click, including reselecting the current entry, fits the effect range with a short transition. Whole-domain spells fit the analysis area; other shapes use an anchor-centered enclosing range. Closer zoom enlarges existing map imagery, without changing physical dimensions or configuration. Manual navigation and parameter edits do not request refocus.
>
> Canonical file/language: English; `water-magic-variant-spec.ja.md` is the Japanese reference.
>
> Parent/source-of-truth: existing v0.1 product and UI specifications apply to terrain, grids, execution and native results. This contract overrides sample selection and forcing only in the water magic variant.
>
> Date: 2026-10-02 JST. Baseline: `3fea888773575b288033faa2f67e12ee645609cb`.

## Goal

Choose water magic, preview its placement and footprint on a real map, then simulate the injected water spreading across terrain during casting and a subsequent relaxation period.

UI mock added 2026-10-03: see [launch instructions and validation](../mockups/water-magic/README.md).
The existing React UI offers `/?mode=water-magic` (also the historical `/?mock=water-magic` alias), replacing city sample choices with all 16 spells while retaining its real map, domain controls and archive import.
The reviewed UI is now connected to Full 1 m water-source execution and archive persistence. See [implementation and verification](water-magic-implementation.md) for the executable scope, limits, second-based engine forcing and numerical approximation.
User-directed mock presentation update: each choice uses two lines, spell name with the estimated maximum volume (m³, one decimal place), then game name. Omit the quantity caption; preserve the underlying estimate range, sorting rule and configured-volume precision.
Latest user-directed workflow: show conditions/address search and editable original latitude/longitude inputs first, magic settings second; omit image controls. Selecting a spell autoplays its assigned GIF. Address selection or coordinate edits update the domain and selected spell location. Bearing is clockwise from true north; its arrow uses the analysis-overlay projection and a draggable handle appears within 60px. Footprints use black/blue lines; whole-domain display is inset by 5 CSS pixels without reducing physical area. Circle/domain geometry is invariant under bearing; directional rectangles/sectors rotate the actual source geometry. Result time labels use seconds for casting duration <300 seconds and minutes for casting >=300 seconds, excluding relaxation from the decision. Computation remains in seconds; short casts prefer 1-second output, long casts prefer 10-second output subject to the 600-frame budget. Earlier reference-only/GIF-control requirements below are superseded by this latest user-approved implementation contract.

## Scope

- A separate variant originating from the frozen main baseline; the initial reviewed one-spell UI and Full 1 m execution are implemented.
- Replace historical sample-condition selection with a magic catalog and shape-specific controls.
- Local nonnegative water injection with explicit volume, footprint and duration; existing terrain acquisition, progress and result viewing.
- Initial implementation proposal: one cast per run, stationary footprint, no background rainfall, Full 1 m execution. Other supported regular grids require separate validation; Adaptive remains disabled.

## Non-goals

- Modifying or merging into the original main product; changing GitHub branch protection or deploying a website.
- Inventing the spell catalog or attributing invented values to the linked discussion.
- Jet momentum, projectile flight, pressure damage, erosion, magic barriers, water removal, moving sources or simultaneous casts in the initial version.
- Assuming a relaxation period makes the solution reach equilibrium.

## Requirements / Invariants

- WM-01: preserve the exact baseline with tag `baseline/main-before-water-magic-2026-10-02`. Develop on `codex/water-magic-spec` in `C:/VSC/urban-pluvial-flood-simulator-water-magic`. This is a Git branch/worktree variant, not a separately created GitHub repository fork. The original checkout and main receive no feature edits. Subsequent main changes do not alter the recorded baseline.
- WM-02: catalog entries require stable ID, revision, Japanese name, description, illustration reference, footprint kind, supported controls, defaults, bounds, quantity definition, duration and approximation notice. Candidate membership is captured in the source catalog below; executable membership and defaults require approval. Preserve estimated ranges and evidence categories instead of treating the discussion as official game specifications.
- WM-03: selecting a spell initially places its anchor at the current visible map center. Persist that location; panning/zooming must not silently move it. Provide explicit “place at map center” and position adjustment. Analysis-domain geometry and casting geometry are separate.
- WM-04: render a per-spell animated GIF overlay and the actual injection footprint as distinct map layers. Obtain visually similar GIFs from the Web; exact game footage is optional and opaque backgrounds are acceptable. Illustration scale cannot determine injection area. Keep terrain and analysis boundary visible. Show a direction arrow where applicable. A simple symbolic illustration is allowed when an asset is unavailable, with an accessible text label. The GIF overlay requirements below govern acquisition, display and acceptance.
- WM-05: shape controls must have numeric equivalents: disk radius; sector radius/opening angle/bearing; oriented rectangle length/width/bearing; polygon vertex edit/undo/reset. These are supported geometry proposals, not a confirmed spell list. Show only controls applicable to the selected spell. Distances use metres; bearing is clockwise from true north, independent of map rotation. A rectangle starts at its anchor and extends along its bearing, centered across its width.
- WM-06: display total injected volume in m³, duration in seconds, average discharge in m³/s, effective injection area in m² and equivalent source depth/rate. A catalog expressed as a rate must resolve to a volume before execution; volume is not silently scaled when footprint changes.
- WM-07: use nonnegative, spatially local forcing; zero outside support and after casting ends. Do not substitute domain-wide average rain. The implemented engine adapter uses constant discharge followed by a terminal linear decrease over `delta=min(0.1 s, T_cast/100)` and normalizes by `T_cast-delta/2` to preserve the requested volume. This declared approximation adjusts the plateau by at most 0.503%; it never injects during relaxation. See the implementation contract for eligibility and dry initial state.
- WM-08: simulation end is `T_end = T_cast + T_relax`, with positive finite casting duration and nonnegative finite relaxation duration. Show and persist both durations and their sum. Relaxation is an editable fixed observation period in the initial proposal; no unvalidated automatic equilibrium rule.
- WM-09: freeze all inputs and the resolved footprint when execution starts. Changing setup or selecting a spell invalidates an older preview. Returning to setup retains editable values; rerunning creates a new run identity.
- WM-10: in results distinguish casting from relaxation, keep the footprint available as a toggle, and reuse native depth/velocity/timeline/point inspection. Maximum results cover the entire simulation. Never depict preview artwork as calculated inundation.
- WM-11: persist catalog revision, resolved values, coordinates/CRS, geometry, forcing profile/hash, source-volume report, baseline, grid and engine provenance. Imported magic runs restore placement without substituting current defaults. Legacy rain archives remain identified as rain results.
- WM-12: short spells require a new verified forcing/output-time policy. Preserve observations at start, casting end and simulation end, and enough samples during casting to resolve its effect. Any engine quantization must be displayed before execution; do not silently round a subminute spell to one minute.
- WM-13: all 16 catalog entries require an explicit GIF asset assignment and representative still. Similar generic animations may be shared across spells with an explicit mapping; the same character name alone is not evidence of visual suitability. Reference-only spells may still preview their GIF while analysis remains disabled.
- WM-14: GIF playback is decorative, independent of hydraulic time, duration, volume and geometry. Provide show/hide and play/stop controls; stop uses the representative still, not CSS animation pause on an animated `<img>`. Reduced-motion preference starts on the still. Do not claim GIF frame seeking or simulation-time synchronization in the initial version.
- WM-15: list spells in ascending estimated total volume, normalized to m³. For estimate ranges sort by lower bound, then upper bound, then stable spell ID. Keep the range visible and explain the lower-bound ordering. Entries without a finite estimate follow quantified entries; missing quantities are never zero. Changing a run's configured volume does not reorder its catalog entry.
- WM-16: place the game's identifying icon before the choice text. The icon spans the two text lines (spell name and game name), preserving aspect ratio; initial layout is a 48 CSS px square beside two 24 px lines. Keep the volume range separately readable. Provide game-name alt text, visible game text and a game abbreviation fallback. Final game icons need source/usage records; the one-spell mock uses an explicitly temporary GW2 badge.

## Affected Interfaces / Contracts

Current baseline evidence:

| Boundary | Observed constraint | Required review |
| --- | --- | --- |
| `web/src/dev/SmokeApp.tsx` | “サンプルまたは読込み”, historical ranking and sample dialog | Replace catalog/selection in variant; retain separately labelled archive import |
| `floodsim/domain/run_config.py` | Required `rainfall` discriminated union; extra fields forbidden | Versioned magic configuration; do not insert unsupported fields into legacy schema |
| `floodsim/domain/rainfall.py` | `meteorological_spatial_mode` is `uniform`; constant rain duration is integer minutes and intensity <=500 mm/h | Separate local source and second-based time contracts; do not reuse rainfall limits as magic limits |
| `floodsim/orchestration/rainfall_resolution.py` | Resolves temporal rainfall profiles | Separate casting end from total simulation end |
| `floodsim/sfincs/model_builder.py` | Builds `precip_2d(time,y,x)` from temporal rate times `grid.rain_weight`; writes `sfincs_netampr.nc` | Confirm engine interpolation, zero cutoff, active-cell placement and roof redistribution semantics |
| same builder, `derive_output_interval_seconds` | Existing output policy has a whole-minute lower bound | Validate short-duration source/output support before Ready |

These observations are source reads, not proof that the new forcing works in the real engine. Production code is unchanged in this draft. Exact API names, schema version and affected tests belong to subsequent repository review. Optional query helpers are not prerequisites.

## Approved Tools / Implementation Constraints

Use existing map, backend, grid and SFINCS boundaries where validated. No new dependency is approved by this draft. Before code changes, perform targeted CodebaseMemory CLI queries when available, verify against source, and stop operation-owned processes afterwards. Do not enable persistent MCP registration or refresh the graph for this documentation-only change.

## Inputs and Outputs

Proposed logical inputs (not yet public API field names): catalog ID/revision; anchor WGS84 longitude/latitude; geometry; total volume; casting seconds; relaxation seconds; grid/domain settings. Project geometry to the run's metre-based CRS before area calculations; geographic degrees are never metres.

Define eligible cell source areas `a_i` by intersecting the footprint with water-accepting computational cells. Let `A = sum(a_i) > 0`, cell area `A_i`, volume `V`, and constant discharge `Q = V / T_cast`. Then cell source depth rate is `r_i = Q * a_i / (A * A_i)` m/s; precipitation-equivalent rate is `r_i * 3,600,000` mm/h. Hence `sum(r_i * A_i) = Q`. Partial-cell intersections must be retained; a footprint smaller than a cell must not vanish through center-point rasterization.

The backend is authoritative for eligible areas, CRS and volume validation. Initial policy proposal: reject footprints extending outside the domain rather than clipping silently. Inactive/impermeable source locations must report requested and eligible area and the redistribution policy before execution; explicit confirmation of any changed effective footprint is required. Roof routing must not duplicate or lose water. A wholly ineligible footprint is rejected. The eligibility/routing policy is an unresolved readiness gate.

Persist geometry, source values, casting cutoff, zero-source relaxation, generated forcing checksum, expected and actual integrated source volumes. Result outputs retain native result contracts, additionally carrying magic metadata and phase boundaries.

## State / Normal Flow

`SETUP -> MAGIC_SELECTED -> PLACEMENT_EDITING -> READY -> RUNNING -> RESULT`, with failed/canceled runs reported using existing orchestration states. These are logical UI states, not proposed new backend enum values.

1. Choose map/domain and a spell.
2. Place at current map center; preview artwork and injection footprint.
3. Adjust position, direction/range, volume and durations; backend validates effective footprint and budget.
4. Confirm the visible resolved parameters and start analysis.
5. Build localized forcing, run casting plus relaxation, then open native results.
6. Replay actual output times; inspect water depth/velocity and casting/relaxation phase.

## Errors / Fallbacks / Stop Conditions

Reject NaN/infinite/negative values, nonpositive volume/duration, invalid polygons, out-of-domain footprints, zero eligible area, unsupported catalog revisions and run budgets exceeding verified limits. All limits must be declared in the catalog/engine capability contract before Ready.

Missing artwork permits a labelled symbolic preview. Missing forcing support, excessive source rates or incompatible time sampling must block execution with an actionable explanation, never substitute uniform rainfall or clamp the quantity. Boundary overflow/loss and numerical failure must be reported. Unresolved geometry, interpolation, roof routing or physical semantics require `spec-change-required` before implementation readiness.

## Physical / Numerical Assumptions

Magic is approximated as addition of water mass to a shallow-water terrain solver. Direction rotates the injection footprint; it does not inject horizontal momentum. Magic requiring jet momentum cannot be labelled physically reproduced by this model. Infiltration, roughness, roof handling and open/closed boundaries use explicitly recorded model settings; changing them requires separate review. Relaxation may retain pooled water, particularly with closed boundaries.

Time discretization must conserve the prescribed source volume across casting cutoff: inspect the generated file and real engine behavior, since endpoint sampling alone does not prove zero tail or step semantics. Do not adopt an epsilon cutoff without an error budget. Grid resolution, very large local depth/rate and forcing-memory growth require budget checks and refinement/convergence evidence. Sparse or factored forcing is allowed only if physically equivalent to the declared source.

## Acceptance Criteria

- AC-01: baseline tag resolves to the recorded main SHA; isolated variant differs only by specifications at this stage.
- AC-02: spell selection places artwork and footprint at map center; map navigation preserves location; explicit recentering moves it. Numeric direction/range agrees with map handles, including a rotated map.
- AC-03: invalid geometry/values and unsupported physical spells cannot execute; missing artwork leaves an operable labelled preview.
- AC-04: requested/generated source volume relative error <=`1e-6` in float64 geometric/time integration; serialized engine forcing <=`1e-4`. Outside support and throughout relaxation the defined source is zero. Exact integration must use the verified engine interpolation convention.
- AC-05: source-only, flat, closed, no-loss test conserves final stored water to <=1% of injected volume; a zero-source control produces no water. These are proposed acceptance tolerances requiring repository review, not passed results.
- AC-06: disk, directional sector/rectangle and partial-cell fixtures prove area/rotation; invalid/outside/ineligible cases prove rejection or explicit routing disclosure. Engine-backed runs show terrain-driven spreading and no unintended relaxation injection.
- AC-07: a proposed short-duration fixture (5 seconds casting plus 60 seconds relaxation) observes casting end and subsequent spread without minute rounding; if unsupported, this blocks Ready and requires revising capability/scope.
- AC-08: export/import reproduces resolved placement, quantity, durations and catalog revision. Legacy rain results retain their identity. Native depth/velocity inspection and all existing retained result controls remain functional.
- AC-09: every catalog entry resolves to a packaged GIF and still with traceable source, suitability review, attribution/redistribution status and file metadata. Test one opaque-background asset and all shape categories. Local packaged GIF previews work without contacting their original host; online basemap/provider availability is a separate concern.
- AC-10: browser flows prove selection/replacement, center placement, pan/zoom retention, direction adjustment, opacity/size, show/hide, play/stop, reduced motion, missing/invalid asset fallback and unobstructed map gestures. At most one selected GIF animates; animation cannot alter forcing or result state. GIF preview is not advertised as accepted until assets and these checks exist.
- AC-11: multi-entry catalog fixtures prove unit-normalized lower-bound/upper-bound/stable-ID ordering and missing-estimate placement. Browser inspection verifies the game icon precedes text and spans two lines without obscuring text/volume on desktop and narrow screens. The initial one-spell mock demonstrates layout/interaction, not multi-entry product acceptance.

## Validation

This iteration: documentation structure, links, baseline identity, bounded diff and `git diff --check`; no hydraulic or browser-product acceptance is claimed.

Before Ready: verify material game claims against primary sources, approve executable catalog and limits, settle source eligibility/roof handling, query relevant symbols/callers/tests on demand, and verify pinned engine forcing/time behavior against primary documentation and a tiny real-engine experiment.

Before implementation acceptance: applicable independent quality checks before focused tests; geometry/time/mass unit tests; generated forcing inspection; API/schema/archive integration; tiny real SFINCS source-only and terrain runs; browser selection/placement/direction/timeline/return/import flows; regression of retained results. Use the canonical `urban-pluvial-flood-phase0` interpreter for Python checks. Record exact versions, SHA, commands, exit status and artifacts. Full-suite/build/release gates follow repository policy when implementation exists.

## Completion Criteria

- [x] Discussion content and candidate spell definitions are captured by reading the user-opened Chrome conversation.
- [ ] Executable spell definitions, evidence and preset revisions are approved.
- [ ] Product defaults, bounds, relaxation presets and source eligibility are decided.
- [ ] Engine localization, cutoff, output times and volume acceptance are demonstrated.
- [ ] Repository review resolves schema/UI/result compatibility and defines implementation tasks.
- [ ] User review accepts the resulting contract; then status may become Ready.
- [ ] Per-spell GIF assignments, redistribution evidence, packaged assets/stills and browser acceptance are complete before feature acceptance.

Creating this draft completes the present documentation task; the checklist describes future implementation readiness.

## Required Repository Tools

`git`, `rg`, repository-native documentation checks. CodebaseMemory CLI only for necessary future code investigation; no persistent server. Real engine and browser are required for eventual product acceptance, not for creating this draft.

## Risks / Rollback

Concentrated volume may cause high local depth, small stable timesteps and large forcing arrays. Unverified interpolation may leak source into relaxation. Mask/roof transformations may change effective placement. Check each before Ready. Keep the original variant and baseline independently addressable; investigate defects before considering rollback. No deployment or original-product migration is included.

## Open Questions

1. The user-opened Chrome conversation was successfully read after initial public retrieval/login and new-tab timeout failures. Which candidate entries and estimated values should become executable presets? No table value is yet an approved default.
2. Are any agreed spells momentum-driven, moving, removing water or barrier-producing, requiring another model/scope?
3. Which catalog presets/bounds and relaxation defaults should be offered?
4. What source placement/roof routing policy preserves both the intended footprint and mass?
5. Does the pinned engine faithfully support the proposed short-time forcing/output contract, and what validated execution limits apply?
6. Does “fork” require a separately named GitHub repository in addition to the branch/worktree created here? Destination/ownership/name were not specified; no new GitHub repository is created by this draft.

## Source Catalog and Conversion Policy

Source: [ゲーム水魔法調査表](https://chatgpt.com/c/6abdb38d-8f90-83e9-9cfe-af76e33518cc), user-opened Chrome tab read on 2026-10-02 JST, including the follow-up equivalent-depth table. The conversation describes water volumes as visual/physical estimates, not official quantities. Durations, dimensions and game mechanics below are discussion claims, not independently verified facts. Preserve that evidence status in the UI and saved catalog. Years label the discussed games/versions, not independently established release metadata.

| Candidate ID | Game / spell | Estimated V (m³) | Discussed time | Discussed effective area (m²) | Variant treatment proposal |
| --- | --- | --- | --- | --- | --- |
| ff3-tsunami | FFIII / Tsunami | 10–100 | 1–3 s effect | 150–300 | Directional swept-footprint deposition; wave momentum omitted |
| warcraft1-elemental | Warcraft I / Water Elemental | 0.2–0.8 | Until defeated | 0.5–1 | Optional collapse-to-water scenario; finite release duration must be specified |
| chrono-water2 | Chrono Trigger / Water II | 20–80 | 2–4 s effect | 100–200 | Fixed battlefield footprint deposition |
| ff7-tidalwave | FFVII / Leviathan Tidal Wave | 1,000–10,000 | 5–10 s effect | 500–2,000 | Directional swept footprint; extreme depth/budget gate |
| ro-waterball5 | Ragnarok Online / Water Ball Lv.5 | 0.84–4.5 | 5 s casting plus impact sequence | About 25 | Target deposition approximation; impact timing unresolved, not 5 s injection by assumption |
| warcraft3-elemental | Warcraft III / Water Elemental | 0.3–1 | 60 s entity lifetime | 0.8–1.5 | Optional collapse scenario; entity lifetime is not water generation duration |
| tales-tidalwave | Tales of Symphonia / Tidal Wave | 50–200 | 2–4 s effect; about 7 s casting | 150–300 | Fixed battlefield deposition; exclude casting delay from release duration |
| gw2-healingrain | Guild Wars 2 / Healing Rain | 0.039–0.117 | 6 s rainfall | About 468, radius about 12.2 m | Disk rain; strongest initial temporal/geometry candidate |
| lol-nami-wave | LoL / Nami Tidal Wave | 10–40 | 3.267 s travel | 100–210 | Oriented strip deposition; game units lack verified metre conversion |
| dos2-rain | Divinity: Original Sin 2 / Rain | 1.6–6.3 | 6 s rain, puddle persists | About 314, radius about 10 m | Disk rain; confirm radius/diameter discrepancy |
| kh3-waterga | Kingdom Hearts III / Waterga | 0.5–5 | 1–2 s effect | 3–13 | Target disk deposition; homing/barrier mechanics omitted |
| genshin-mona | Genshin / Mona Illusory Torrent | 0.05–0.2 instantaneous water | Stamina-dependent | 1–2 | Optional one-time local release; no repeated generation from moving film |
| genshin-neuvillette | Genshin / Equitable Judgment | 7.5–60 integrated passage | 3 s | 4–8 irradiation strip | Oriented deposition approximation; jet momentum and moving aim omitted |
| forspoken-cataract | Forspoken / Cataract | 10–100 | Several seconds, unspecified | 28–79 | Disk/sector deposition; vortex and upward/forward momentum omitted |
| bg3-createwater | Baldur's Gate 3 / Create Water | 0.25–1 | Brief rain; Wet status 3 turns | About 50.3, radius 4 m | Disk generation; numeric release seconds required, do not convert Wet turns to rain duration |
| wow-elemental | WoW / Water Elemental (discussion's 2026 version) | 0.4–1.5 | 1.5 s casting; no fixed lifetime given | 1–2 | Optional collapse scenario; casting is not release duration |

All 16 entries may be listed for exploration, but unsupported entries must be marked reference-only and cannot execute without a validated deposition contract. This proposal allows water-volume approximations for directional spells rather than silently removing them from the catalog; it does not broaden the initial model to moving sources or momentum injection.

The same conversation supplies area-based equivalent depth. Recompute it from the actual chosen inputs instead of copying rounded extrema: `h_eq_mm = 1000 * V / A`; `Q_L_per_s = 1000 * V / T_cast`; `q_L_per_s_per_m2 = 1000 * V / (T_cast * A)`. `1 L/m² = 1 mm`. Display these beside quantity, area and duration. Label equivalent depth as cumulative supplied water per area, distinct from calculated terrain water depth. For beams, integrated passage is not instantaneous stored volume; for summoned entities, body volume is not continuous production.

For a source range `[V_min,V_max]` and independently variable area `[A_min,A_max]`, the bounding depth range is `[1000*V_min/A_max,1000*V_max/A_min]`; do not imply endpoints are correlated or precise. Approximate geometry and water volume remain separate uncertainty fields. Do not select a midpoint automatically as an approved preset or interpret estimate ranges as numerical safety limits.

Conversion checks for eventual tests: GW2 `V=0.039 m³,A=468 m²,T=6 s` gives `h≈0.08333 mm,Q=6.5 L/s`; BG3 `V=0.25 m³,r=4 m` gives `h≈4.97359 mm`; Neuvillette `V=60 m³,A=4 m²,T=3 s` gives `h_eq=15000 mm,Q=20000 L/s`, explicitly not a predicted 15 m flood depth. Retain full precision internally and round display only.

## Per-spell GIF Overlay

Added 2026-10-03 JST by user request. This iteration adds specification and Web-discovered candidates, not downloaded binary assets or implemented UI. Exact game footage and transparent cutouts are not required. Candidates are discovery leads; title/page availability does not prove animation quality or permission to redistribute.

### Display and lifecycle

Place the selected GIF at the persisted spell anchor (initially current visible map center). Use a screen-sized image marker/card, default longest edge 240 CSS px, adjustable 120–480 px, default opacity 0.65, adjustable 0.2–1.0, preserving aspect ratio with `contain`. Keep the visible image within available viewport space on small screens. An opaque rectangular background is valid; background removal, chroma key and blend modes are not prerequisites. Label the overlay as an illustrative animation and show the spell name.

The GIF remains anchored while panning/zooming but its screen dimensions are independent of metre-based injection geometry. Render the actual footprint and direction arrow above the GIF. Decorative image content does not consume pointer/touch gestures; controls live in the setup panel. Directional artwork may rotate only when its manifest declares an actual forward axis; otherwise keep the GIF upright and rotate the authoritative footprint/arrow. Do not guess a GIF's direction from its filename.

Selection starts looping the chosen GIF unless reduced motion or the user's stopped state applies; replacing a spell disposes of the prior image. The default is animation visible in setup; starting analysis stops it to a still, preserving the footprint. Result view defaults the GIF off and allows an explicitly decorative preview toggle. Do not tie its loop count to supplied water or use its native animation length as `T_cast`. No audio, autoplay of an entire gallery, or retained hidden GIF decoder after hide/replace. Show/hide removes the animated element; play/stop swaps GIF/still resources. This permits reliable stopping without adding a frame decoder dependency.

### Acquisition and persistence

Prefer downloadable GIFs whose recorded conditions permit bundling in the application. Search the Web for each spell's effect family (rain, wave, flood, water sphere, water body, beam or vortex); a generic similar clip is acceptable and must be labelled as such. Shared clips reduce package size but each spell has an explicit assignment. Do not package external embed scripts/iframes or make the deployed preview depend on a third-party GIF host.

Proposed asset location: `web/public/magic-gifs/`, manifest alongside it; exact schema/API naming requires repository review. Each record carries asset ID/revision, spell IDs, source detail-page URL, original media URL, creator/credit, acquisition date, recorded license/permission, permitted redistribution, local GIF/still paths, SHA-256, bytes, dimensions, frame count/loop duration, opaque/transparent background, optional forward axis and suitability status. Preserve required credits in application credits and release materials. A site's availability is not a redistribution grant; unresolved permission remains candidate-only and prompts a similar distributable replacement.

Initial asset budget proposal: packaged longest edge <=640 px, <=10 MiB per GIF, <=60 MiB unique GIF total, with one selected animation decoded at a time. Verify actual frame count, decode cost and Windows/browser memory before approving the budget. Preserve the original source and conversion settings if optimizing; derivative permission must cover optimization. Do not silently replace a requested GIF with MP4/WebM. A page exposing only video remains a candidate until lawful GIF acquisition/conversion is verified. The original rain GIF below fits the per-file byte budget but still needs display/performance review.

Loading/download failure, corrupt media or missing packaged file uses the still or labelled symbol without blocking analysis. The network timeout cannot leave a blocking spinner. Exported runs save the asset ID/revision, not executable embed code; archives may resolve to a fallback if their asset is unavailable.

### Web-discovered candidate references

| Ref | Detail page | Proposed effect | Evidence / unresolved work |
| --- | --- | --- | --- |
| G-R | [Rain 2.gif, Shisma](https://commons.wikimedia.org/wiki/File:Rain_2.gif) | Rain with visible natural background | Page identifies GIF, 640×480, 36 frames, 2.7 s, 9,725,792 bytes and CC0; download/visual review pending |
| G-W | [FF16 Leviathan](https://tenor.com/view/ff16-ff16-leviathan-leviathan-gif-14897385901495178633) | Large water/summon candidate | Page describes a creature in water, 470×266, 9.3 s; verify it actually shows a useful wave, otherwise replace; redistribution unresolved |
| G-B | [Shape Water / Water Ball](https://tenor.com/view/shape-water-water-ball-magic-gif-11983037) | Water-sphere / magic body proxy | Page offers GIF, 498×249, 2 s; review motion and redistribution, not an exact spell depiction |
| G-V | [Blackhole / Whirlpool / Water](https://tenor.com/view/blackhole-whirlpool-black-hole-water-gif-5915936) | Water vortex proxy | Page describes water at a circular opening, 498×280, 3.9 s; suitability/redistribution pending |
| G-J | [Neuvillette / Abyssal Current](https://gifs.alphacoders.com/gifs/view/220968) | Directional water-stream candidate | Detail page exists; verify GIF bytes, actual beam action, direction and redistribution before adoption |
| G-F | [Water Magic, Hempions](https://giphy.com/gifs/magic-stoptime-hempions-jnVfGp2ThvvYz7YBYx) | Waterfall/flood-flow proxy | Page tags water/magic/waterfall; review actual frames, download format and redistribution |

Metadata above is discovery evidence from 2026-10-03, not a substitute for validating acquired files. Avoid assigning unrelated character/reaction GIFs just because search matches a spell name.

| Spell ID | Candidate ref | Intended appearance / caveat |
| --- | --- | --- |
| ff3-tsunami | G-W | Large wave candidate; replace if clip only depicts a summon |
| warcraft1-elemental | G-B | Temporary water-body proxy; prefer a humanoid water animation |
| chrono-water2 | G-F | Flood-flow proxy |
| ff7-tidalwave | G-W | Wave proxy from another game; not labelled as FFVII footage |
| ro-waterball5 | G-B | Sphere proxy; not an assertion of 25 visual projectiles |
| warcraft3-elemental | G-B | Temporary water-body proxy; prefer humanoid water |
| tales-tidalwave | G-F | Flood-flow proxy |
| gw2-healingrain | G-R | Rain proxy with background |
| lol-nami-wave | G-W | Wave proxy; numeric direction comes from the footprint |
| dos2-rain | G-R | Rain proxy |
| kh3-waterga | G-B | Water-sphere proxy |
| genshin-mona | G-B | Temporary water-body proxy; seek flowing water-film motion |
| genshin-neuvillette | G-J | Beam candidate; confirm correct action rather than idle animation |
| forspoken-cataract | G-V | Water-vortex proxy |
| bg3-createwater | G-R | Rain proxy; independent of Wet status duration |
| wow-elemental | G-B | Temporary water-body proxy; prefer humanoid water |

The table is an acquisition worklist covering every spell, not final artwork approval. Replace unsuitable candidates before acceptance; shared placeholders cannot be presented as completed per-spell visual verification. Reference-only physics status does not prevent an illustrative GIF preview.


## 2026-10-03: 最新のカタログと結果表示

利用者の追加指示により、最大水量5 m³以下を選択肢から除外。残る8件と追加6件の14件を最大水量の昇順、同量はID順に並べる。旧IDは保存結果の互換性のため保持する。DQVIIのメイルストロムを初期選択とする。全選択肢に実ゲーム映像の自動再生GIFと作品アイコンを登録する。推定値は公式体積ではない。以前の下限優先順・全16件選択の記述を置き換える。[推定根拠と水深高速化](water-magic-volume-expansion.md)を参照。水深専用経路は選択した実出力時刻のhだけを読み、固定色分けを再利用する。


## 2026-10-04: Initial-only momentum and vortices

Per the user's new instruction, initial placement supplies all water and horizontal velocity once. Motion options are still, directional, radial and vortex, with editable handedness and core radius. Speed is0–4m/s. No continuing momentum or water source follows. Earlier exclusions of momentum now apply to continuous injection only. Old archives restore continuous water and zero initial speed. See the [additional contract and validation](water-magic-initial-momentum.md).


## 2026-10-04: Pool, kaiju and full initial placement

Add a 300 m³ school pool, an inferred maximum 100000 m³ Godzilla landing wave and an inferred maximum 23000 m³ Titanosaurus vortex. All 17 selectable presets, including Rain, default to placing the entire volume at time zero with no subsequent source water. New Analysis defaults to initial placement; saved legacy results retain their original release mode. This supersedes the previous 14-preset count and continuous Rain default. Sources, inference limits, bundled assets and native 0.5 m engine/browser evidence: [additional contract](water-magic-pool-kaiju.md).

## 2026-10-04: Top-ten depth and speed navigation

Depth and speed buttons independently cycle up to ten distinct native locations and jump to each retained peak output time. Point details show rank and kinetic-energy-equivalent speed for a Prius or sumo wrestler, in km/h. Source-backed ranking uses bounded frame/chunk reads and persistent summaries. [Contract and real-data/browser validation](result-extrema-navigation.md).
