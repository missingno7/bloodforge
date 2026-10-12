# Experimental NBlood bot route diagnostic

BloodForge exposes NBlood's autonomous route signal only as an experimental
diagnostic. Its reliability is unestablished: the bot chooses its own route,
has no waypoint input in the inspected build, and these probes did not produce
a completed traversal record. A complete-looking sector/Z trace is reported
as `diagnostic_observed`; `claims.traversal_verified` remains false.

The bounded API is
`bloodforge.bot_diagnostic.diagnose_bot_route(map_path, expected_sectors, output_dir,
executable, assets_dir, *, data_root, game_seconds=5,
max_wall_seconds=10.0)`. The ordered sectors are a comparison target, not
steering instructions. The wrapper confines all writes to `data_root`, uses a
hidden process, kills and reaps only its own NBlood child at the wall-clock
deadline, and enforces independent 32 MiB caps on telemetry and trajectory
files. It also caps parsed traces by total bytes, line length, and attempted
line count (including blank and malformed lines). Exceeding a file or parser
limit kills the process when necessary and reports `resource_limit`, distinct
from wall-clock `timeout` and engine `crashed`. Its output records hashes of
the input map, executable, and reference assets.

Two bounded runs used the generated five-sector map with expected route
`(0, 1, 2, 3, 4)`. In the first, the bot emitted 312,500 repeated trajectory
samples in sector 0 with game time 0 and no completion summary before the
wrapper killed it at 10 seconds. In the second, adding NBlood's
`-bot_realtime` option yielded one trajectory sample in sector 0, no summary,
and another wall-clock timeout. The first report is at
`data/runtime/traversal/traversal-89c5a0546b4f-1791840124611777800/report.json`;
the second is at
`data/runtime/traversal/traversal-89c5a0546b4f-1791840247445873300/report.json`.
Both report that the source MAP, executable, and all reference asset hashes
were unchanged. Neither run supports a route/traversal claim.

An earlier direct exploratory run produced sector transitions `0, 1, 2, 3, 4`,
but the actor did not settle by one rise on the final sector transition before
the bot stopped. That run also does not validate complete stair traversal.
Further bot probes were stopped because the observed behavior is inconsistent
and the bot is unfinished. This diagnostic must not be presented as a
validated behavior oracle or used as a gate for authoring correctness.
