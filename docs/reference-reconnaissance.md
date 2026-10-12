# BloodForge reference reconnaissance

Read-only inventory of local `llmapper` tools, their source references, prior
observation/runtime evidence, and candidate commands. No game/editor executable
was launched while preparing this note.

## Local capability and evidence

The reference checkout is `D:\Games\DOS\llmapper`. Its README describes a
dependency-free Python 3.10+ MAP toolkit (`README.md`, Quick start and observer
sections). The source checkout includes a built headless observer at
`xmapedit\bin\xmapedit-observe.exe` (658,732 bytes), NBlood at
`reference\blood\nblood.exe` (7,634,944 bytes), and Blood resources including
`BLOOD.RFF`, `BLOOD.INI`, `SOUNDS.RFF`, `TILES000.ART` through `TILES017.ART`,
`SURFACE.DAT`, `TABLES.DAT`, `CHARSET.DAT`, `VOXEL.DAT`, and `GUI.RFF`. Thus the
local inventory is sufficient to attempt both static rendering and NBlood load
smokes. The reference directory also contains existing maps/logs/caches; treat
it as input and put new artifacts elsewhere.

The observer is already built; do not rebuild unless its binary is absent or
stale. Its source is the XMapEdit fork under
`xmapedit\src_blood\observe\` plus renderer hooks in `xmapedit\src\engine.c`.
The build recipe is `mingw32-make -C xmapedit/src_blood/observe` from the
llmapper root. The command creates `xmapedit\bin\xmapedit-observe.exe` and
object files under `xmapedit\src_blood\observe\obj` (Makefile). The observer
is a separate classic software-renderer process: no editor GUI, SDL, OpenGL,
audio, focus, or injected keys. It reads a Blood MAP, ART data and `BLOOD.RFF`,
renders supplied camera poses, and reports visible native surfaces, clipped
painted areas, occlusion, and invalid poses. A PNG is optional; the JSON packet
is the primary result (`docs\visual-observation.md`; observer README).

NBlood has a different job: whether a map initializes and stays alive, and
whether gameplay actions such as doors and switches work. `oracle-nblood` stages
the map, config and logs in per-run work directories and terminates each test
process after its bounded grace period. The CLI uses `-usecwd`, an explicit
`-game_dir`, `-cfg`, `-map`, and `-noautoload`; it does not copy or rewrite the
source map or game assets (`bloodmap\oracle.py`, `_probe_map` and
`run_nblood_oracle`). The standard oracle proves load/startup only. The action
and behavior oracles exercise triggers and movement, but their screenshot/input
gate has an environment-sensitive window-focus dependency (`docs\reference-oracles.md`).

## Door, stair, and renderer findings

* **Door appearance and operation are separate checks.** Static `DoorAffordance`
  can audit the closed slab, approach-side distinction, channel and nominal
  opening. Runtime action checks are needed to prove motion or switch behavior
  (`docs\door-affordances.md`; `reports\SP-v2-door-audit.md`). A previous
  SP-v2 NBlood action run initialized all three maps, but could not focus the
  isolated window; no before/after screenshots were captured. This is an
  action-harness failure, not evidence that the doors failed
  (`reports\SP-v2-door-runtime.json`, `note` and `input_control_error`). Keep
  that distinction explicit in future regressions.
* **A staircase can exist and still be visually unreadable from the approach.**
  The structured observation experiment measured the grand stair at 5.5% of the
  lobby-entry frame. Widening it from six to nine player widths raised that to
  8.1% at the same replayed pose; hard gates still passed, but the report says
  the stair remained a distant feature (`experiments\nested-authoring-observation.md`).
  Keep a pinned entry camera and a foot-of-stair camera in regressions; compare
  measured node visibility and inspect an optional frame rather than treating
  structure detection or geometry gates as a visual pass.
* **Sky and palette have known plausible-looking failure modes.** An earlier
  generated-level path used one sky panel instead of the Blood panorama's
  16 offsets, making outdoor skies black. An incorrect palette path first
  caused magenta speckle; compensating for it produced a plausible but dark,
  wrong-colour frame. Both were corrected and documented
  (`docs\authoring-architecture-audit.md`, sky bullet;
  `docs\visual-observation.md`, Colour). When sky/color matters, inspect the
  actual observer frame and manifest renderer/palette details.
* **Static renderer scope is limited.** Observer output is the XMapEdit classic
  editor renderer, not a running game state. It does not animate doors or sector
  effects; voxels/models are not drawn; sprite occlusion follows engine sprite
  clipping rather than exact per-pixel occlusion. Use NBlood for load and
  behavior claims (`docs\visual-observation.md`, Known limitations).

## Safe standalone command candidates

Run Python commands from `D:\Games\DOS\llmapper` so `bloodmap` and `tools`
resolve. Use the following output root in this workspace:
`D:\Games\DOS\bloodforge\data`. The folder can be created on first use.
Replace `<candidate.MAP>` with the authored MAP path under that data folder.

```powershell
$Candidate = 'D:\Games\DOS\bloodforge\data\candidate.MAP'

# Structured visual observation of a decompiled MAP. This is read-only against
# the reference tree; packet, request, and optional images go under bloodforge\data.
python -m tools.observe decompiled $Candidate `
  --hierarchy D:\Games\DOS\bloodforge\data\hierarchy.json `
  --resource-dir D:\Games\DOS\llmapper\reference\blood `
  --binary D:\Games\DOS\llmapper\xmapedit\bin\xmapedit-observe.exe `
  -o D:\Games\DOS\bloodforge\data\observer-entry

# Bounded NBlood load smoke. Candidate is copied into the explicit work tree;
# configs and logs are created there. Reference assets are passed as game data.
python -m bloodmap oracle-nblood $Candidate `
  --nblood D:\Games\DOS\llmapper\reference\blood\nblood.exe `
  --game-dir D:\Games\DOS\llmapper\reference\blood --seconds 6 `
  --work-dir D:\Games\DOS\bloodforge\data\nblood-load `
  -o D:\Games\DOS\bloodforge\data\nblood-load-report.json
```

After inspecting the observer packet, add `--screenshot <actual-view-id>` to
capture a selected planned pose. `--screenshot` names a view id; `entry` is not
assumed to be a valid id for every MAP.

For an authored `LevelProgram`, use `tools.observe program <module>` instead;
that mode compiles and writes a MAP as well as the packet, so pass `--map-out`
under `bloodforge\data` if it should not default to a MAP inside the output
directory. For an actual door/switch claim, use `oracle-nblood-action` or
`oracle-nblood-behavior` only after the basic load smoke. Action screenshots may
be unavailable if Windows cannot focus the isolated process. The low-level
observer itself can also be called with a request JSON whose `map`,
`resource_dir`, and `output` fields are absolute paths; it writes
`observation.json` and requested images only beneath the request's output path
(`xmapedit\src_blood\observe\README.md`).

The static observer reads but does not edit `reference\blood`; the NBlood
harness itself places its controlled config, copied candidate MAP, stdout,
stderr, and generated log in `--work-dir`. The engine still receives the
reference directory as its data path. Keep that directory treated as input and
review its timestamps if proving zero writes is a requirement; no executable
was run here to test engine-side cache behavior.

## Licensing and attribution notes

`xmapedit\README.md` identifies XMapEdit contributors and upstream components:
JFBuild / Jonathon Fowler, NBlood / nuke.YKT, SAMapedit, and Ken Silverman's
BUILD Engine / KPLIB. The observer README identifies its fork as XMapEdit
submodule revision `ea89fb1` and describes the classic renderer source seam.
`xmapedit\buildlic.txt` / `COPYING` contains Ken Silverman's BUILD source
conditions: derivative works may be distributed only over the Internet, free
of charge; distributions must include the specified Ken Silverman copyright
notice and the license; modified source files must carry the modification
notice. Do not copy observer or renderer implementation code into BloodForge.
If distributing a derived BUILD-source binary or source, preserve those
conditions and the required attribution. `NBlood\README.md` labels NBlood a
reverse-engineered port; `NBlood\AUTHORS.md` and per-component license files
carry its attribution/licensing context. The locally supplied commercial Blood
game files are not source assets to copy into tracked BloodForge paths.

## Evidence references

All paths below are relative to `D:\Games\DOS\llmapper`:

* `README.md` — observer, runtime oracle, and quick-start command families.
* `docs\visual-observation.md` — observer design, exact renderer limits, palette
  history, and `mingw32-make` / `tools.observe` commands.
* `xmapedit\src_blood\observe\README.md`, `Makefile`, `obs_main.c` — source
  implementation, request format, build output, RFF/ART reads and renderer.
* `bloodmap\visual.py`, `tools\observe.py` — request paths, observer invocation,
  generated packet/MAP output behavior.
* `docs\reference-oracles.md`, `bloodmap\oracle.py` — NBlood CLI contract,
  work-directory staging, startup markers, run bounds, and behavior limits.
* `docs\authoring-architecture-audit.md`,
  `experiments\nested-authoring-observation.md`,
  `reports\SP-v2-door-audit.md`, `reports\SP-v2-door-runtime.json` — prior
  stairs, renderer, door and action-capture evidence.
* `xmapedit\README.md`, `xmapedit\buildlic.txt`, `xmapedit\COPYING`,
  `NBlood\README.md`, `NBlood\AUTHORS.md` — attribution and license notices.
