# Spatial Understanding v1 — 2026-10-13

BloodForge now answers bounded spatial questions using native geometry,
competing continuity hypotheses and independently rendered pose sequences.
The parser, LevelScene, compiler and strict reconstruction benchmarks are
preserved. This is a first operational spatial-understanding slice, not a learned
theory of room identity or proof of the original designers' intent.

## Evidence levels and scope

Native sector/wall/sprite references remain scoped to the exact MAP SHA-256.
Loop topology, flags, materials and flat heights are native evidence; polygon
area, aperture width, plane overlap and boundary lengths are derived measures.
Two policy-based grouping views propose coplanar continuity through broad
openings and finish continuity across open boundaries. Memberships overlap
between views and do not form a canonical room hierarchy. Area contrast proposes
a transition hypothesis only when a positive unconstrained flat aperture exists.

Negative-space evidence covers native envelopes, polygon holes, XY overlaps,
one-sided and portal boundary lengths, sky flags and sprite references. It does
not subtract actor meshes, prove clearance, infer intentional emptiness, or
classify unmapped outside coordinates as solid. Renderer samples establish
visible native objects at particular poses. Neither native adjacency nor a
missing renderer record establishes navigability, visibility, occlusion or
inaccessibility in general.

## Original source examples

| Source and selected sectors | Measured relationship | Renderer evidence |
|---|---|---|
| Campaign E2M5: 9–16, including stair sequence 10→12→13→15→14 | Finish continuity groups 9,10,12,13,14,15 despite different flat floor levels; coplanar grouping separates the treads. | Five fixed west-facing poses sampled on the treads; retained source context remains original. |
| Campaign E1M3: 287,286,284 | Connected thresholds link a bounded start area to a much larger irregular sky/water sector. The two grouping views do not force these sectors into one room. | Four poses show the opening and changing outdoor water/rock/built surfaces. Their orientations were explicitly authored after initial inspection. |
| Campaign E1M4: 156,335,333,314,152,210 | Coplanar view joins 156+335; finish view also joins 333+314. Sector 314 has area 2,752,512 XY units² and the neighboring 152 has 36,700,160: a 13.33× area contrast. | Five poses show enclosed timber, gated passages, then a wider water-and-rock area and raised timber path. Sector 210 first contributes pixels at the third sample, while the camera remains in sector 314. |

The first observed appearance is only among these samples, not an earliest point
along a continuous player route. Sprite poses and mechanisms are static editor
state; dynamic and underwater behavior remain unknown.

E1M4 sector 210 demonstrates why understanding must exceed rectangular sectors.
Its 46 wall records form five point2 cycles: outer area 136,019,968 and four holes
of 65,536 each, giving 135,757,824 XY units². Treating the wall range as one polygon
would yield the incorrect 123,928,576. These holes are geometric facts, not proof
of columns, solid masses or traversable voids. Sky/parallax flags also prevent a
finite enclosed-volume claim.

Canonical source identities:

- E2M5: `50fc597e01989b6ac95abd0eedeb1eedbda0ecde65ac0161749aeb65589d806a`.
- E1M3: `bc3633ef9f402e21eafb2050737c54812ed45f45eef296f895ad3e6c79c42595`.
- E1M4: `ae239537a4d6973cc0c75ae90e30fc68a8a8c241dbc2bb1293e318cb792b7408`.

All are original campaign sources; no new generalization claim is made from
these three examples. Existing held-out reconstruction splits were not changed.

## Frozen counterfactual predictions

`examples/spatial-interventions.json` specifies predictions before edits or
rendering. Each case deep-copies E2M5; every wall XY coordinate, native record
count, header, sprite and unrelated record remains unchanged. The five exact
camera poses, renderer binary and resources are held fixed and hash-checked.

| Intervention | Prediction and observed result | Changed image pixels at five poses |
|---|---|---|
| Close wall 77↔81 | One reciprocal portal pair removed. Upper-stair sector 13/14/15 contributions at the approach decrease from 98,614 to **0**. Passed. | 231,923; 288,265; 0; 0; 0 |
| Lower sector 13 ceiling to −8192 | XY, floor area and adjacency stay fixed; static apertures and rendered views change. Passed. | 54,364; 54,745; 54,970; 0; 0 |
| Set movement-blocking bit on 77↔81 | Native movement constraint changes while adjacency and every image remain fixed. Passed; runtime collision remains untested. | 0; 0; 0; 0; 0 |
| Increase wall 77 shade by 8 | Appearance changes while native apertures and continuity groupings remain fixed. Passed as a decorative control. | 52,920; 0; 0; 0; 0 |

Renderer contributions are nonexclusive sample counts and may exceed the frame
area. Image pixel differences are measured separately and are not a design
quality score. The opening prediction is tested by source-object visibility,
not merely by a different-looking image. These results distinguish geometry,
movement flags and perceived appearance even when sector counts and floor areas
remain identical. They do not establish learned design rules.

## Failures and unresolved cases

The frozen community contrast `community/BLUDTOWN.MAP`, hash
`670fa0792ff8d2c0be2fafb921261b67184331107f95975c288f0c56d635294d`,
fails the whole-map native geometry/portal gate (64 validator errors, including
two-wall sectors such as 17 and 70). It is recorded as unsupported, not repaired
or replaced with an easier holdout in the original 1.0 report. The tier-S alias has the same hash and does
not count as a second example. Tier is provenance, not a quality judgment.

Selected E1M3 geometry is supported, but neighbor sectors 72 and 325 have invalid
or touching polygon loops under this geometry model. Their geometry limitations
are localized, and their area-dependent ratios remain unknown. Some overlapping
E2M5 geometry prevents unique automatic camera-sector resolution; explicit
native sector hints are retained for the five valid samples. Nonzero slopes,
room-over-room effects, collision volumes, dynamic mechanisms, intent and
continuous traversal remain outside this verification level. The bot was neither
launched nor used as an oracle.

## Scoped follow-up — extractor 1.1.0

Explicit sector inspection now proves the selected polygons and all incident
portal references independently of unrelated malformed loops. Native records
and complete unique wall ownership still pass globally. Incoming references
from outside selection cannot bypass reciprocity, ownership or reversed-endpoint
checks. The full validator findings remain in `scope_validation`; failed global
connectivity is reported as unknown rather than computed from damaged links.
Default whole-map inspection keeps its original strict gate. Unsupported
neighbor polygon/Z facts make dependent measurements unknown, with endpoint
support recorded separately from native aperture measurements.

The contract was tested on synthetic damage before re-evaluating BLUDTOWN.
Sectors 810 and 814 are now supported locally, with areas 24,903,680 and
329,269,248 XY units². Sector 814 has one hole of 31,588,352 XY units². Their four
reciprocal links are 6205↔6233, 6206↔6232, 6208↔6236 and 6209↔6235. Each has
movement-blocking and masked flags; neither grouping view merges them despite
matching floor and ceiling finishes. The 13.22× area contrast therefore earns
no unconstrained expansion candidate. All **14 geometry and 50 portal errors**
remain outside this local selection; whole-map inspection still fails.

Two authored poses in `examples/spatial-bludtown.cameras.json` passed strict
local XY/Z checks and rendered without modifying inputs. From the sector-814
pose, sector 810 contributes 5,860 surface samples; from sector 810, sector 814
contributes 14,490. The latter frame is dominated by a nearby wall, with other
surfaces near its edges. Contributions are nonexclusive renderer records, not
area or traversability. Eleven and two additional visible sectors respectively
are explicitly outside the analysis scope. The sequence retains that distinction,
the analyzer version, whole-map failures and a hash of its analytical input.
These poses do not establish a continuous route or physical access.

BLUDTOWN is now a **diagnostic re-evaluation**, not an unseen generalization
test. Its original failure and earlier report remain intact; no grouping policy,
source geometry, corpus alias count or reconstruction holdout changed.
This gives an agent useful local evidence without promoting the entire source
or assigning spatial interpretations to every object the renderer happens to see.

The follow-up passes **168 tests**, including every previous reconstruction and
compiler regression. Synthetic cases cover remote damage, corrupt incoming
portals, malformed selected polygons, global ownership ambiguity, unsupported
neighbors and scope-sensitive evidence identities. All four frozen campaign
counterfactuals still pass after live input/output hash checks. Every previously
released campaign geometry/grouping metric is unchanged. Both databases have
zero foreign-key violations; zero recipes are verified. No bot was launched.
An offline wheel build succeeds with SHA-256
`b7c36972fcacb8fe3385a4d815c519675417aa4024b75344b088c4259e542ba0`.
Visibility transition deltas still cover all rendered native sectors; use the
per-view scope fields when interpreting them as local spatial evidence.

Follow-up visual report: `data/spatial-v1/scope-release-20261013/report.html`;
machine report beside it. Immutable artifact:
`data/artifacts/05/05d28948ea2ee58b9f06104ebadb84ebaf1873ea8d7e9417d634782deedb1a11.json`.

## Replay and stored evidence

Install `.[spatial,visual]`, then follow the README `space inspect`, `transitions`,
`viewpoints`, `intervene` and `compare` workflow. Full replay:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe tools\spatial_demo.py --output data\spatial-v1-fresh
```

Original 1.0 local visual report: `data/spatial-v1/release-20261013/report.html`.
It includes representative top-down SVG diagrams and controlled rendered frames.
The machine report is beside it; its immutable indexed artifact is
`data/artifacts/3a/3a34ef94e1aa265d63173491a3e104d591c929d999d0c21dae393dbd78e920fe.json`.
Source analyses use extractor `spatial-understanding-1.0` in the existing knowledge
store. Counterfactuals, comparisons and PNGs use the existing workspace artifact
index. Commercial MAPs, assets and generated output remain outside Git.

The original 1.0 checkpoint passed **154 tests**, including the previous 111 parser, reconstruction,
compilation, transaction and observation tests. New regressions cover holes,
ambiguous poses, nonzero and zero-heinum slopes, inverted bounds, overlapping
groupings, false expansion candidates at blocked boundaries, source identity,
native renderer-object ownership and bounded edits. Both databases have zero
foreign-key violations and there are still zero verified recipes. The final
analyzer reproduces all three released source reports exactly. Two offline wheel
builds with `SOURCE_DATE_EPOCH=1767225600` produced SHA-256
`0600d218d3c684b5c9b262ded32b35af2fbcfcc516ec67bab3f93cbd6e9a1527`.

An LLM agent can now ask which source sectors share a continuity hypothesis,
what native boundary separates them, when a selected region first appears among
sampled views, and whether an audited edit changes that visibility while leaving
other structural properties intact. It receives source refs and expandable
evidence rather than a room label or an aggregate design score. The next step is
to test and refine these relationships across contrasting source examples and
unsupported geometry, then express supported relationships as authoring constraints.
