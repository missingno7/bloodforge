"""Replay the source-backed spatial milestone; outputs stay outside the corpus.

Run with PYTHONPATH=src using the spatial+visual optional dependencies installed.
Predictions are read before interventions; failed predictions stay in the report.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path

from bloodforge.config import load_config
from bloodforge.evidence import record_tool_evidence
from bloodforge.native import encode_map, read_map
from bloodforge.observe import observe_map
from bloodforge.spatial import inspect_space, compare_spaces, diagram_svg, require_index_correspondence
from bloodforge.spatial_edits import intervene
from bloodforge.spatial_geometry import sample_route
from bloodforge.spatial_views import summarize_sequence, compare_sequences
from bloodforge.store import artifact_put
from bloodforge.visual_fidelity import compare_observations

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run(output: Path, reuse_recon: Path | None = None, reuse_run: Path | None = None) -> dict:
    config = load_config()
    output = output.resolve()
    if not output.is_relative_to(config.data_root.resolve()) or output.exists():
        raise ValueError("demo requires a fresh output directory below data_root")
    output.mkdir(parents=True)
    plan = json.loads((ROOT / "examples/spatial-interventions.json").read_text())
    save(output / "predictions-before-edits.json", plan)
    source = config.corpus_root / plan["source"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != plan["source_sha256"]:
        raise ValueError("demo source identity no longer matches frozen intervention plan")
    level = read_map(source)
    route = json.loads((ROOT / "examples/spatial-e2m5.route.json").read_text())
    cameras = sample_route(level, route, 1)
    for camera in cameras:
        camera["screenshot"] = True
    save(output / "e2m5.cameras.json", cameras)
    observer = config.observer or config.reference_root / "xmapedit/bin/xmapedit-observe.exe"
    assets = config.game_assets or config.reference_root / "reference/blood"

    def observe(path, name, poses):
        if reuse_run is not None and (reuse_run / name / "summary.json").is_file():
            summary = reuse_run / name / "summary.json"
            request = json.loads((summary.parent / "request.json").read_text())
            if request["views"] != poses or Path(request["map"]).resolve() != path.resolve():
                raise ValueError(f"{name} reused observation disagrees with MAP or pose request")
            # summarize_sequence and compare_observations revalidate every live
            # input and output hash before any reused observation earns a claim.
            return summary
        directory = output / name
        result = observe_map(path, directory, observer, assets, poses, data_root=config.data_root)
        if result["status"] != "completed":
            raise ValueError(f"{name} observation did not complete: {result['status']}")
        summary = directory / "summary.json"
        record_tool_evidence(config, json.loads(summary.read_text()), "spatial-demo-observation")
        return summary

    before = inspect_space(config, source, plan["sectors"])
    base_summary = observe(source, "e2m5-baseline", cameras)
    base_sequence = summarize_sequence(base_summary, before)
    record_tool_evidence(config, base_sequence, "spatial-perception")
    save(output / "e2m5-analysis.json", before)
    save(output / "e2m5-sequence.json", base_sequence)
    (output / "e2m5.svg").write_text(diagram_svg(level, before, cameras), encoding="utf-8")
    examples = [{"name": "E2M5", "source": before["source"], "analysis_report": before["report_path"],
                 "sequence": str(output / "e2m5-sequence.json"), "views": len(cameras), "diagram": "e2m5.svg"}]
    results = []
    observation_summaries = {"e2m5-baseline": str(base_summary)}
    for case in plan["cases"]:
        variant, audit = intervene(level, case["edit"])
        require_index_correspondence(level, variant)
        sha, path = artifact_put(config.data_root, encode_map(variant), ".map")
        native_audit = {"schema": "bloodforge.spatial-intervention.v1", "source_sha256": plan["source_sha256"],
                        "artifact_hash": sha, "artifact_path": str(path), "edit": case["edit"], "audit": audit,
                        "population": "controlled-counterfactual-not-independent-corpus-evidence"}
        audit_saved = record_tool_evidence(config, native_audit, "spatial-intervention")
        after = inspect_space(config, path, plan["sectors"])
        native = compare_spaces(before, after)
        summary = observe(path, case["id"], cameras)
        observation_summaries[case["id"]] = str(summary)
        sequence = summarize_sequence(summary, after)
        perception = compare_sequences(base_sequence, sequence)
        pixel_report = compare_observations(base_summary, summary)
        changed_pixels = [view["changed_pixel_count"] for view in pixel_report["views"]]
        actual = {"portal_pairs_removed": len(native["portal_pairs_removed"]),
                  "pixel_change": any(changed_pixels), "opening_change": bool(native["opening_changes"])}
        if "upper_stair_contribution_decreases_at_approach" in case["prediction"]:
            def upper(view):
                return sum(view["surface_pixel_samples_by_sector"].get(str(s), 0) for s in (13, 14, 15))
            actual["upper_stair_contribution_decreases_at_approach"] = upper(sequence["views"][0]) < upper(base_sequence["views"][0])
            actual["upper_stair_approach_samples"] = [upper(base_sequence["views"][0]), upper(sequence["views"][0])]
        checked = {key: actual[key] == value for key, value in case["prediction"].items() if key != "explanation"}
        invariants = {key: audit["invariants"][key] for key in ("record_counts_preserved", "wall_xy_preserved", "unaffected_records_preserved", "header_preserved", "input_preserved")}
        result = {"id": case["id"], "prediction": case["prediction"], "actual": actual,
                  "prediction_checks": checked, "invariants": invariants,
                  "prediction_passed": all(checked.values()) and all(invariants.values()),
                  "changed_pixels_by_pose": changed_pixels, "native": native, "perception": perception,
                  "intervention": audit_saved, "artifact_hash": sha, "artifact_path": str(path),
                  "pixel_report": record_tool_evidence(config, pixel_report, "spatial-paired-pixels")}
        save(output / f"{case['id']}.json", result)
        results.append(result)
        print(json.dumps({"case": case["id"], "prediction_passed": result["prediction_passed"], "actual": actual, "pixels": changed_pixels}), flush=True)
    for name, scope, expected in (
        ("e1m3", [287,286,284], "bc3633ef9f402e21eafb2050737c54812ed45f45eef296f895ad3e6c79c42595"),
        ("e1m4", [156,335,333,314,152,210], "ae239537a4d6973cc0c75ae90e30fc68a8a8c241dbc2bb1293e318cb792b7408"),
    ):
        source_path = config.corpus_root / "campaign" / (name.upper() + ".MAP")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{name} source hash differs from selected reference")
        analysis = inspect_space(config, source_path, scope)
        poses = json.loads((ROOT / f"examples/spatial-{name}.cameras.json").read_text())
        summary = reuse_recon / f"recon-{name}/summary.json" if reuse_recon else observe(source_path, name, poses)
        sequence = summarize_sequence(summary, analysis)
        # Reuse still requires the exact authored pose list, not merely a valid old run.
        request = json.loads((summary.parent / "request.json").read_text())
        if request["views"] != poses:
            raise ValueError(f"{name} reused poses do not match the authored sequence")
        record_tool_evidence(config, sequence, "spatial-perception")
        save(output / f"{name}-analysis.json", analysis)
        save(output / f"{name}-sequence.json", sequence)
        (output / f"{name}.svg").write_text(diagram_svg(read_map(source_path), analysis, poses), encoding="utf-8")
        examples.append({"name": name.upper(), "source": analysis["source"], "analysis_report": analysis["report_path"],
                         "sequence": str(output / f"{name}-sequence.json"), "views": len(poses), "diagram": f"{name}.svg"})
    holdout_path = config.corpus_root / "community/BLUDTOWN.MAP"
    holdout_hash = hashlib.sha256(holdout_path.read_bytes()).hexdigest()
    if holdout_hash != "670fa0792ff8d2c0be2fafb921261b67184331107f95975c288f0c56d635294d":
        raise ValueError("holdout source SHA differs")
    try:
        holdout_analysis = inspect_space(config, holdout_path, [810,814])
        holdout = {"sha256": holdout_hash, "status": "analyzed", "report_path": holdout_analysis["report_path"]}
    except ValueError as exc:
        holdout = {"sha256": holdout_hash, "status": "unsupported", "reason": str(exc),
                   "scope": "held-out community geometry gate; no heuristic tuning or silent repair"}
    report = {"schema": "bloodforge.spatial-milestone.v1", "examples": examples,
              "counterfactuals": results, "holdout": holdout,
              "claims": {"predictions_all_passed": all(r["prediction_passed"] for r in results),
                         "runtime_traversal_verified": False, "bot_used": False, "design_intent_proven": False}}
    report.update(record_tool_evidence(config, report, "spatial-milestone"))
    save(output / "report.json", report)
    html = ['<!doctype html><meta charset="utf-8"><title>BloodForge spatial evidence</title>',
            '<style>body{font:16px system-ui;background:#101820;color:#e5edf5;max-width:1100px;margin:40px auto}img,svg{max-width:100%;height:auto}figure{margin:12px 0}table{border-collapse:collapse}td,th{padding:9px;border:1px solid #607080}.frames{display:grid;grid-template-columns:1fr 1fr;gap:16px}small{color:#aebdcb}</style>',
            '<h1>Spatial Understanding v1</h1><p>Original-map geometry, controlled static views and audited counterfactuals. No bot or runtime traversal evidence.</p>',
            '<table><tr><th>Intervention</th><th>Prediction</th><th>Changed pixels by pose</th><th>Outcome</th></tr>']
    for result in results:
        html.append(f'<tr><td>{result["id"]}</td><td>{escape(result["prediction"]["explanation"])}</td><td>{result["changed_pixels_by_pose"]}</td><td>{"passed" if result["prediction_passed"] else "REFUTED"}</td></tr>')
    html.append('</table><p>All interventions preserve sector/wall/sprite counts and every wall XY coordinate. Pixel change alone is not design understanding; native aperture, continuity and renderer-sector evidence are stored alongside it.</p>')
    for example in examples:
        sequence = json.loads(Path(example["sequence"]).read_text())
        summary = Path(sequence["observation"]["summary_path"])
        shots = json.loads(summary.read_text())["screenshots"]
        html.append(f'<h2>{example["name"]}</h2><small>Source SHA-256 {example["source"]["sha256"]}</small><div class="frames"><figure>{(output / example["diagram"]).read_text()}</figure><div>')
        for shot in (shots[0], shots[-1]):
            relative = Path(shot["path"]).resolve().as_uri()
            html.append(f'<figure><img src="{relative}"><figcaption>{escape(shot["id"])}</figcaption></figure>')
        html.append('</div></div><p>Sampled visible native sectors: ' + escape(str([v["visible_native_sectors"] for v in sequence["views"]])) + '</p>')
    html.append('<h2>Counterfactual approach</h2><div class="frames">')
    for name in ("e2m5-baseline", "close-opening", "lower-ceiling", "block-movement-flag"):
        shot = json.loads(Path(observation_summaries[name]).read_text())["screenshots"][0]
        html.append(f'<figure><img src="{Path(shot["path"]).resolve().as_uri()}"><figcaption>{name}</figcaption></figure>')
    html.append('</div><h2>Limits and holdout</h2><p>' + escape(str(holdout)) + '</p><p>Region groups are hypotheses. Sprite volume, traversal, underwater state, nonzero slopes and dynamic mechanisms remain unresolved. Absence from a view does not prove occlusion or inaccessibility. Surface pixel samples can overlap.</p>')
    (output / "report.html").write_text("\n".join(html), encoding="utf-8")
    return {"report": str(output / "report.json"), "visual_report": str(output / "report.html"),
            "report_hash": report["report_hash"], "claims": report["claims"], "holdout": holdout}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reuse-recon", type=Path)
    parser.add_argument("--reuse-run", type=Path, help="reuse prior observer files after checking their current identities")
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.reuse_recon, args.reuse_run), indent=2))
