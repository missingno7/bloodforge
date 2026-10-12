"""Small structured command surface; no LLM provider is required."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
import time

from .config import load_config
from .store import artifact_put, canonical_json, database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bloodforge", description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--corpus-root")
    parser.add_argument("--data-root")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create versioned metadata and workspace databases")
    corpus = commands.add_parser("corpus").add_subparsers(dest="action", required=True)
    ingest = corpus.add_parser("ingest")
    ingest.add_argument("--limit", type=int)
    ingest.add_argument("--retry-failed", action="store_true")
    corpus.add_parser("inspect")
    search = corpus.add_parser("search")
    search.add_argument("query")
    search.add_argument("--population")
    search.add_argument("--limit", type=int, default=20)
    duplicates = corpus.add_parser("duplicates")
    duplicates.add_argument("--kind", choices=("bytes", "geometry", "similarity"), default="bytes")
    duplicates.add_argument("--limit", type=int, default=20)
    patterns = commands.add_parser("patterns").add_subparsers(dest="action", required=True)
    mine = patterns.add_parser("mine")
    mine.add_argument("--population", default="blood-campaign")
    search = patterns.add_parser("search")
    search.add_argument("--population", default="blood-campaign")
    search.add_argument("--limit", type=int, default=20)
    inspect = patterns.add_parser("inspect")
    inspect.add_argument("id")
    recipes = commands.add_parser("recipes").add_subparsers(dest="action", required=True)
    infer = recipes.add_parser("infer")
    infer.add_argument("--population", default="blood-campaign")
    recipes.add_parser("list")
    evaluate = recipes.add_parser("evaluate")
    evaluate.add_argument("id")
    scene = commands.add_parser("scene").add_subparsers(dest="action", required=True)
    create = scene.add_parser("create")
    create.add_argument("id")
    create.add_argument("--file", required=True, type=Path)
    inspect = scene.add_parser("inspect")
    inspect.add_argument("id")
    apply = scene.add_parser("apply")
    apply.add_argument("id")
    apply.add_argument("--file", required=True, type=Path)
    apply.add_argument("--expected-revision", required=True, type=int)
    apply.add_argument("--dry-run", action="store_true")
    undo = scene.add_parser("undo")
    undo.add_argument("id")
    undo.add_argument("--expected-revision", required=True, type=int)
    diff = scene.add_parser("diff")
    diff.add_argument("id")
    diff.add_argument("--before", required=True, type=int)
    diff.add_argument("--after", required=True, type=int)
    level = commands.add_parser("level").add_subparsers(dest="action", required=True)
    compile_cmd = level.add_parser("compile")
    compile_cmd.add_argument("--project", required=True)
    validate = level.add_parser("validate")
    validate.add_argument("map", type=Path)
    observe = level.add_parser("observe")
    observe.add_argument("map", type=Path)
    observe.add_argument("--output", type=Path, required=True)
    observe.add_argument("--executable", type=Path)
    observe.add_argument("--assets", type=Path)
    observe.add_argument("--cameras", type=Path, help="JSON array of native camera poses; default MAP start")
    runtime = level.add_parser("test-load", help="bounded NBlood initialization smoke; not a playability test")
    runtime.add_argument("map", type=Path)
    runtime.add_argument("--output", type=Path, required=True)
    runtime.add_argument("--executable", type=Path)
    runtime.add_argument("--assets", type=Path)
    runtime.add_argument("--seconds", type=float, default=5)
    demo = commands.add_parser("demo", help="ingest, mine, infer and evaluate the first structural family")
    demo.add_argument("--population", default="blood-campaign")
    checkpoint = commands.add_parser("checkpoint", help="link independent tool evidence by source and output hashes")
    for name in ("benchmark", "comparison", "source-observation", "reconstruction-observation", "runtime"):
        checkpoint.add_argument("--" + name, type=Path, required=True)
    return parser


def _progress() -> object:
    last = 0.0

    def report(event):
        nonlocal last
        now = time.monotonic()
        if now - last >= 5 or event.get("error"):
            print(canonical_json({"event": "progress", **event}), file=sys.stderr, flush=True)
            last = now
    return report


def _compact_evaluation(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "results"} | {
        "artifacts": [{"source": row["source"]["relative_path"], "pattern_id": row["pattern_id"],
                       "map": row.get("artifact_path"), "status": row["status"],
                       "structural_exact": row.get("fidelity", {}).get("structural_exact", False)}
                      for row in value["results"]]}


def dispatch(args: argparse.Namespace) -> object:
    config = load_config(args.config, corpus_root=args.corpus_root, data_root=args.data_root)
    if args.command == "init":
        with database(config.knowledge_db), database(config.workspace_db, workspace=True):
            return {"knowledge_db": str(config.knowledge_db), "workspace_db": str(config.workspace_db),
                    "corpus_root": str(config.corpus_root), "schema_version": 1}
    if args.command == "checkpoint":
        from .evidence import bundle_checkpoint
        result = bundle_checkpoint(config, args.benchmark, args.comparison, args.source_observation,
                                   args.reconstruction_observation, args.runtime)
        return {key: result[key] for key in ("recipe_id", "heldout_candidates", "structurally_reproduced",
                                            "observed_artifact_hash", "visual", "runtime", "report_path")}
    if args.command == "corpus":
        from .corpus import corpus_search, corpus_summary, ingest
        if args.action == "ingest":
            started = time.perf_counter()
            result = ingest(config, progress=_progress(), limit=args.limit, retry_failed=args.retry_failed)
            result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            return result
        with database(config.knowledge_db) as db:
            if args.action == "inspect":
                return corpus_summary(db)
            if args.action == "search":
                return corpus_search(db, args.query, args.population, args.limit)
            if not 1 <= args.limit <= 500:
                raise ValueError("limit must be 1..500")
            if args.kind == "bytes":
                rows = db.execute("""SELECT sha256 AS group_id,COUNT(*) paths,SUM(1-is_view) source_paths
                    FROM sources WHERE active=1 GROUP BY sha256 HAVING COUNT(*)>1 ORDER BY paths DESC,sha256 LIMIT ?""",
                                  (args.limit,))
            else:
                column = {"geometry": "geometry_signature", "similarity": "similarity_signature"}[args.kind]
                rows = db.execute(f"""SELECT {column} AS group_id,COUNT(DISTINCT c.sha256) identities
                    FROM contents c WHERE {column} IS NOT NULL AND EXISTS
                    (SELECT 1 FROM sources s WHERE s.sha256=c.sha256 AND active=1)
                    GROUP BY {column} HAVING COUNT(DISTINCT c.sha256)>1 ORDER BY identities DESC,{column} LIMIT ?""", (args.limit,))
            return {"kind": args.kind, "claim": "exact-bytes" if args.kind == "bytes" else "revision-candidates-not-proof",
                    "groups": [dict(row) for row in rows]}
    if args.command == "patterns":
        from .learning import mine_stairs, pattern_rows, source_reference
        if args.action == "mine":
            return mine_stairs(config, args.population, _progress())
        with database(config.knowledge_db) as db:
            if args.action == "inspect":
                row = db.execute("SELECT * FROM patterns WHERE id=?", (args.id,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown pattern {args.id}")
                return dict(row) | {"parameters": json.loads(row["parameters_json"]),
                                    "evidence": json.loads(row["evidence_json"]),
                                    "source": source_reference(db, row["sha256"])}
            if not 1 <= args.limit <= 500:
                raise ValueError("limit must be 1..500")
            rows = pattern_rows(db, args.population)
            return {"total": len(rows), "results": [
                {"id": row["id"], "family": row["family"], "parameters": json.loads(row["parameters_json"]),
                 "status": row["status"], "source": source_reference(db, row["sha256"], args.population)}
                for row in rows[:args.limit]]}
    if args.command == "recipes":
        from .learning import evaluate_recipe, infer_recipe
        if args.action == "infer":
            return infer_recipe(config, args.population)
        if args.action == "evaluate":
            return _compact_evaluation(evaluate_recipe(config, args.id))
        with database(config.knowledge_db) as db:
            return [dict(row) for row in db.execute("SELECT id,family,version,status FROM recipes ORDER BY id")]
    if args.command == "scene":
        from .scene import PlayerStart, SceneChange, StairSpec
        from .workspace import (apply_scene_changes, create_scene, diff_scene, inspect_scene,
                                scene_from_dict, scene_to_dict, undo_scene)
        with database(config.workspace_db, workspace=True) as db:
            if args.action == "create":
                scene = scene_from_dict(json.loads(args.file.read_text(encoding="utf-8-sig")))
                create_scene(db, args.id, args.id, scene)
                return {"project_id": args.id, "scene": scene_to_dict(scene)}
            if args.action == "inspect":
                return scene_to_dict(inspect_scene(db, args.id))
            if args.action == "undo":
                return scene_to_dict(undo_scene(db, args.id, expected_revision=args.expected_revision))
            if args.action == "diff":
                scenes = []
                for revision in (args.before, args.after):
                    row = db.execute("SELECT scene_json FROM revisions WHERE project_id=? AND revision=?", (args.id, revision)).fetchone()
                    if row is None:
                        raise ValueError(f"missing scene revision {revision}")
                    scenes.append(scene_from_dict(json.loads(row[0])))
                return diff_scene(*scenes)
            values = json.loads(args.file.read_text(encoding="utf-8-sig"))
            changes = []
            for value in values:
                cls = PlayerStart if value["operation"] == "set_player_start" else StairSpec
                changes.append(SceneChange(value["operation"], value["object_id"],
                                           cls(**value["expected"]) if value.get("expected") is not None else None,
                                           cls(**value["value"]) if value.get("value") is not None else None))
            result = apply_scene_changes(db, args.id, tuple(changes), expected_revision=args.expected_revision,
                                         dry_run=args.dry_run)
            return {"scene": scene_to_dict(result.scene), "changed_ids": result.changed_ids, "committed": result.committed}
    if args.command == "level":
        from .native import encode_map, read_map
        from .validation import validate_map
        if args.action == "validate":
            return validate_map(read_map(args.map))
        if args.action == "test-load":
            from .evidence import record_tool_evidence
            from .runtime import test_load
            binary = args.executable or config.nblood
            assets = args.assets or config.game_assets
            if config.reference_root:
                binary = binary or config.reference_root / "reference" / "blood" / "nblood.exe"
                assets = assets or config.reference_root / "reference" / "blood"
            if binary is None or assets is None:
                raise ValueError("set nblood/game_assets or pass --executable/--assets")
            result = test_load(args.map, args.output, binary, assets, data_root=config.data_root, seconds=args.seconds)
            saved = record_tool_evidence(config, result, "nblood-load-smoke")
            return {key: result.get(key) for key in ("status", "claims", "engine", "initialized_markers", "fatal_indicators", "outputs")} | saved
        if args.action == "compile":
            from .compiler import compile_scene
            from .workspace import inspect_scene
            with database(config.workspace_db, workspace=True) as db:
                scene = inspect_scene(db, args.project)
                result = compile_scene(scene)
                sha, path = artifact_put(config.data_root, encode_map(result.native_map), ".map")
                with db:
                    db.execute("INSERT OR REPLACE INTO outputs VALUES(?,?,?,?,?,?)", (
                        sha, args.project, scene.revision, str(path), "blood-map-v7", canonical_json(result.object_map)))
            return {"artifact_hash": sha, "artifact_path": str(path), "object_map": result.object_map,
                    "validation": validate_map(result.native_map)}
        from .observe import CameraPose, observe_map
        level = read_map(args.map)
        cameras = json.loads(args.cameras.read_text(encoding="utf-8-sig")) if args.cameras else CameraPose(
            "entry", level.start_x, level.start_y, level.start_z, level.start_angle, sector=level.start_sector)
        binary = args.executable or config.observer
        assets = args.assets or config.game_assets
        if config.reference_root:
            binary = binary or config.reference_root / "xmapedit" / "bin" / "xmapedit-observe.exe"
            assets = assets or config.reference_root / "reference" / "blood"
        if binary is None or assets is None:
            raise ValueError("set observer/game_assets in local.toml or pass --executable/--assets")
        result = observe_map(args.map, args.output, binary, assets, cameras, data_root=config.data_root)
        from .evidence import record_tool_evidence
        full_result = json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
        saved = record_tool_evidence(config, full_result, "native-observation")
        return {key: result.get(key) for key in ("status", "claims", "screenshots", "observer_manifest")} | saved
    if args.command == "demo":
        from .corpus import ingest
        from .learning import evaluate_recipe, infer_recipe, mine_stairs
        ingestion = ingest(config, _progress())
        mining = mine_stairs(config, args.population, _progress())
        recipe = infer_recipe(config, args.population)
        evaluation = evaluate_recipe(config, recipe["recipe_id"])
        return {"ingestion": ingestion, "mining": mining, "recipe": recipe,
                "evaluation": _compact_evaluation(evaluation)}
    raise ValueError("unknown command")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        if isinstance(result, dict) and (result.get("status") in {"failed", "crashed", "timeout", "invalid_input", "asset_modified", "asset_check_failed", "map_modified", "executable_modified", "unavailable"} or result.get("summary", {}).get("status") == "fail"):
            return 1
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(canonical_json({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(canonical_json({"status": "interrupted", "message": "durable ingestion checkpoints are retained"}), file=sys.stderr)
        return 130
