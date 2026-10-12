"""Experimental diagnostics for NBlood's autonomous bot telemetry.

The NBlood bot chooses its own exploration route, and its reliability has not
been established. Results are observations only, never a validated traversal
oracle, controlled route, or general playability result.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

from .native import NativeMapError, read_map
from .runtime import (
    _AUTOEXEC,
    _LOCAL_CONFIG,
    _asset_manifest,
    _contained,
    _hidden_process_options,
    _overlaps,
    _sha256,
)
from .store import canonical_json
from .validation import validate_map

_MAX_TRACE_BYTES = 32 * 1024 * 1024
_MAX_TRACE_LINE_BYTES = 64 * 1024
_MAX_TRACE_LINES = 100_000
_MAX_REPORTED_PARSE_ERRORS = 50


def _write_report(path: Path, result: dict[str, Any]) -> None:
    path.write_text(canonical_json(result) + "\n", encoding="utf-8")


def _read_ndjson(path: Path, *, max_bytes: int = _MAX_TRACE_BYTES,
                 max_line_bytes: int = _MAX_TRACE_LINE_BYTES,
                 max_lines: int = _MAX_TRACE_LINES) -> tuple[list[dict[str, Any]], list[str], bool]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    limited = False
    suppressed_errors = 0

    def report_error(message: str) -> None:
        nonlocal suppressed_errors
        if len(errors) < _MAX_REPORTED_PARSE_ERRORS:
            errors.append(message)
        else:
            suppressed_errors += 1

    if not path.exists():
        return rows, ["file_missing"], limited
    attempted_lines = 0
    consumed_bytes = 0
    with path.open("rb") as stream:
        while True:
            if attempted_lines >= max_lines or consumed_bytes >= max_bytes:
                # Detect additional input without reading an unbounded tail.
                if stream.read(1):
                    limited = True
                    report_error("trace_line_or_byte_limit_exceeded")
                break
            raw = stream.readline(min(max_line_bytes + 1, max_bytes - consumed_bytes + 1))
            if not raw:
                break
            attempted_lines += 1
            consumed_bytes += len(raw)
            if consumed_bytes > max_bytes:
                limited = True
                report_error(f"trace_byte_limit_exceeded:{max_bytes}")
                break
            if len(raw) > max_line_bytes:
                limited = True
                report_error(f"trace_line_limit_exceeded:{max_line_bytes}")
                break
            line = raw.decode("utf-8", errors="replace")
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                report_error(f"line_{attempted_lines}:invalid_json:{exc.msg}")
                continue
            if not isinstance(row, dict):
                report_error(f"line_{attempted_lines}:expected_object")
                continue
            rows.append(row)
    if suppressed_errors:
        errors.append(f"additional_parse_errors_suppressed:{suppressed_errors}")
    return rows, errors, limited


def _portal_between(level, from_sector: int, to_sector: int) -> bool:
    source = level.sectors[from_sector]
    for wall_index in range(source.wallptr, source.wallptr + source.wallnum):
        wall = level.walls[wall_index]
        if wall.nextsector == to_sector and 0 <= wall.nextwall < len(level.walls):
            other = level.walls[wall.nextwall]
            if other.nextwall == wall_index and other.nextsector == from_sector:
                return True
    return False


def _route_evidence(level, trajectory: list[dict[str, Any]], route: tuple[int, ...]) -> dict[str, Any]:
    positions: list[int] = []
    cursor = -1
    for sector in route:
        found = next((index for index in range(cursor + 1, len(trajectory))
                      if trajectory[index].get("sector") == sector), None)
        if found is None:
            return {"complete_sequence": False, "reason": f"sector_{sector}_not_observed",
                    "sectors_seen": sorted({row.get("sector") for row in trajectory
                                             if isinstance(row.get("sector"), int)})}
        positions.append(found)
        cursor = found

    segments: list[dict[str, Any]] = []
    all_grounded = True
    for offset, sector in enumerate(route):
        begin = positions[offset]
        end = positions[offset + 1] if offset + 1 < len(route) else len(trajectory)
        samples = [row for row in trajectory[begin:end]
                   if row.get("sector") == sector and row.get("on_ground") == 1
                   and isinstance(row.get("z"), int)]
        if not samples:
            all_grounded = False
            segments.append({"sector": sector, "grounded_samples": 0,
                             "floor_z": level.sectors[sector].floorz, "actor_z": None})
            continue
        segments.append({"sector": sector, "grounded_samples": len(samples),
                         "floor_z": level.sectors[sector].floorz,
                         "actor_z": samples[-1]["z"],
                         "sample_tick": samples[-1].get("tick")})

    transitions: list[dict[str, Any]] = []
    all_rises = all_grounded
    for index, (source, destination) in enumerate(zip(segments, segments[1:])):
        from_sector, to_sector = route[index], route[index + 1]
        floor_delta = destination["floor_z"] - source["floor_z"]
        actor_delta = (destination["actor_z"] - source["actor_z"]
                       if source["actor_z"] is not None and destination["actor_z"] is not None else None)
        # Actor Z is sampled at the player's grounded body position, so allow
        # a modest offset from a sector plane. The tolerance must still be
        # smaller than one stair rise; otherwise a missing rise can pass.
        abs_floor_delta = abs(floor_delta)
        tolerance = (min(abs_floor_delta - 1, max(64, abs_floor_delta // 8))
                     if abs_floor_delta else 64)
        matched = (actor_delta is not None and abs(actor_delta - floor_delta) <= tolerance
                   and _portal_between(level, from_sector, to_sector))
        transitions.append({"from_sector": from_sector, "to_sector": to_sector,
                            "floor_delta_z": floor_delta, "actor_delta_z": actor_delta,
                            "tolerance_z": tolerance, "reciprocal_portal": _portal_between(level, from_sector, to_sector),
                            "grounded_samples": [source["grounded_samples"], destination["grounded_samples"]],
                            "rise_observed": bool(matched)})
        all_rises = all_rises and bool(matched)
    return {"complete_sequence": True, "sectors_seen": [item["sector"] for item in segments],
            "segments": segments, "transitions": transitions,
            "all_grounded": all_grounded, "all_floor_changes_observed": all_rises}


def diagnose_bot_route(map_path: str | Path, expected_sectors: tuple[int, ...],
                       output_dir: str | Path, executable: str | Path,
                       assets_dir: str | Path, *, data_root: str | Path,
                       game_seconds: int = 5, max_wall_seconds: float = 10.0) -> dict[str, Any]:
    """Collect experimental autonomous-bot observations for an expected route.

    The route is an expected path, not a steering command: this NBlood build
    exposes autonomous exploration and position telemetry but no waypoint
    input. Complete telemetry is reported as a diagnostic observation only;
    it never establishes validated traversal or general playability.
    """
    if (not isinstance(expected_sectors, tuple) or len(expected_sectors) < 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in expected_sectors)):
        raise ValueError("expected_sectors must be a tuple of at least two integer sector indexes")
    if len(set(expected_sectors)) != len(expected_sectors):
        raise ValueError("expected_sectors must not repeat sectors")
    if isinstance(game_seconds, bool) or not isinstance(game_seconds, int) or not 1 <= game_seconds <= 120:
        raise ValueError("game_seconds must be between 1 and 120")
    if (isinstance(max_wall_seconds, bool) or not isinstance(max_wall_seconds, (int, float))
            or not 1 <= max_wall_seconds <= 10):
        raise ValueError("max_wall_seconds must be between 1 and 10")

    root = Path(data_root).resolve()
    output_base = Path(output_dir).resolve()
    candidate = Path(map_path).resolve()
    engine = Path(executable).resolve()
    assets = Path(assets_dir).resolve()
    if not _contained(output_base, root) or not _contained(candidate, root):
        raise ValueError("output_dir and map_path must be inside data_root")
    if _overlaps(root, assets):
        raise ValueError("data_root and assets_dir must not overlap")
    if _overlaps(root, engine.parent):
        raise ValueError("data_root and executable directory must not overlap")

    output_base.mkdir(parents=True, exist_ok=True)
    source_sha = _sha256(candidate) if candidate.is_file() else None
    run_dir = output_base / f"bot-diagnostic-{(source_sha or 'missing')[:12]}-{time.time_ns()}"
    run_dir.mkdir(parents=False, exist_ok=False)
    local_map = run_dir / "candidate.MAP"
    local_cfg = run_dir / "local.cfg"
    autoexec = run_dir / "autoexec.cfg"
    telemetry_path = run_dir / "telemetry.ndjson"
    trajectory_path = run_dir / "trajectory.ndjson"
    demo_path = run_dir / "bot.dem"
    stdout_path = run_dir / "stdout.txt"
    stderr_path = run_dir / "stderr.txt"
    log_path = run_dir / "nblood.log"
    report_path = run_dir / "report.json"
    overlay = run_dir / "game-assets"
    result: dict[str, Any] = {
        "schema": "bloodforge.nblood-bot-diagnostic",
        "schema_version": 1,
        "status": "invalid_input",
        "experimental": True,
        "reliability_established": False,
        "control_mode": "autonomous_bot_expected_route",
        "claims": {"map_loaded": False, "engine_initialized": False,
                   "route_observed": False, "traversal_verified": False,
                   "controlled_route": False,
                   "player_spawn_verified": False, "playability_verified": False},
        "expected_sectors": list(expected_sectors),
        "map": {"path": str(candidate), "sha256": source_sha},
        "engine": {"path": str(engine), "sha256": None, "version": None},
        "assets": {"path": str(assets), "unchanged": None},
        "run_dir": str(run_dir),
        "process_cwd": str(run_dir),
        "asset_overlay": str(overlay),
        "game_seconds": game_seconds,
        "max_wall_seconds": float(max_wall_seconds),
        "command": None,
        "return_code": None,
        "terminated_by_wrapper": False,
        "outputs": {"map": str(local_map), "config": str(local_cfg),
                    "autoexec": str(autoexec), "telemetry": str(telemetry_path),
                    "trajectory": str(trajectory_path), "demo": str(demo_path),
                    "stdout": str(stdout_path), "stderr": str(stderr_path),
                    "log": str(log_path), "report": str(report_path)},
    }

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        result["status"] = status
        result.update(extra)
        for key, path in (("stdout_text", stdout_path), ("stderr_text", stderr_path),
                          ("nblood_log_text", log_path)):
            result[key] = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if telemetry_path.exists():
            result["telemetry_sha256"] = _sha256(telemetry_path)
        if trajectory_path.exists():
            result["trajectory_sha256"] = _sha256(trajectory_path)
        _write_report(report_path, result)
        return result

    if not candidate.is_file():
        return finish("invalid_input", error="MAP file does not exist")
    try:
        level = read_map(candidate)
        validation = validate_map(level)
    except (OSError, NativeMapError, ValueError) as exc:
        return finish("invalid_input", error=f"MAP parse failed: {exc}")
    if validation["summary"]["status"] == "fail":
        return finish("invalid_input", error="MAP structural validation failed", validation=validation)
    if any(sector < 0 or sector >= len(level.sectors) for sector in expected_sectors):
        return finish("invalid_input", error="expected route names a sector outside this MAP")
    if any(not _portal_between(level, a, b) for a, b in zip(expected_sectors, expected_sectors[1:])):
        return finish("invalid_input", error="expected route contains a non-portal sector transition")
    result["map"]["counts"] = {"sectors": len(level.sectors), "walls": len(level.walls),
                                 "sprites": len(level.sprites)}

    if os.name != "nt":
        return finish("unavailable", reason="the configured NBlood binary requires a Windows host")
    if not engine.is_file():
        return finish("unavailable", reason=f"NBlood executable does not exist: {engine}")
    if not assets.is_dir():
        return finish("unavailable", reason=f"Blood asset directory does not exist: {assets}")
    try:
        engine_sha = _sha256(engine)
        assets_before = _asset_manifest(assets)
        result["engine"]["sha256"] = engine_sha
        result["assets_before"] = {"files": len(assets_before),
                                   "sha256": hashlib.sha256(canonical_json(assets_before).encode()).hexdigest()}
        shutil.copytree(assets, overlay)
        shutil.copyfile(candidate, local_map)
        if _sha256(local_map) != source_sha:
            return finish("invalid_input", error="MAP contents changed while preparing the runtime input")
    except OSError as exc:
        return finish("unavailable", reason=f"could not stage read-only inputs under data_root: {exc}")
    local_cfg.write_text(_LOCAL_CONFIG, encoding="utf-8", newline="\n")
    autoexec.write_text(_AUTOEXEC, encoding="utf-8", newline="\n")
    command = [str(engine), "-usecwd", "-game_dir", str(overlay), "-cfg", str(local_cfg),
               "-map", local_map.name, "-noautoload", "-quick", "-nosetup",
               "-bot", "-bot_timeout", str(game_seconds),
               "-bot_realtime",
               "-bot_telemetry", str(telemetry_path),
               "-bot_trajectory", str(trajectory_path), "-bot_demo", str(demo_path)]
    result["command"] = command
    started = time.monotonic()
    process = None
    launch_error = None
    timed_out = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(command, cwd=run_dir, stdout=stdout, stderr=stderr,
                                       **_hidden_process_options())
        except OSError as exc:
            launch_error = str(exc)
        if process is not None:
            deadline = started + float(max_wall_seconds)
            resource_limited = False
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    output_over_limit = any(path.exists() and path.stat().st_size > _MAX_TRACE_BYTES
                                            for path in (telemetry_path, trajectory_path))
                except OSError as exc:
                    launch_error = f"could not inspect bot trace size: {exc}"
                    break
                if output_over_limit:
                    resource_limited = True
                    break
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            timed_out = process.poll() is None and not resource_limited
            if timed_out or resource_limited:
                process.kill()
                result["terminated_by_wrapper"] = True
                if resource_limited:
                    result["termination_reason"] = "trace_output_limit"
            try:
                result["return_code"] = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    result["return_code"] = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launch_error = "NBlood bot process could not be reaped after kill"

    telemetry, telemetry_errors, telemetry_limited = _read_ndjson(telemetry_path)
    trajectory, trajectory_errors, trajectory_limited = _read_ndjson(trajectory_path)
    result["telemetry_errors"] = telemetry_errors
    result["trajectory_errors"] = trajectory_errors
    result["telemetry_resource_limited"] = telemetry_limited
    result["trajectory_resource_limited"] = trajectory_limited
    summaries = [row for row in telemetry if row.get("type") == "summary"]
    result["bot_summary"] = summaries[-1] if summaries else None
    result["trajectory_rows"] = len(trajectory)
    result["route_evidence"] = _route_evidence(level, trajectory, expected_sectors)
    result["trajectory_file"] = str(trajectory_path)
    try:
        result["map"]["source_sha256_after"] = _sha256(candidate)
        result["map"]["source_unchanged"] = result["map"]["source_sha256_after"] == source_sha
        result["engine"]["sha256_after"] = _sha256(engine)
        result["engine"]["unchanged"] = result["engine"]["sha256_after"] == engine_sha
        assets_after = _asset_manifest(assets)
        result["assets_after"] = {"files": len(assets_after),
                                   "sha256": hashlib.sha256(canonical_json(assets_after).encode()).hexdigest()}
        result["assets"]["unchanged"] = assets_before == assets_after
        result["assets"]["changed_paths"] = sorted(path for path in assets_before.keys() | assets_after.keys()
                                                    if assets_before.get(path) != assets_after.get(path))
    except OSError as exc:
        return finish("asset_check_failed", error=f"could not verify read-only inputs: {exc}")

    if launch_error:
        return finish("crashed", error=launch_error)
    if result.get("termination_reason") == "trace_output_limit" or telemetry_limited or trajectory_limited:
        return finish("resource_limit", error="NBlood bot trace exceeded a configured resource limit")
    if timed_out:
        return finish("timeout", error="NBlood exceeded the wall-clock limit")
    if result["assets"]["unchanged"] is False:
        return finish("asset_modified", error="reference game assets changed during traversal")
    if not result["map"]["source_unchanged"] or not result["engine"]["unchanged"]:
        return finish("input_modified", error="source MAP or NBlood executable changed during traversal")
    if result["return_code"] != 0:
        return finish("crashed", error=f"NBlood exited with code {result['return_code']}")
    if not summaries or result["bot_summary"].get("result") in {"RUNTIME_ERROR", "DIED", "TIMEOUT", "STOPPED"}:
        return finish("incomplete", error="NBlood bot did not finish with a usable traversal summary")
    if telemetry_errors or trajectory_errors:
        return finish("incomplete", error="NBlood bot telemetry contains invalid or incomplete NDJSON")
    evidence = result["route_evidence"]
    observed = evidence.get("complete_sequence") and evidence.get("all_grounded") and evidence.get("all_floor_changes_observed")
    if not observed:
        return finish("incomplete", error="expected grounded route and every floor-height change were not observed")
    result["claims"].update(map_loaded=True, engine_initialized=True,
                            route_observed=True)
    return finish("diagnostic_observed")
