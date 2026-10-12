# Connected stairs demo evidence

`examples/connected-stairs.scene.json` is a synthetic, authored level fixture. It has five east-facing treads (2048-unit width, 256-unit tread, 2048-unit rise), a 1024-unit landing at each end, lower and upper rooms, and an east corridor. Three explicit `ConnectionSpec`s join the lower room to the bottom landing, the top landing to the upper room, and the upper room to the corridor. Landing-to-stair adjacency is implicit in `LandingSpec`. The player start is in the lower room. No commercial MAP geometry or author indices were used.

Materials are explicit author choices: floor tile 20, ceiling tile 454, wall tile 110 in the rooms/landings, and wall tile 68 on the stair; the level is not evidence of learned material selection. Room and stair walls use one repeat per 64 map units: 48 for 3072-unit room walls, 32 for 2048-unit stair spans, 16 along 1024-unit landings, and 4 across 256-unit treads. The corridor uses repeat 32 on each wall. Shading varies only on the raised room and corridor.

## Native compile

Workspace project: `connected-stairs`. MAP artifact: `data/artifacts/45/45bb28f2e69d862bc016fcaa14ff447dc3fda9ca445bff272ac335085ed755cd.map` (SHA-256 `45bb28f2e69d862bc016fcaa14ff447dc3fda9ca445bff272ac335085ed755cd`). It contains 10 sectors, 46 walls, and no sprites. Native format and reciprocal portal checks report zero errors; the combined validator reports `partial` because it does not test arbitrary polygon intersections, mechanisms, runtime collision, or render assets. Full compile output is `data/connected-stairs-compile.json`.

## Static XMapEdit observations

Four semantic cameras in `examples/connected-stairs.cameras.json` were rendered by `xmapedit-observe.exe` to `data/observations/connected-stairs-final/`. The semantic sector IDs were resolved against the compiled current project, and the CLI required the observed MAP bytes to match that project revision. XMapEdit exited 0; the wrapper reports `completed`, all requested views rendered, all screenshots valid, all outputs contained, and MAP/executable/assets unchanged. The 640×480 renderer was the XMapEdit classic Build software renderer using the Blood palette and shade tables. The manifest is `data/observations/connected-stairs-final/render/observation.json`; the identity summary is `data/observations/connected-stairs-final/summary.json`.

| View | Camera sector | Visible evidence | PNG SHA-256 |
|---|---:|---|---|
| `entry` | 1 | Lower room faces the stair run; several ascending treads are visible. | `121ed396c851f0982ee097a8650b196c26f7fd349fe17c2810ae3ee797b464dc` |
| `stair-up` | 7 | Static view from the middle tread shows a riser, treads, side walls and ceiling. | `c49bc1cb54fda38b242fd4208164a5970204a9216959059b7010ec8e1376a367` |
| `upper-lookback` | 2 | Upper room and the stair/landing direction are visible from the far side. | `6815631fede4dd39fef03aeb0cb562f3b3986ffabfa13a3bb8a647b27caa31dc` |
| `corridor` | 0 | Corridor view faces west through its opening toward the wider upper room. | `d9a6032e944cc233dca536fcaee845a9aeaf0b8fafd014a448cd225e056ae021` |

Each frame reports all 307,200 pixels painted. The first three views report respectively 39, 17, and 31 surfaces; corridor reports 4. The stair-up view records one occluded surface and upper-lookback records five, as expected from depth ordering. These are static renderer observations only: they do not show traversal, collision, door motion, or gameplay. They are not a source-map comparison and do not establish runtime or engine playability.

## Static topology and engine initialization

An independent audit of the emitted native wall records found nine reciprocal portal pairs and all ten sectors reachable from the start in the static portal graph. The indexed audit is `data/artifacts/5a/5ab6e2ccfde11e65929fa75d594bed69be1fc60fcc339203a2ed74856677344e.json`. Recompiling the previous fitted single-stair fixture retained MAP SHA-256 `6fb0a403b65f743109f7594283289d2750e2ea5d50668e605aef09e58204ba75`.

`level test-load` reported `initialized` for this exact connected-area MAP. The ordinary NBlood launch included `-map candidate.MAP -noautoload -quick -nosetup` and no bot flag; no bot traces or outcomes were used. The engine binary and staged source assets were checked by the existing smoke wrapper. The indexed report is `data/artifacts/fa/faf43862c8106ad74f26b2d59a5966cd6a3fbe86e52ec30e38bc6f609a2574c9.json`.

The evidence supports native compilation, static connectivity, rendering, and engine initialization. Player spawn, stair traversal, collision clearance and playability remain unverified. No recipe was promoted on this evidence.

## Software checks

All 111 automated tests pass. New coverage includes all four stair directions,
both landing ends, rotated texture anchors, exact split-wall phases, reciprocal
portals, invalid geometry/reference rollback, unchanged unrelated material
records, JSON compatibility, undo and camera-to-MAP identity checks. The
unfinished bot was not launched or used as a verification oracle.

Both SQLite databases have zero foreign-key violations; the knowledge database
still has zero verified recipes. Two offline wheel builds with
`SOURCE_DATE_EPOCH=1767225600` produced identical SHA-256
`75e75fe97a2cc6d7309ab57f5a9d7f4c8af3cda4fdbfb0f4a1b9a48f8409b803`.
Game assets, generated MAPs, database files and local render outputs remain
outside Git. The reference repository remains read-only.

The next functional bottleneck is repeatable input and position observation
independent of the unfinished autonomous bot. Context placement and material
selection also remain authored rather than learned.
