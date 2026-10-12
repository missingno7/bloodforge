# BloodForge

An independent Python 3.12 project for evidence-driven Blood MAP v7 analysis
and construction. The first working slice indexes the real local corpus,
discovers strict rectangular stair runs, infers parameter bounds from training
maps, reconstructs held-out fragments, and records native, structural, visual,
and runtime evidence separately. No LLM is required by the library or CLI.

Spatial Understanding v1 adds source-backed continuity hypotheses, aperture and
negative-space measurements, static viewpoint sequences and controlled edits
on original maps. It separates sectors from possible perceived spaces, and
renderer observations from navigation and design interpretations. See the
[spatial evidence checkpoint](docs/spatial-understanding-v1.md).

This is a foundation, not an autonomous full-level designer. See
[the first milestone](docs/milestone.md), [surface reconstruction checkpoint](docs/surface-checkpoint.md), [architecture](docs/architecture.md),
and [task ledger](docs/tasks.md).

## Run from this checkout

The PowerShell launcher uses `BLOODFORGE_PYTHON`, the checkout's `.venv`, the
locally bundled Python 3.12 runtime when available, or `python`. It does not
install dependencies. Install `pip install -e ".[spatial,visual]"` in your Python
environment for spatial geometry and image comparisons (Shapely and Pillow).
Run commands from the repository root:

```powershell
.\tools\bf.ps1 init
.\tools\bf.ps1 corpus ingest
.\tools\bf.ps1 corpus inspect
.\tools\bf.ps1 corpus search E2M5 --limit 5
.\tools\bf.ps1 corpus duplicates --kind geometry
.\tools\bf.ps1 patterns mine --population blood-campaign
.\tools\bf.ps1 patterns search --population blood-campaign --limit 5
$recipe = .\tools\bf.ps1 recipes infer | ConvertFrom-Json
.\tools\bf.ps1 recipes evaluate $recipe.recipe_id
.\tools\test.ps1
```

## Inspect spatial relationships

The spatial commands accept original irregular maps, independently of the
rectangular construction compiler. Select native sectors as an explicit scope;
derived groups may overlap across continuity views and are not room labels.
An explicit selection can be inspected when unrelated loops or portals fail
structural checks. Native records and complete unique wall ownership must still
pass globally; selected polygons and every incoming/outgoing boundary portal
must pass locally. `scope_validation` retains the whole-map errors, and global
connectivity is withheld when that gate fails. Omitting `--sectors` retains the
strict whole-map gate. This does not repair or validate the rest of the source.

```powershell
$map = 'D:\Games\DOS\llmapper\maps\blood\campaign\E1M4.MAP'
.\tools\bf.ps1 space inspect $map --sectors '156,335,333,314,152,210'
.\tools\bf.ps1 space transitions $map --sectors '156,335,333,314,152,210'
.\tools\bf.ps1 level observe $map --cameras examples\spatial-e1m4.cameras.json --output data\spatial-e1m4-new
.\tools\bf.ps1 space inspect $map --sectors '156,335,333,314,152,210' --observation data\spatial-e1m4-new\summary.json --diagram data\spatial-e1m4.svg
```

`space viewpoints` samples a JSON waypoint sequence and rejects invalid or
ambiguous camera positions. Explicit sector hints are required around some
overlapping Build geometry. It validates sampled poses, not movement between
them. `space intervene` takes a JSON object with `expected_source_sha256` and
`edit`, creates a content-addressed copy and audits every changed native field.
`space compare` pairs maps with preserved indexed XY loops and optionally
`--before-views` / `--after-views` observer summaries. Reports retain native refs,
assumptions and limits; no visibility is inferred from adjacency.

Replay the three campaign examples, four frozen counterfactual predictions and
the retained BLUDTOWN diagnostic selection:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe tools\spatial_demo.py --output data\spatial-v1-fresh
```

The output must be a fresh directory. `report.html` contains source-indexed
top-down diagrams and controlled frames; `report.json` links the full evidence.
`--reuse-recon` and `--reuse-run` can reuse earlier observer files only after
rechecking live MAP, executable, resource, manifest, pose and image identities.
The experiment plan is [spatial-interventions.json](examples/spatial-interventions.json).
View reports distinguish visible sectors inside and outside the spatial scope.
BLUDTOWN remains unsupported as a whole map and is now a diagnostic re-evaluation,
not an unseen holdout result. Its two poses are separate static samples, not a
verified movement route. The bot is not used.

`demo` performs ingestion, campaign mining, recipe inference and held-out
evaluation in order. It returns compact JSON plus a content-addressed full
report. Progress goes to stderr. Repeating it reuses native extractions and
mining results. Recipe/benchmark inputs and parameters are deterministic.

```powershell
.\tools\bf.ps1 demo
```

Copy `local.example.toml` to `local.toml` to override local resources. Without
configuration, the sibling `llmapper/maps/blood` is discovered read-only.
Global `--config`, `--corpus-root`, and `--data-root` options precede commands.
Equivalent environment variables are `BLOODFORGE_REFERENCE_ROOT`,
`BLOODFORGE_CORPUS_ROOT`, and `BLOODFORGE_DATA_ROOT`.

For another Python environment: `python -m pip install -e .` exposes
`bloodforge` and `python -m bloodforge`. There are no runtime third-party
dependencies for the core. Paired pixel comparisons use optional Pillow:
`python -m pip install -e ".[visual]"`. A wheel can be built offline with a local setuptools/wheel
installation using `python -m pip wheel . --no-deps --no-build-isolation
--no-index --wheel-dir data/wheels`.
For repeatable wheel timestamps, set `SOURCE_DATE_EPOCH=1767225600`; the measured
build used Python 3.12.14, setuptools 84.0.0 and wheel 0.48.0, and two builds
produced the same hash. See the milestone report for that hash.

## Evidence and populations

Every physical path has provenance; byte-identical maps share one extraction.
Tiered copies are views and do not provide additional support. Tier labels are
sampling metadata, not quality or confidence weights. Query population names:

| Population | Meaning |
| --- | --- |
| `blood-campaign` | Original single-player campaigns |
| `blood-bloodbath` | Original multiplayer maps |
| `community-curated` | Owner-curated community sources |
| `community` | General community maps, preserving tier metadata |
| `mechanism-tutorial` | Tutorial/showcase population, including Modern/Vanilla distinctions in paths |
| `own-conversion` | Existing cross-game conversions; excluded from construction evidence |
| `generated` | Generated/reconstructed files; excluded from construction evidence |
| `unclassified` | Unknown provenance; excluded from construction evidence |

`patterns mine --population community` and `patterns mine --population
mechanism-tutorial` run separate studies. The default recipe uses only original
campaign maps. `original` combines the two original populations explicitly.

`corpus ingest --limit 100` commits a bounded checkpoint. Run `corpus ingest`
again to finish; unchanged source identities are reused. Per-map errors remain
queryable, including on subsequent passes whose `failed` count is zero.
`--retry-failed` retries cached failures. Changed extractor versions invalidate
their caches. A full completed scan deactivates removed paths but retains
historical content and provenance. Source hashes are rechecked on every scan.

## Author and edit a scene

```powershell
.\tools\bf.ps1 scene create my-stair --file examples\stair.scene.json
.\tools\bf.ps1 scene inspect my-stair
.\tools\bf.ps1 level compile --project my-stair
```

Compilation returns a MAP artifact path and semantic-to-native object map.
The source model accepts straight stairs, rectangular rooms/corridors, stair-end
landings, explicit connections, and an optional player start.
Stairs support material tile IDs and optional per-step plane/wall appearance.
Appearance includes shade, palette, texture scale/panning, visual flags,
visibility and a cardinal first-wall anchor for relative texture alignment.
It intentionally cannot represent a complete
gameplay level yet. `scene apply` takes a JSON list of `SceneChange` records,
an `--expected-revision`, and optional `--dry-run`; each record has operation,
object_id, expected, and value. Exact previous-value preconditions and native
compilation must succeed before a revision commits. `scene undo` appends a
new revision; `scene diff --before N --after M` reports semantic changes.

### Landings and connecting areas

`LandingSpec` names a stair, its `bottom` or `top`, and a depth. The compiler
derives its footprint, width, floor and ceiling and connects it to that stair.
`AreaSpec` describes a named flat rectangle with a `room` or `corridor` role.
`ConnectionSpec` joins two named boundaries, for example an area's `east` side
to a landing's `west` side. Stair endpoints use `bottom`/`top`. Touching geometry
does not open a portal without an explicit connection or landing relationship.

Connections open the positive overlap of opposing shared boundaries and require
matching floor levels. The compiler splits room walls for narrower openings,
preserving texture scale and panning where exactly representable. It rejects
overlap, duplicate portals, disconnected endpoints, unsupported texture splits,
and missing references. Disconnected groups are permitted as closed regions;
static adjacency is not a traversal claim. Overlapping XY interiors, including
stacked rooms, are outside this compiler's present scope.

```powershell
.\tools\bf.ps1 scene create connected-stairs --file examples\connected-stairs.scene.json
$level = .\tools\bf.ps1 level compile --project connected-stairs | ConvertFrom-Json
.\tools\bf.ps1 level validate $level.artifact_path
.\tools\bf.ps1 level observe $level.artifact_path --project connected-stairs --cameras examples\connected-stairs.cameras.json --output data\connected-stairs-observation
.\tools\bf.ps1 level test-load $level.artifact_path --output data\connected-stairs-load
```

The example generates both rooms, both landings and the corridor from semantic
source; it does not retain original-map surroundings. Camera `sector_object_id`
references are resolved through the project, with an exact compiled-MAP identity
check. Use a fresh observation directory when repeating the render. The load
smoke runs without the bot and does not verify player movement.
See [the recorded connected-area evidence](docs/connecting-areas-evidence.md)
for native counts, image identities, static connectivity and load results.

Scene changes now include `set_area`, `remove_area`, `set_landing`,
`remove_landing`, `set_connection` and `remove_connection`. Add or remove
dependent objects together in one atomic batch. `PlayerStart.area_id` can name
an area or landing; `stair_id` targets a stair. These fields are mutually exclusive.

## Independent observation and load checks

Use a MAP path returned by `recipes evaluate` or `level compile`:

```powershell
.\tools\bf.ps1 level validate <generated.map>
.\tools\bf.ps1 level observe <generated.map> --output data\my-observation
.\tools\bf.ps1 level test-load <generated.map> --output data\my-load-test
```

`level observe` launches the installed XMapEdit observer directly. A fresh
output directory is required so stale frames cannot pass. `--cameras` accepts
a JSON array with `id,x,y,z,angle,horiz,sector,screenshot`; otherwise the MAP
header's start pose is used. Original source MAPs may be rendered read-only.
The wrapper verifies input hashes and records native object visibility.

`level test-load` uses NBlood for a bounded hidden initialization smoke, with
private asset staging and configs under the data root. It does not press keys,
assert player traversal, or validate mechanisms. Both commands accept
`--executable` and `--assets`; defaults resolve installed tools in the reference
tree. Missing tools or failed checks never count as passes. Reports and PNGs
are retained in the content-addressed store and indexed in `workspace.db`.

## Fitted surface reconstruction and context fixtures

The separate fitted diagnostic measures appearance from a frozen held-out
source, compiles geometry from its recipe parameters and can attach the result
to a source-held-fixed context fixture. The latter retains all original
surroundings and sprites. It is benchmark apparatus, not room generation or
learned material selection. The original baseline results remain available.

For the local E2M5 example (run ingestion/mining/inference first):

```powershell
$fitted = .\tools\bf.ps1 recipes reconstruct rect-stairs-3bea04cfdf5c73f5 --pattern 0a65e188fc188eb841727d92a6f49d37dd4c35d6364fdd7768efc0f30a291399 --with-source-context | ConvertFrom-Json
.\tools\bf.ps1 scene inspect $fitted.project_id
.\tools\bf.ps1 level observe ..\llmapper\maps\blood\campaign\E2M5.MAP --output data\fitted-source --cameras examples\e2m5-stair.cameras.json
.\tools\bf.ps1 level observe $fitted.context_fixture.artifact_path --output data\fitted-candidate --cameras examples\e2m5-stair.cameras.json
.\tools\bf.ps1 recipes compare-views --reconstruction $fitted.report_path --source data\fitted-source\summary.json --candidate data\fitted-candidate\summary.json
```

Choose fresh observation directories on subsequent runs. Other recipe/source
populations can have different IDs; `recipes list` and `patterns search` expose
them. Comparison requires identical poses, executable/resources/settings and
intact recorded PNGs. It reports equal/changed pixels and RGB error, without
an aggregate design score or automatic capability promotion.

The existing NBlood bot is unfinished and unreliable in local probes. Its
[experimental Python diagnostic](docs/experimental-bot-diagnostic.md) is kept
outside normal evaluation and promotion; it is not a traversal oracle. A
repeatable input/position harness remains necessary for behavioral validation.

## Data and licensing

All databases, MAP outputs, renderer frames, runtime overlays, and reports are
under ignored `data/`; source MAP/ART/RFF files remain external. Do not add game
data, binaries or generated commercial-data derivatives to Git. No llmapper
Python package is imported. The observer and NBlood are configurable external
tools, not redistributed dependencies.

The reference checkout has no top-level license granting code reuse. Native
layout facts were studied and implemented independently; renderer/game source
was not copied. See [native format](docs/native-format.md) and
[reference reconnaissance](docs/reference-reconnaissance.md) for attribution
and per-component license notes. No distribution license has been selected
for this new project.

Synthetic tests run without commercial resources. Optional real-corpus tests
check available campaign maps and clearly skip when assets are absent.
