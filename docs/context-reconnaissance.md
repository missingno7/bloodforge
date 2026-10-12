# E2M5 stair context reconnaissance

Read-only record for the held-out five-tread case. The source is
`D:\Games\DOS\llmapper\maps\blood\campaign\E2M5.MAP` (SHA-256
`50fc597e01989b6ac95abd0eedeb1eedbda0ecde65ac0161749aeb65589d806a`). The
held-out fragment is `data/artifacts/89/89c5a0546b4f2bfbe6ec6bdfc5cf8f4e2e9b50ecf1ad3cd5623b39bb97ee8284.map`;
full parsed records, map hashes, portals, camera evidence, and per-wall data
are in ignored `data/context-recon.json`. No llmapper file was written and no
new render was run.

## Stair and immediate portal boundary

The source step order is sector indices `[10, 12, 13, 15, 14]`, paired with
fragment sectors `[0, 1, 2, 3, 4]`. All five source sectors are flat four-wall
rectangles with no sector tags, extended XSECTOR, or assigned sprites. Their
floors step by 2048 units; the ceiling is `-40960`. The immediate external
portal neighbors are `[9, 11, 16]`.

| Neighbor | Geometry and renderer relevance | Reuse warning |
| --- | --- | --- |
| 9 | Flat, four-wall sector; direct portal from step 0. | Active XSECTOR: `tx_id=114`, `trigger_enter=1`, trigger-on/off, command 1. Sprite 406 has XSPRITE `rx_id=114`. A context-only copy that omits this circuit would silently change behavior. It contributes no painted surfaces in the two pinned views. |
| 11 | Flat, four-wall sector; direct portal from step 4. | Contains sprite 1133 (tile 2528), painted at `foot_up`. Keeping the sector while dropping sprites changes the observed frame. |
| 16 | Flat 15-wall sector; direct portals from steps 0–2; it fans out to 14 neighbor sectors. | The main threshold toward `mid_down`; it has three sprites, including a type-416 sprite and two type-73 launch records. The sector itself has no XSECTOR, but this is not just a rectangular room primitive. |

All three immediate neighbors have clear slope bits and zero floor/ceiling heinum. This does not establish that sectors reached farther into the view are static: visible mid-view sectors 21, 97, 98, 468, and 472 contain XSECTORs, and sector 472 has 15 sprites. The typed extension fields were decoded for this report using the read-only schema in `llmapper/bloodmap/format.py`; names/bit meanings were cross-checked with `NBlood/source/blood/src/db.h`. Blood’s slope flag is bit 1 (`NBlood/source/blood/src/macros.h`, `CEILING_STAT_SLOPE` / `FLOOR_STAT_SLOPE`).

## Pinned view coverage and context budget

The source observer sidecars are `data/observations/heldout-source/summary.json` and
`data/observations/heldout-reconstructed/summary.json`; the paired hashes and
frames are recorded in `data/observations/heldout-comparison.json`.

* `foot_up`: camera `(-7040, -14080, -3072)`, angle 1024, source sector 10 / fragment sector 0. Source paints 18 surface records in 8 sectors: four stair sectors plus context sectors 11, 17, 18, 19. Fragment paints 8 surfaces in 4 sectors and omits the source backdrop and sprite.
* `mid_down`: camera `(-7552, -14080, -7168)`, angle 0, source sector 13 / fragment sector 2. Source paints 89 surface records in 19 sectors. Sector 16 is the only immediate stair neighbor in this view; visible context spans hop distances 1–4 and includes sectors 0–8, 20, 21, 30, 97, 98, 468, 470, 471, 472. The isolated fragment paints one wall over all 307,200 pixels.

So `[11, 16]` is a useful first visual context probe for the two poses, while
`[9, 11, 16]` is the full direct portal ring. Neither set restores the source
views. The lower bound from the renderer’s actually painted source surfaces is
23 external sectors across these poses, plus the five stair sectors. A complete
fixture may also need portal-path closure, occluded portals, and relevant
sprites. This is not a proposal to recreate 23 sectors in the authored scene.

For milestone two, a source-held-fixed evaluation fixture is the narrowest
controlled test: keep the original E2M5 MAP shell and untouched context,
mechanisms, and sprites; replace only the emitted stair through a guarded
graft. Label the result as contextual render evidence for a local reconstruction,
not autonomous room/context synthesis or gameplay proof. Parent-owned fixture
grafting must reject any mismatch in the generated ordered polygon cycle,
first-wall anchor/direction, or step order. It must not paste source appearances
over generated surfaces, because that would conceal compiler errors.

## Wall-order and appearance pitfalls

The current fragment matches all five canonical polygons and floor/ceiling
heights, but its wallptr-ordered cycles do not match the source. For example,
step 0 source wall 69 starts `(-7168,-13056)` and points to
`(-7168,-15104)`; fragment wall 0 starts `(-7168,-15104)` and points to
`(-6912,-15104)`. Step 2 is also anchored on a different edge. Preserve the
ordered `wallptr`/`point2` cycle and verify its first edge independently; a
canonical polygon comparison that rotates/reverses the ring misses this error.

The current fragment assigns picnum 68, shade 0, repeat 8×8, and zero panning to
every wall. Source edges include step-face tile 110, tile 88, and tile 20, plus
per-edge shade, repeat, and panning differences. Compare each oriented edge’s
`picnum`, `overpicnum`, `cstat`, shade/palette, `xrepeat/yrepeat`, and
`xpanning/ypanning`; compare floor/ceiling tiles, shade/palette, stat bits, and
panning separately. Do not reduce a sector to one “wall tile.”

This is grounded in the native record layout (`docs/native-format.md` in
BloodForge; `llmapper/docs/format.md` and `bloodmap/format.py` in the reference)
and renderer source: `llmapper/xmapedit/src/engine.c` uses `sector[sectnum].wallptr`
and the first wall’s `point2` direction in slope alignment (`owallmost`, around
lines 1240–1255), and it passes wall `xpanning`/`ypanning` into rendering
(around lines 2977–2978). Wall endpoint order and first-wall anchoring are
therefore appearance/alignment inputs, not arbitrary serialization details.

## Scope and regression warnings

The current static observer is XMapEdit’s classic software renderer, not
NBlood’s running game. It reports post-occlusion painted surfaces; its documented
limitations include no moving doors/sector effects and approximate sprite
clipping (`llmapper/docs/visual-observation.md`, “Known limitations”). Prior
llmapper stair observations also show that structurally correct stairs can be
distant or visually unreadable from an entry pose (`docs/reference-reconnaissance.md`,
“Door, stair, and renderer findings”). Keep the two pinned cameras, check both
surface/coverage evidence and PNGs, and do not treat a static view as traversal.
Door motion/switch behavior needs a runtime action oracle; a failed focus/input
harness is not evidence that the door failed (`llmapper/docs/door-affordances.md`,
`llmapper/docs/reference-oracles.md`).

The source sector numbers above are evidence locators for this exact MAP hash,
not portable semantic identities. A generalized polygon-room model should use
named semantic boundaries and oriented edge appearance, with native sector/wall
indices retained only in a traceability sidecar. Context-only reuse is clearly
labelled evaluation evidence; it must not be counted as model-generated context.
