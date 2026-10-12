"""Persist independent tool outputs by content identity, without changing claims."""
from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .store import artifact_put, canonical_json, database, digest


def record_tool_evidence(config: Config, result: dict, kind: str) -> dict:
    """Index a tool report and its PNGs in workspace.db and the artifact store."""
    records = []
    for frame in result.get("screenshots", []):
        if not frame.get("present"):
            continue
        path = Path(frame["path"])
        sha, artifact = artifact_put(config.data_root, path.read_bytes(), ".png")
        records.append((sha, str(artifact), "native-observer-frame", {
            "view_id": frame.get("id"), "source_map": result.get("map"),
            "observer_manifest": result.get("observer_manifest"), "tool_kind": kind}))
    sha, artifact = artifact_put(config.data_root,
                                 (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(), ".json")
    records.append((sha, str(artifact), kind, {"source_map": result.get("map"),
                                            "status": result.get("status"), "claims": result.get("claims")}))
    with database(config.workspace_db, workspace=True) as db, db:
        for item_hash, path, record_kind, evidence in records:
            db.execute("INSERT OR IGNORE INTO outputs VALUES(?,NULL,NULL,?,?,?)",
                       (item_hash, path, record_kind, canonical_json(evidence)))
    return {"report_hash": sha, "report_path": str(artifact),
            "artifacts": [{"sha256": key, "path": path, "kind": record_kind} for key, path, record_kind, _ in records]}


def bundle_checkpoint(config: Config, benchmark_path: Path, comparison_path: Path,
                      source_observation: Path, reconstruction_observation: Path,
                      runtime_path: Path) -> dict:
    """Link independently recorded evidence by MAP hash, preserving old results."""
    paths = [benchmark_path, comparison_path, source_observation, reconstruction_observation, runtime_path]
    benchmark, comparison, source_view, generated_view, runtime = [
        json.loads(path.read_text(encoding="utf-8-sig")) for path in paths]
    map_hash = comparison["lineage"]["artifact_hash"]
    if runtime["map"]["sha256"] != map_hash or generated_view["input_integrity"]["before"]["map"]["sha256"] != map_hash:
        raise ValueError("runtime/renderer evidence does not refer to the same generated MAP")
    if source_view["input_integrity"]["before"]["map"]["sha256"] != comparison["lineage"]["source_sha256_expected"]:
        raise ValueError("source observation does not match the held-out source identity")
    matched = [r for r in benchmark["results"] if r.get("artifact_hash") == map_hash]
    if len(matched) != 1:
        raise ValueError("expected one benchmark occurrence for the externally observed MAP")
    if matched[0]["source"]["sha256"] != comparison["lineage"]["source_sha256_expected"]:
        raise ValueError("benchmark source identity differs from the paired visual observation")
    if matched[0]["source_sectors"] != [r["source_sector_index"] for r in comparison["correspondence"]]:
        raise ValueError("benchmark native-object correspondence differs from the paired observation")
    stored = {}
    for name, value in zip(("benchmark", "visual_comparison", "source_observation", "reconstruction_observation", "runtime"),
                           (benchmark, comparison, source_view, generated_view, runtime)):
        stored[name] = record_tool_evidence(config, value, name)
    augmented = dict(matched[0])
    augmented["visual"] = {"status": "observed", "fidelity_passed": False,
                           "source_render_status": source_view["status"],
                           "generated_render_status": generated_view["status"],
                           "comparison_report": stored["visual_comparison"]["report_path"],
                           "scope": comparison["scope"]}
    augmented["runtime"] = {"status": runtime["status"], "claims": runtime["claims"],
                            "report_path": stored["runtime"]["report_path"]}
    with database(config.knowledge_db) as db, db:
        db.execute("INSERT OR IGNORE INTO benchmarks(id,recipe_id,heldout_pattern_id,result_json,artifact_hash) VALUES(?,?,?,?,?)",
                   (digest(augmented), benchmark["recipe_id"], augmented["pattern_id"], canonical_json(augmented), map_hash))
    checkpoint = {"schema": "bloodforge.checkpoint.v1", "recipe_id": benchmark["recipe_id"],
                  "heldout_candidates": benchmark["heldout_candidates"],
                  "structurally_reproduced": benchmark["structurally_reproduced"],
                  "observed_artifact_hash": map_hash, "observed_source": augmented["source"],
                  "visual": augmented["visual"], "runtime": augmented["runtime"],
                  "recipe_promoted_to_verified": False, "evidence": stored}
    report = record_tool_evidence(config, checkpoint, "checkpoint")
    checkpoint.update(report)
    (config.data_root / "checkpoint.json").write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
    return checkpoint


def record_fitted_visual(config: Config, reconstruction_path: Path,
                         source_observation: Path, candidate_observation: Path) -> dict:
    """Append a paired-view diagnostic only when its recipe and MAP identities agree."""
    from .visual_fidelity import compare_observations

    reconstructed = json.loads(reconstruction_path.read_text(encoding="utf-8-sig"))
    if reconstructed.get("schema") != "bloodforge.fitted-reconstruction.v1":
        raise ValueError("expected a fitted reconstruction report")
    # A report must already be in the append-only knowledge history. Do not
    # attach observations to a hand-edited report with invented lineage.
    with database(config.knowledge_db) as db:
        known = db.execute("SELECT result_json FROM benchmarks WHERE id=?", (digest(reconstructed),)).fetchone()
    if known is None or json.loads(known[0]) != reconstructed:
        raise ValueError("reconstruction report is not an indexed benchmark result")
    comparison = compare_observations(source_observation, candidate_observation)
    actual_source = comparison["runs"]["source"]["map"]["sha256"]
    actual_candidate = comparison["runs"]["candidate"]["map"]["sha256"]
    if actual_source != reconstructed["source"]["sha256"]:
        raise ValueError("paired source does not match reconstruction source identity")
    fixture = reconstructed.get("context_fixture")
    if fixture and actual_candidate == fixture["artifact_hash"]:
        scope = "compiled stairs in source-held-fixed context; surrounding map, sprites and mechanisms are original"
    elif actual_candidate == reconstructed["artifact_hash"]:
        scope = "isolated fitted stair fragment"
    else:
        raise ValueError("paired candidate does not match a reconstruction output")
    comparison["reconstruction"] = {"recipe_id": reconstructed["recipe_id"],
                                    "pattern_id": reconstructed["pattern_id"], "scope": scope,
                                    "benchmark_result_id": digest(reconstructed),
                                    "reconstruction_report_path": str(reconstruction_path.resolve()),
                                    "appearance_policy": reconstructed["appearance_policy"]}
    saved = record_tool_evidence(config, comparison, "fitted-paired-visual-diagnostic")
    augmented = dict(reconstructed)
    augmented["visual"] = {"status": "compared", "scope": scope, "comparison": saved,
                           "views": comparison["views"], "claims": comparison["claims"]}
    with database(config.knowledge_db) as db, db:
        db.execute("INSERT OR IGNORE INTO benchmarks(id,recipe_id,heldout_pattern_id,result_json,artifact_hash) VALUES(?,?,?,?,?)",
                   (digest(augmented), augmented["recipe_id"], augmented["pattern_id"],
                    canonical_json(augmented), augmented["artifact_hash"]))
    return {"recipe_id": augmented["recipe_id"], "pattern_id": augmented["pattern_id"],
            "scope": scope, "views": comparison["views"], "promoted_to_verified": False, **saved}
