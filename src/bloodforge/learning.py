"""Traceable staircase mining, conservative splits and bounded reconstruction.

The executable constructor is a trusted, human/LLM-designed primitive. Training
infers its empirical parameter domain; it does not synthesize or execute code.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable

from .compiler import compile_scene
from .config import Config
from .native import encode_map, parse_map
from .patterns import discover_stairs
from .scene import LevelScene, StairSpec
from .store import EXTRACTION_VERSION, artifact_put, canonical_json, database, digest, load_native

MINER_VERSION = f"rect-stairs-2/{EXTRACTION_VERSION}"
RECIPE_FAMILY = "stairs/rect-axis/straight/v1"
QUARANTINED = ("generated", "own-conversion", "unclassified")
LIMITATIONS = [
    "Trusted staircase composition was implemented with LLM assistance; learned values are parameter bounds and observed support, not synthesized Python code.",
    "Only static, axis-aligned, straight, constant-rise, equal rectangular treads and constant ceilings are covered.",
    "Source external portals close at fragment boundaries; surrounding rooms, enemies, props, mechanisms and progression are not reconstructed.",
    "Homogeneous tile choice uses a source sample; shade, palette, panning, scale and heterogeneous material layouts are not reproduced.",
    "Map-level held-out groups conservatively merge geometry/revision buckets; undetected revisions and absent author metadata remain leakage risks.",
    "Native and structural fidelity do not demonstrate player traversal or meaningful playability.",
]


def eligible_contents(db: sqlite3.Connection, population: str) -> list[sqlite3.Row]:
    if population in QUARANTINED:
        raise ValueError(f"{population} is excluded from construction evidence; use corpus queries for native inspection")
    populations = ("blood-campaign", "blood-bloodbath") if population == "original" else (population,)
    placeholders = ",".join("?" for _ in populations)
    # A conflicting generated/conversion alias must never donate design evidence.
    return db.execute(f"""SELECT DISTINCT c.* FROM contents c JOIN sources s ON s.sha256=c.sha256
        WHERE c.parse_status='ok' AND s.active=1 AND s.population IN ({placeholders})
        AND NOT EXISTS (SELECT 1 FROM sources q WHERE q.sha256=c.sha256 AND q.active=1
            AND q.population IN ('generated','own-conversion','unclassified'))
        ORDER BY c.sha256""", populations).fetchall()


def source_reference(db: sqlite3.Connection, sha: str, population: str | None = None) -> dict:
    params: list = [sha]
    where = ""
    if population == "original":
        where = " AND s.population IN ('blood-campaign','blood-bloodbath')"
    elif population:
        where = " AND s.population=?"
        params.append(population)
    row = db.execute("""SELECT s.*,r.path root_path FROM sources s JOIN roots r ON r.id=s.root_id
        WHERE s.sha256=? AND s.active=1""" + where + " ORDER BY s.is_view,s.relative_path LIMIT 1", params).fetchone()
    if row is None:
        raise ValueError(f"missing active source {sha}")
    return {"sha256": sha, "relative_path": row["relative_path"], "root": row["root_path"],
            "population": row["population"], "tier": row["tier"], "author": row["author"]}


def mine_stairs(config: Config, population: str = "blood-campaign",
                progress: Callable[[dict], None] | None = None) -> dict:
    with database(config.knowledge_db) as db:
        sources = eligible_contents(db, population)
        processed, skipped, errors = 0, 0, []
        for row in sources:
            sha = row["sha256"]
            stamp = db.execute("SELECT 1 FROM observations WHERE sha256=? AND extractor=? AND kind='stair_mining' AND object_id=?",
                               (sha, MINER_VERSION, EXTRACTION_VERSION)).fetchone()
            if stamp:
                skipped += 1
                continue
            try:
                level = load_native(db, sha)
                candidates = discover_stairs(level)
                with db:
                    for candidate in candidates:
                        params = candidate.parameters
                        normalized = {"count": params["count"],
                                      "width_over_tread": str(Fraction(params["width"], params["tread"])),
                                      "rise_over_tread_native_units": str(Fraction(params["rise"], params["tread"]))}
                        # Immutable candidates retain evidence for old recipes.
                        pid = digest([MINER_VERSION, sha, candidate.source_sector_indices])
                        db.execute("INSERT OR IGNORE INTO patterns VALUES(?,?,?,?,?,?,?,?)", (
                            pid, sha, candidate.family_signature, digest(normalized), MINER_VERSION,
                            canonical_json(params), canonical_json({**candidate.to_dict(), "normalized": normalized}), "candidate"))
                    db.execute("INSERT INTO observations VALUES(?,?,?,?,?)", (sha, MINER_VERSION, "stair_mining", EXTRACTION_VERSION,
                               canonical_json({"candidates": len(candidates), "status": "completed"})))
                processed += 1
            except Exception as exc:
                errors.append({"sha256": sha, "error": f"{type(exc).__name__}: {exc}"})
            if progress:
                progress({"event": "mining", "processed": processed, "skipped": skipped, "total": len(sources)})
        rows = pattern_rows(db, population)
        grouped = split_groups(db, population)
        summary = {"population": population, "input_maps": len(sources), "processed": processed, "skipped": skipped,
                   "candidates": len(rows), "distinct_map_hashes": len({r["sha256"] for r in rows}),
                   "conservative_map_groups": len({grouped["groups"][r["sha256"]] for r in rows}),
                   "normalized_groups": len({r["signature"] for r in rows}), "errors": errors,
                   "status": "candidates-only", "extractor": MINER_VERSION}
        with db:
            db.execute("INSERT OR REPLACE INTO mining_runs(extractor,population,input_digest,summary_json) VALUES(?,?,?,?)",
                       (MINER_VERSION, population, digest([r["sha256"] for r in sources]), canonical_json(summary)))
        return summary


def pattern_rows(db: sqlite3.Connection, population: str = "blood-campaign") -> list[sqlite3.Row]:
    hashes = {r["sha256"] for r in eligible_contents(db, population)}
    return [r for r in db.execute("SELECT * FROM patterns WHERE extractor=? ORDER BY id", (MINER_VERSION,))
            if r["sha256"] in hashes]


def split_groups(db: sqlite3.Connection, population: str, *, seed: str = "bloodforge-stairs-v1") -> dict:
    rows = eligible_contents(db, population)
    parents = {r["sha256"]: r["sha256"] for r in rows}

    def find(item):
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    def union(a, b):
        first, second = sorted((find(a), find(b)))
        parents[second] = first

    buckets = {}
    for row in rows:
        sha = row["sha256"]
        keys = [(kind, row[kind]) for kind in ("geometry_signature", "similarity_signature") if row[kind]]
        keys += [("author", r[0].strip().casefold()) for r in db.execute(
            "SELECT DISTINCT author FROM sources WHERE sha256=? AND author IS NOT NULL AND active=1", (sha,)) if r[0].strip()]
        for key in keys:
            if key in buckets:
                union(sha, buckets[key])
            else:
                buckets[key] = sha
    groups = {}
    for sha in sorted(parents):
        groups.setdefault(find(sha), []).append(sha)
    assignments, group_ids = {}, {}
    for members in groups.values():
        gid = digest(members)
        # Frozen split decision uses only source identity / revision grouping,
        # never candidate parameters, fit quality or benchmark success.
        split = "heldout" if int(digest([seed, gid])[:8], 16) % 5 == 0 else "training"
        for sha in members:
            assignments[sha] = split
            group_ids[sha] = gid
    return {"seed": seed, "population": population, "assignments": assignments, "groups": group_ids,
            "group_count": len(groups), "policy": "sha+translation-normalized-geometry+coarse-revision-bucket+known-author-v1"}


def infer_recipe(config: Config, population: str = "blood-campaign") -> dict:
    with database(config.knowledge_db) as db:
        split = split_groups(db, population)
        all_patterns = pattern_rows(db, population)
        training = [r for r in all_patterns if split["assignments"][r["sha256"]] == "training"]
        support = {r["sha256"] for r in training}
        if len({split["groups"][sha] for sha in support}) < 2:
            raise ValueError("recipe inference requires two independent training map groups")
        parameters = [json.loads(r["parameters_json"]) for r in training]
        bounds = {key: [min(p[key] for p in parameters), max(p[key] for p in parameters)]
                  for key in ("count", "width", "tread", "rise")}
        definition = {"constructor": "trusted.rect_stair.v1", "parameters": bounds,
                      "directions": ["east", "north", "south", "west"],
                      "observed_training_directions": sorted({p["direction"] for p in parameters}),
                      "orientation_policy": "cardinal rigid rotations are a trusted primitive invariant; empirical sizes remain bounded",
                      "preconditions": ["positive dimensions and rise", "every floor strictly below the constant ceiling",
                                        "signed-native-coordinate-bounds", "one isolated connected stair run"],
                      "outputs": ["native flat step sectors", "reciprocal open internal portals", "MAP header start"],
                      "learning": "empirical parameter bounds; no arbitrary code execution", "split": split,
                      "upstream_extraction_version": EXTRACTION_VERSION,
                      "heldout_snapshot": [{"pattern_id": r["id"], "sha256": r["sha256"],
                                            "source": source_reference(db, r["sha256"], population)}
                                           for r in all_patterns if split["assignments"][r["sha256"]] == "heldout"]}
        evidence = {"pattern_ids": [r["id"] for r in training], "map_hashes": sorted(support),
                    "independent_groups": len({split["groups"][sha] for sha in support}),
                    "authors": sorted({r[0] for sha in support for r in db.execute(
                        "SELECT DISTINCT author FROM sources WHERE sha256=? AND active=1 AND author IS NOT NULL", (sha,))}),
                    "author_support_known": False, "frequency_is_not_design_quality": True}
        recipe_id = "rect-stairs-" + digest([definition, evidence])[:16]
        with db:
            db.execute("INSERT OR IGNORE INTO recipes VALUES(?,?,?,?,?,?,?)", (
                recipe_id, RECIPE_FAMILY, 1, "candidate", canonical_json(definition),
                canonical_json(evidence), canonical_json(LIMITATIONS)))
        return {"recipe_id": recipe_id, "status": db.execute("SELECT status FROM recipes WHERE id=?", (recipe_id,)).fetchone()[0],
                "parameter_bounds": bounds, "training_maps": len(support),
                "training_instances": len(training), "independent_groups": evidence["independent_groups"],
                "heldout_maps": sum(value == "heldout" for value in split["assignments"].values()),
                "limitations": LIMITATIONS}


def evaluate_recipe(config: Config, recipe_id: str) -> dict:
    from .fidelity import compare_stair
    from .validation import validate_map

    with database(config.knowledge_db) as db:
        recipe = db.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
        if recipe is None:
            raise ValueError(f"unknown recipe {recipe_id}")
        definition = json.loads(recipe["definition_json"])
        split = definition["split"]
        # Frozen memberships are retained in the recipe; newly ingested maps
        # cannot silently join an existing benchmark.
        if "heldout_snapshot" not in definition:
            raise ValueError("recipe predates frozen benchmark membership; run recipes infer for a current recipe")
        eligible_now = {r["sha256"] for r in eligible_contents(db, split["population"])}
        outcomes = []
        for member in definition["heldout_snapshot"]:
            row = db.execute("SELECT * FROM patterns WHERE id=?", (member["pattern_id"],)).fetchone()
            if row is None:
                outcomes.append({"pattern_id": member["pattern_id"], "source": member["source"],
                                 "status": "missing_evidence", "reason": "frozen pattern unavailable; denominator retained"})
                continue
            params = json.loads(row["parameters_json"])
            evidence = json.loads(row["evidence_json"])
            source = member["source"]
            outside = [key for key, (low, high) in definition["parameters"].items() if not low <= params[key] <= high]
            if params["direction"] not in definition["directions"]:
                outside.append("direction")
            result = {"pattern_id": row["id"], "source": source, "source_sectors": evidence["source_sector_indices"],
                      "source_portal_walls": evidence["portal_wall_ids"], "recipe_id": recipe_id,
                      "split_group": split["groups"][row["sha256"]], "parameters": params,
                      "manual_interventions": 0, "limitations": LIMITATIONS,
                      "evaluation_version": "rect-fidelity-2"}
            artifact_hash = ""
            try:
                if row["sha256"] not in eligible_now:
                    result.update(status="ineligible_evidence", reason="source removed or quarantined after inference; denominator retained")
                elif outside:
                    result.update(status="outside_training_domain", outside_parameters=outside)
                else:
                    native_source = load_native(db, row["sha256"])
                    sector = native_source.sectors[evidence["source_sector_indices"][0]]
                    stair = StairSpec(id="reconstructed-stair", **params, floor_tile=sector.floorpicnum,
                                      ceiling_tile=sector.ceilingpicnum,
                                      wall_tile=native_source.walls[sector.wallptr].picnum)
                    scene = LevelScene(stairs=(stair,))
                    compiled = compile_scene(scene)
                    payload = encode_map(compiled.native_map)
                    reread = parse_map(payload)
                    validation = validate_map(reread)
                    comparison = compare_stair(native_source, evidence["source_sector_indices"], reread)
                    comparison["structural_exact"] = bool(comparison["sector_count_matches"] and
                        comparison["geometry"]["polygon_exact_fraction"] == 1 and
                        comparison["geometry"]["max_floor_z_error"] == 0 and
                        comparison["geometry"]["max_ceiling_z_error"] == 0 and
                        comparison["topology"]["internal_topology_matches"])
                    artifact_hash, artifact_path = artifact_put(config.data_root, payload, ".map")
                    result.update(status="evaluated", artifact_hash=artifact_hash, artifact_path=str(artifact_path),
                                  native_roundtrip_exact=encode_map(reread) == payload,
                                  validation=validation, fidelity=comparison, scene=asdict(scene),
                                  object_map=compiled.object_map,
                                  runtime={"status": "not_run", "claim": "no_traversal_or_playability_claim"},
                                  visual={"status": "not_run"})
                    with database(config.workspace_db, workspace=True) as workspace:
                        from .workspace import create_scene
                        project_id = recipe_id + "-" + row["id"][:12]
                        if workspace.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                            create_scene(workspace, project_id, "Held-out stair reconstruction", scene)
                        with workspace:
                            workspace.execute("INSERT OR REPLACE INTO outputs VALUES(?,?,?,?,?,?)", (
                                artifact_hash, project_id, 0, str(artifact_path), "blood-map-v7",
                                canonical_json({"recipe_id": recipe_id, "source": source, "pattern_id": row["id"]})))
            except Exception as exc:
                result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            # Identical re-evaluation is idempotent; changed evidence appends a
            # new result instead of erasing prior validation history.
            bid = digest([recipe_id, row["id"], result])
            with db:
                db.execute("INSERT OR IGNORE INTO benchmarks(id,recipe_id,heldout_pattern_id,result_json,artifact_hash) VALUES(?,?,?,?,?)",
                           (bid, recipe_id, row["id"], canonical_json(result), artifact_hash))
            outcomes.append(result)
        # Reproduced is intentionally weaker than verified. Functional and
        # visual generalization checks are still required for promotion.
        reproduced = [r for r in outcomes if r.get("status") == "evaluated" and
                      r.get("native_roundtrip_exact") and r.get("fidelity", {}).get("structural_exact") and
                      r.get("validation", {}).get("summary", {}).get("error_count", 1) == 0]
        if reproduced:
            with db:
                db.execute("UPDATE recipes SET status='reproduced' WHERE id=? AND status='candidate'", (recipe_id,))
        summary = {"recipe_id": recipe_id, "status": "reproduced" if reproduced else "candidate",
                   "heldout_candidates": len(outcomes), "structurally_reproduced": len(reproduced),
                   "counts": dict(Counter(r["status"] for r in outcomes)), "results": outcomes,
                   "promoted_to_verified": False}
        report_bytes = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
        report_hash, report_path = artifact_put(config.data_root, report_bytes, ".json")
        summary["report_path"] = str(report_path)
        summary["report_hash"] = report_hash
        return summary
