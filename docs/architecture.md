# Architecture, first executable slice

## Ownership of truth

`native.py` controls v7 binary parsing and emission. Parsed dataclasses are
native facts; opaque extended records are preserved rather than interpreted.
`extraction.py` measures structural adjacency, materials and signatures.
`patterns.py` emits interpreted staircase candidates with native object IDs.
`learning.py` infers empirical domains, freezes evaluation populations and
records reconstruction results. None of these stages silently grants the next
stage's claims. A recipe remains `reproduced`, never `verified`, after the
current checks.

`LevelScene` in `scene.py` is the only authoring model. `compiler.py` allocates
native records and portals deterministically; source authors use semantic IDs.
`workspace.py` commits revisions only after preconditions and compilation pass,
using an immediate SQLite transaction. Dry runs write nothing; undo adds a
monotonic revision. Flat rectangular areas, derived stair-end landings and
explicit shared-boundary connections compose in the same scene. Mechanism
channel allocation is not implemented behind speculative interfaces.

`spatial.py` adds a derived, source-hash-bound understanding layer without
extending LevelScene or replacing MAP facts. Coplanar/broad-opening and finish
continuity produce complementary, overlapping memberships, explicitly called
hypotheses. The same native sector may participate in both; neither view is a
room partition. Native aperture, floor/ceiling relations, movement flags, renderer
flags and material matches remain independent observations.

`spatial_geometry.py` uses Shapely/GEOS for irregular point2 polygons, holes,
nested loops and XY overlaps. This optional dependency provides mature polygon
operations; no source geometry is repaired. XY and Z are analyzed separately:
Shapely is [planar](https://shapely.readthedocs.io/en/stable/manual.html#coordinate-systems).
A slope flag with zero heinum uses the native base plane; nonzero slopes remain
unsupported for camera Z and aperture evidence. Unmapped outside coordinates
and polygon holes are not classified as solid material. Sector envelopes do
not establish free player volume or intentional compositional emptiness.

`spatial_views.py` joins independently observed native objects to hypotheses
only after checking the exact source SHA, manifest, pose, executable, assets
and image identities. It reports first appearance among sampled poses and
successive visible-object changes. Renderer contributions can overcount pixels;
they are not physical occupancy, distance or exclusive image coverage. Missing
objects are not automatically occluded. A static pose sequence is not a route
traversal or runtime state observation.

`spatial_edits.py` performs bounded interventions on copied maps and audits every
changed native field. The v1 comparison requires preserved native allocation
and XY loops. Fixed-pose comparisons test frozen predictions about apertures,
visibility and invariants, with a separate appearance-only control. Results
remain counterfactual artifacts and cannot contaminate original-map evidence.
See [the spatial checkpoint](spatial-understanding-v1.md).

## Storage

`knowledge.db` uses schema version 1, an application ID, foreign keys, WAL,
typed native record tables, JSON-checked observation/evidence columns, FTS5,
and indexes for source identities, provenance, signatures and families.
`contents` owns SHA-256 identities; `sources` owns current path metadata and
`source_history` retains replaced identities/metadata. Tier copies remain
separate source locations with `is_view=1`. One extraction serves all aliases.

Spatial analyses reuse `observations` with extractor `spatial-understanding-1.0`,
source SHA, and a scope/policy/geometry-engine-version key. No schema migration
is needed. Generated counterfactuals are indexed only as workspace artifacts;
they are not inserted as original corpus content. Full reports and observer
images use the existing content-addressed artifact store. Prototype extractor
results remain distinct from the released version rather than being overwritten.

`workspace.db` separately owns semantic projects, revisions, operations and
outputs. MAPs, full reports and PNGs live under `data/artifacts/<hash-prefix>/`.
External renderer/runtime work folders are retained for raw audit logs; their
reports and frames are also stored by content hash. No whole MAP JSON is
duplicated into observation rows. FTS is for retrieval, not inference.

Migration 0→1 creates each store transactionally. Newer schemas, the other
store's application ID, or unrecognized populated databases are rejected.
Future migrations must preserve old evidence and bump relevant extractor
versions; derived caches include source identity and extractor versions.

## Ingestion and discovery

Discovery reads actual directories. Every pass recomputes file hashes, but
unchanged content is not reparsed. Successful extraction requires a byte-exact
native round trip. A savepoint isolates each failed parse/extraction; each
processed path is a durable checkpoint. Only a complete scan deactivates
missing sources. Failures and unsupported formats are retained.

Geometry signatures normalize translation and record order while retaining
sector/edge shape. Coarse dimensions and topology form possible-revision
buckets. They are leads, not equivalence proofs. Conservative split groups
merge SHA aliases, geometry signatures, coarse buckets and known author names.
Unknown revisions/authors can still leak; no perfect near-duplicate detector
is claimed. Counts separate physical paths, hashes, revision groups, occurrences
and normalized pattern families. Tiers never increase evidence weight.

The first miner is deliberately strict: at least three static flat rectangles,
constant rise and ceiling, equal widths/treads, a straight cardinal run and
exact reciprocal nonblocking portal edges. Sector and wall mechanisms/slopes
are excluded. Native indices and fitted parameters survive normalization.
The detector does not equate sectors with rooms or adjacency with traversal.

## Recipe and benchmark discipline

The staircase primitive was designed with LLM assistance. Training learns
numeric parameter bounds from independent map groups; cardinal rotations are
an explicitly trusted primitive invariant. No arbitrary generated code runs.
This is empirical recipe-domain learning, not automatic constructor synthesis.
Held-out means withheld from parameter estimation; it is not a blinded study
of the hand-designed detector/compiler.

Group splits use identities only, before estimating parameters. Recipes freeze
training references and held-out candidate membership. Later source removal or
quarantine produces an ineligible result without shrinking the denominator.
Generated/conversion/unknown identities cannot donate construction evidence,
including conflicting SHA aliases. Author metadata is preserved on rescan.

`fidelity.py` independently follows emitted native point2 loops and compares
polygons, Z values, internal portal topology and complete supported native
plane/wall appearance fields, including relative-alignment anchors.
It does not call the detector or compiler. The isolated fragment closes external
portals; lost context is counted. Out-of-domain cases remain failures to cover,
not silently discarded examples. Distinct result content appends validation
history; identical results are idempotent.

`validation.py` separately checks references, loops, portal reciprocity and
flat-plane ordering. Arbitrary self-intersections, slopes and collision-height
assumptions remain explicitly partial. XMapEdit rendering is independent visual
evidence; NBlood initialization is a different claim from interaction or play.
Neither promotes a recipe automatically. There is no aggregate quality score.

`reconstruction.py` adds a separate, explicitly post-hoc fitted diagnostic:
surface settings come from the held-out target, geometry from the trusted
recipe. `StepAppearance` stays inside `LevelScene`; the compiler owns native
record numbering and translates cardinal wall identities after anchor rotation.
`fixture.py` is evaluation apparatus only. It rejects footprint, first-wall,
height or topology mismatch before inserting the compiled fragment into a
source-held-fixed map. It restores native links to original context, never
overwrites generated surfaces with source surfaces, and audits unchanged
outside sectors/walls/sprites. It does not introduce another authoring model.

`visual_fidelity.py` checks observation identities and matching controlled
poses before comparing PNG pixels. Fitted visual reports join the immutable
benchmark history only after their MAP hashes and indexed reconstruction
report agree. Native field fidelity, static rendered fidelity and behavioral
verification remain distinct. The unfinished NBlood bot is quarantined as an
experimental diagnostic; its output cannot establish traversal or promotion.

## Dependencies and current limits

Python 3.12 and SQLite/FTS5 are sufficient. Established geometry/graph/imaging
libraries were considered: the initial operations are integer rectangles,
edge matching, connected components and scalar PNG metadata, so a large
geometry stack adds no needed robustness here. Before expanding to general
polygon unions/intersections, evaluate GEOS/Shapely instead of hand-building
that machinery. Use a graph library when graph matching exceeds these bounded
adjacency operations. The optional external tools keep their own versions and
content fingerprints in evidence.
Pixel comparisons now use optional Pillow rather than a custom PNG decoder.

## Flat area composition

Areas and landings lower to private rectangular geometry inside the compiler.
A landing references a stair endpoint, so its position and floor follow stair
edits. Explicit connections operate on named cardinal boundaries, or named
stair ends. Only positive collinear overlap becomes a reciprocal native portal;
unconnected touching edges remain closed. Native allocation and wall subdivision
remain compiler responsibilities. Non-overlap checks use a sweep over exact
integer rectangles, and splits use one-dimensional intervals; polygon Boolean
operations and general-purpose geometry dependencies are unnecessary for this
bounded domain.

Subdivision retains the original first-wall origin/orientation. It apportions
wall repeat by segment length and advances panning from the directed wall start;
non-integral native texture repeats and unsupported flipped splits fail instead
of silently stretching textures. Semantic object maps include stable area,
landing, step and connection references; numeric native indices may change when
geometry changes. Material-only edits leave other native records unchanged.

Workspace JSON accepts the legacy stair-only shape and optional area/landing/
connection collections. Transactions validate the complete proposed scene, so
dependent objects can be added or removed together. Undo restores every scene
collection. Semantic cameras are resolved only against a byte-matching compiled
scene revision. The authored connected demo is distinct from the source-held-fixed
reconstruction fixture and makes no material-learning or traversal claim.

The present database is about 1.25 GB for 4.55 million native records and
1.23 million observations. Repeating SHA strings and portal JSON is measurable
storage overhead; integer content keys/compact portal tables are a justified
future migration if corpus growth requires it. No spatial index exists yet:
current queries do not need range search, so R*Tree would be unused scaffolding.
