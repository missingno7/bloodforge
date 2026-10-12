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
monotonic revision. Multi-room composition and mechanism channel allocation are
not implemented behind speculative interfaces.

## Storage

`knowledge.db` uses schema version 1, an application ID, foreign keys, WAL,
typed native record tables, JSON-checked observation/evidence columns, FTS5,
and indexes for source identities, provenance, signatures and families.
`contents` owns SHA-256 identities; `sources` owns current path metadata and
`source_history` retains replaced identities/metadata. Tier copies remain
separate source locations with `is_view=1`. One extraction serves all aliases.

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

The present database is about 1.25 GB for 4.55 million native records and
1.23 million observations. Repeating SHA strings and portal JSON is measurable
storage overhead; integer content keys/compact portal tables are a justified
future migration if corpus growth requires it. No spatial index exists yet:
current queries do not need range search, so R*Tree would be unused scaffolding.
