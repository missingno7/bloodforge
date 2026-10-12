# First executable milestone — 2026-10-12

BloodForge now runs the corpus → staircase candidates → parameter inference →
held-out reconstruction → native validation → independent rendering/load-check
workflow. The current evidence supports structural reproduction of a narrow
family. It does not support a complete playable level or a verified recipe.

## Measured corpus results

| Measure | Observed result |
| --- | ---: |
| Physical MAP paths inventoried | 3,235 |
| Population/source paths | 1,773 |
| Tier-view copies | 1,462 |
| Distinct SHA-256 identities | 1,701 |
| MAP v7 identities parsed and round-tripped byte-exactly | 1,690 |
| Unsupported v6 identities | 9 |
| Files with non-Blood signatures | 2 |
| Duplicate groups among non-view source paths | 72 |
| Native sectors / walls / sprites stored | 433,825 / 3,424,395 / 692,649 |
| Derived observation rows | About 1.23 million |
| First complete ingestion | About 74 seconds |
| Unchanged complete rescan | About 3 seconds, zero reparses |

Errors are retained rather than normalized away: `BB9.MAP` uses v0x0603;
eight community maps use v0x0600; `POWER06.MAP` and `TWISTER.MAP` have a
non-Blood signature. All 43 single-player campaign maps round-trip exactly.
Full directory/tier/tutorial findings are in [corpus reconnaissance](corpus-reconnaissance.md).

The default strict miner found 40 campaign candidates across 18 distinct maps
and 18 conservative map groups. Separate community mining found 1,126
candidates across 416 hashes / 404 conservative groups. Tutorial mining found
5 candidates across 4 maps. These are static geometric candidates, not quality,
gameplay, or mechanism judgments. Sources without credited author metadata do
not count as independently known authors.

## Held-out reconstruction

Current recipe: `rect-stairs-3bea04cfdf5c73f5`, status `reproduced`.
Extractor: `rect-stairs-2/native-geometry-1`.

Training used 33 instances from 15 map groups. Eight campaign maps were assigned
to the frozen held-out population independently of parameters; three contain
seven candidates in this strict family. The empirical domain is 3–8 treads,
width 1024–4096, tread depth 256–1024, and native Z rise 2048–16384. These are
observed structural bounds, not tested player-climb limits.

Five held-out fragments from E2M5/E6M6 compiled into real v7 MAPs and matched
source polygons, floor/ceiling Z and internal portal topology exactly. Two
other candidates were outside the training domain and remain recorded. No
manual parameter repair was used. Floor/ceiling tile fields and wall material
match fractions are reported separately; complete rendered materials are not
reconstructed. The independent comparison follows the emitted `point2` loops,
not the compiler's intent or the detector's output claims.

The detector/primitive were designed with LLM assistance. The held-out claim
applies to empirical parameter fitting, not a blinded development study or
automatically synthesized code. Strict automatic capability promotion is still
withheld.

## Independent engine evidence and what it exposed

The five-step E2M5 fragment with MAP hash
`89c5a0546b4f2bfbe6ec6bdfc5cf8f4e2e9b50ecf1ad3cd5623b39bb97ee8284`
was rendered beside its original source with matched foot and midpoint cameras.
Both XMapEdit observations completed. Native surface/object IDs, camera poses,
source-to-generated sector correspondence and PNG hashes are retained.

The foot view contains recognizable stairs, but simplified texture/shading and
closed fragment boundaries visibly differ from the source. Looking back from
the midpoint, the source shows the adjoining room while the fragment shows a
full-frame wall. This is a recorded failure of visual/context fidelity, despite
exact internal structural reconstruction. The next architectural task is to
represent connected context and material continuity explicitly.

NBlood loaded the same generated MAP, emitted initialization markers, and
remained alive during the five-second smoke window. The runtime uses private
asset staging because NBlood creates its texture cache in its game-data search
directory. Input MAP, executable and all 554 reference asset files retained
their hashes. See [runtime evidence](runtime-evidence.md). No player movement,
stair traversal, switch activation, level exit or full playability was verified.

## Local evidence and replay

- `data/knowledge.db`: queryable native/derived facts, candidate references,
  recipe inputs and validation history.
- `data/workspace.db`: semantic scene revisions and content-addressed output references.
- `data/demo.json`: public CLI demonstration summary and full benchmark-report path.
- `data/checkpoint.json`: combined, hash-linked structural, visual and runtime evidence.
- `data/observations/heldout-comparison.json`: source/native-object visibility comparison.
- `data/observations/heldout-{source,reconstructed}/render/frames/`: paired PNGs.
- `data/runtime/nblood-89c5a0546b4f-1791839151522966400/`: isolated successful engine run.

Run `tools/bf.ps1 demo` for the deterministic core workflow and see the README
for native observer/runtime commands. To bundle an existing benchmark report,
paired visual comparison, two observation summaries and a runtime report, use
`checkpoint --benchmark ... --comparison ... --source-observation ...
--reconstruction-observation ... --runtime ...`. Bundling validates matching
generated/source hashes and source-sector correspondence before recording any
cross-tool claims. It preserves old evidence and does not promote a recipe.

All 60 automated tests passed. The suite covers synthetic malformed records, real campaign maps,
deduplication, interrupted/bounded scans, provenance/history, frozen splits,
quarantine, mutation preconditions, rollback/undo, geometry regressions,
independent comparison, native-tool result classification, asset integrity,
and public CLI compilation. SQLite's foreign-key check returned no violations.
Two offline wheel builds with Python 3.12.14, setuptools 84.0.0, wheel 0.48.0
and `SOURCE_DATE_EPOCH=1767225600` produced identical SHA-256:
`c95bf75629cad1346d67b0c752b69e83e6bf748a1d28fa77753d4b2090fca99b`.
Commercial
MAP/ART/RFF files, executables, screenshots and database files are excluded from
Git; llmapper is an external read-only reference, never a Python dependency.

## Acceptance boundary

The requested initial foundation and one full structural vertical slice have
been demonstrated, including auditable failures. Native load and rendering are
available. The long-term observe/discover/synthesize/verify/promote loop is not
complete: functional traversal, general composition, full surface fidelity,
mechanism semantics and autonomous recipe synthesis remain in the task ledger.
