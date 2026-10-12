# Blood corpus reconnaissance

Read-only inventory of `D:\Games\DOS\llmapper\maps\blood`, prepared 2026-10-12. The source tree was not modified. No `AGENTS.md` was found under `D:\`, `D:\Games`, `D:\Games\DOS`, `bloodforge`, or `llmapper`.

## Inventory and schemas

The directory contains 3,235 `.map` files (376,733,191 bytes), three JSON manifests (87,855,047 bytes), and `xmapedit.chm` (16,570,707 bytes). `corpus.json` is the source-of-truth path manifest (`$schema: llmapper.blood-corpus-manifest`, version 1): 1,773 rows, 1,701 distinct SHA-256 values. A row looks like:

```json
{"relative":"campaign/E1M1.MAP","filename":"E1M1.MAP","bytes":111431,"sha256":"8c84150d...","population":"blood-campaign","provenance_directory":"campaign","mode":"sp"}
```

The 1,773 manifest rows comprise the source populations below. `tiered/` adds 1,462 physical copies/views, so count source rows once and treat those copies only as tier metadata or sample selection.

| Path | Population | Maps | Mode evidence |
|---|---|---:|---|
| `campaign/` | `blood-campaign` | 43 | `sp` |
| `campaign/multiplayer/` | `blood-bloodbath` | 9 | `multiplayer` |
| `community/` | `community` | 1,500 | unknown from path |
| `conversions/` | `own-conversion` | 4 | unknown |
| `curated/` | `community-curated` | 46 | curated root includes SP and `multiplayer/` maps |
| `mechanism/` | `mechanism-tutorial` | 171 | unknown |

Views in `corpus.json`: `original` is the 52 original campaign/BloodBath maps; `reference` is those 52 plus 46 curated community maps. The manifest's tier totals are S 383, A 175, B 169, C 52, bloodbath 537, mechanism 12, questionable 134, untiered 38. The tiered directory has 1,462 map copies: S 383, A 175, B 169, C 52, bloodbath 537, mechanism 12, questionable 134. The 38 health-gate failures remain in `community/` without a tier copy.

`tiered/tier-manifest.json` (`llmapper.blood-corpus-classification`, version 1) has 1,462 records. Useful fields include `source_relative`, `source_sha256`, `destination`, `classification`, `quality_score`, `quality_tier`, `status`, `reasons`, `special_purpose_evidence`, and analyzer-derived measurements. Its `destination` and provenance references use another checkout's absolute paths in metadata; join to the current source using relative path/hash fields. `tiered/tier-summary.json` is an aggregate summary, not another map population.

## Exact duplicates and attribution

I hashed all 3,235 physical map files. There are 1,701 distinct SHA-256 values and 1,390 duplicate groups: 1,318 groups contain two files and 72 groups contain four. The groups of two are usually a source map and its tiered copy. Each group of four combines a pair of byte-identical community paths with their two tiered copies. Within the 1,773 source manifest rows, the 72 duplicate groups are all within `community/`; `cross_population_duplicates` is empty. Preserve alternate path/name provenance even when deduplicating content.

The corpus and tier manifests contain no author/mapper credit or human-annotation field. `reasons`, `special_purpose_evidence`, `unusual_features`, validation/sensor fields, and measurements are useful structured annotations, but are analyzer-produced evidence, not author assertions. `xmapedit.chm` is present as editor documentation; it was counted but not parsed. Mechanism maps are 171 total: 38 directly under `mechanism/`, 98 under `mechanism/Vanilla/`, and 35 under `mechanism/Modern/`. Names commonly describe the demonstration/mechanism (`SPRITE-*`, `TRAP-*`, `WALL-*`, `STACKS3DSPACES-*`), but there are no separate readme or credit files.

## Deterministic classification guidance

1. Normalize separators to `/` and classify by the first path segment, with the more specific `campaign/multiplayer/` test before `campaign/`. Use the manifest `relative` path as the stable source key.
2. Assign source population from path: `campaign/multiplayer/` → `blood-bloodbath`; other `campaign/` → `blood-campaign`; `community/` → `community`; `curated/` → `community-curated`; `conversions/` → `own-conversion`; `mechanism/` → `mechanism-tutorial`.
3. Assign mode only where the directory states it: campaign root → SP; campaign/multiplayer → multiplayer. For curated, read the relative subpath or manifest mode. Keep community, conversions, and mechanism unknown. Do not infer mode from tier class or filenames.
4. Treat `tiered/<class>/` as a derived view. Join by `source_sha256` and `source_relative`; never count those files as new source examples. Use tier as a sampling aid only.
5. Use filename hints only as cross-checks. Path provenance wins: `DWE*` (Death Wish) and `TEDE*` are community sources; `DNE*` are the owner's Duke3D-to-Blood conversions. Only `campaign/multiplayer/BB1-BB9` is original BloodBath; curated multiplayer maps are community selections.
6. Keep SHA duplicate paths as aliases/provenance records, and deduplicate training content by SHA within the intended split. The 72 duplicated community hashes should be grouped before any train/eval split to avoid leakage.

Important pitfalls recorded by the manifest: community maps have not passed the native losslessness gate; parsing must fail closed and report skipped maps. `tiered/tier-manifest.json` contains foreign absolute paths. The two filenames flagged by `filename_hint_conflicts` are `mechanism/Vanilla/E1M1ZD1.map` and `mechanism/Vanilla/E1M1ZDF.map`; retain their mechanism provenance despite campaign-like filename hints. Tier counts sum to 1,462, while the 1,500 community sources include 38 untiered maps.

Machine-readable counts and every physical SHA duplicate group are in [corpus-inventory.json](../data/recon/corpus-inventory.json). The inventory contains only metadata and paths; no map assets or implementations were copied.
