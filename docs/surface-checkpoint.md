# Surface reconstruction checkpoint — 2026-10-12

BloodForge can now reproduce a staircase's supported native surface settings
and test it inside fixed original surroundings. It still cannot generate the
surrounding room or reliably validate player traversal.

## Implemented and measured

`StairSpec` accepts immutable per-step plane/wall materials: shade, palette,
panning, repeats, visual flags, visibility and first-wall alignment. Cardinal
semantic wall IDs stay stable when native allocation rotates. Existing scenes
without appearance keep their previous MAP bytes; transaction, undo and JSON
decoding support both representations.

The fitted diagnostic uses frozen held-out pattern
`0a65e188fc188eb841727d92a6f49d37dd4c35d6364fdd7768efc0f30a291399`
from recipe `rect-stairs-3bea04cfdf5c73f5`. E2M5 source sectors
`[10,12,13,15,14]` become five newly compiled sectors and twenty walls.
Geometry comes from recipe parameters. Appearance is measured from the target;
this is explicitly post-hoc fitting, not learned material selection or a
blinded generalization result. The original simplified benchmark is retained.

Independent checks found exact polygons, heights, internal portals and every
supported surface field. Five floor-relative texture anchors also match.
The new isolated fragment hash is
`6fb0a403b65f743109f7594283289d2750e2ea5d50668e605aef09e58204ba75`.

The fixture assembler requires matching ordered wall loops, anchors, heights
and internal portals before inserting generated records into the source map.
Only index references and five external portal links are restored. Outside
sectors, walls and all sprites remain unchanged; source material fields never
overwrite generated material fields. For this example the result is byte-exact
with original E2M5, hash
`50fc597e01989b6ac95abd0eedeb1eedbda0ecde65ac0161749aeb65589d806a`.
This confirms local native reconstruction; the other 783 sectors are retained
source context, not generated output. Native/portal validation reports no
errors; geometry, mechanisms and collision validation remain explicitly partial.

The native observer rendered two controlled 640×480 poses. Both source-held
fixture frames match the original images in all 307,200 pixels, with zero RGB
error. As a negative control, increasing visible wall 77's shade by 8 changes
52,920 pixels in `foot_up` and none in `mid_down`. The comparator therefore
detects a view-specific appearance error without assigning a quality score.

The context study explains the earlier mismatch: direct neighbor sectors
9/11/16 are insufficient. Across the two views, 23 distinct outside sectors
are visible, including original sprites and active mechanism wiring. Keeping
this context fixed isolates the stair compiler; it does not solve room design.
The isolated fitted staircase still closes external connections.

## Runtime uncertainty

The user's warning about the existing bot is consistent with local probes:
one exploration reached all sectors but lacked a settled final-rise sample;
subsequent bounded runs stayed in sector 0 and timed out. The bot diagnostic
is explicitly experimental, excluded from normal evaluation/promotion, and
always reports traversal verification as false. No more probes were run after
that decision. See [diagnostic evidence](experimental-bot-diagnostic.md).

The earlier NBlood initialization smoke remains valid for its original MAP
hash only. It does not verify traversal of the new fitted fragment. No recipe
was promoted to `verified`.

## Evidence and next bottleneck

- `data/artifacts/fe/fed922bae1af550610deb2cdffab743cbb10ef01cf32352f30355c42896c3db6.json`: full fitted reconstruction report.
- `data/artifacts/7d/7d75462ea8bfc97ef41c8e6ba5e84a3298b67e890990207d89ad87071c93c706.json`: hash-linked paired visual report with live input rechecks, indexed in benchmark history.
- `data/surface-checkpoint.json`: compact replay result and report link.
- `data/observations/fitted-{context,isolated,negative-control}/`: raw observer runs.
- `data/observations/fitted-*-comparison.json`: pixel measurements, including the negative control.
- `data/context-recon.json`: native context, mechanism and visibility evidence.

The README documents `recipes reconstruct` and `recipes compare-views` replay.
All 89 tests passed; both databases have no foreign-key violations and there
are still zero verified recipes. Tests cover material bounds/rotation/transactions, invalid graft rejection,
unchanged context, source eligibility/hash checks, comparator integrity,
negative pixel differences and experimental bot failure handling. Game data
and generated artifacts remain ignored; the reference project remains read-only.

Two offline wheel builds with `SOURCE_DATE_EPOCH=1767225600` produced the same
SHA-256: `e3dd16790a3b4edec1f3e8e98b9c73ebf377138a52ed8737cb3ec3363da87eb7`.

Next: generate landings and adjoining areas as semantic scene objects, then
establish repeatable low-level input/position observation independently of the
autonomous bot. These are needed before making a functional stair capability
claim. Material fitting alone does not infer authentic material choices.
