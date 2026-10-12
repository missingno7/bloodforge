# BloodForge

An independent Python 3.12 project for evidence-driven Blood MAP v7 analysis
and construction. The first working slice indexes the real local corpus,
discovers strict rectangular stair runs, infers parameter bounds from training
maps, reconstructs held-out fragments, and records native, structural, visual,
and runtime evidence separately. No LLM is required by the library or CLI.

This is a foundation, not an autonomous full-level designer. See
[the first milestone](docs/milestone.md), [surface reconstruction checkpoint](docs/surface-checkpoint.md), [architecture](docs/architecture.md),
and [task ledger](docs/tasks.md).

## Run from this checkout

The PowerShell launcher uses `BLOODFORGE_PYTHON`, the locally bundled Python
3.12 runtime when available, or `python`. It does not install dependencies.
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
The source model currently accepts one straight stair per scene, material tile
IDs, optional per-step plane/wall appearance and an optional player start.
Appearance includes shade, palette, texture scale/panning, visual flags,
visibility and a cardinal first-wall anchor for relative texture alignment.
It intentionally cannot represent a complete
gameplay level yet. `scene apply` takes a JSON list of `SceneChange` records,
an `--expected-revision`, and optional `--dry-run`; each record has operation,
object_id, expected, and value. Exact previous-value preconditions and native
compilation must succeed before a revision commits. `scene undo` appends a
new revision; `scene diff --before N --after M` reports semantic changes.

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
