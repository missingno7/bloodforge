"""Bounded NBlood MAP-load smoke tests with auditable, isolated outputs.

This checks that NBlood can load a map and reach its ordinary game loop. It
does not establish that the map is playable or visually correct.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

from .native import NativeMapError, read_map
from .store import canonical_json
from .validation import validate_map


_REQUIRED_MARKERS = {
    "autoexec": "BLOODMAP_ORACLE_BOOTSTRAPPED",
    "game_loop": "Waiting for network players!",
    "map_initialization": "Modern types erased:",
}
_FATAL = re.compile(r"Caught signal|\bFATAL\||\bERROR\|", re.IGNORECASE)
_ENGINE_VERSION = re.compile(r"\bNBlood ([^\r\n]+)")
_LOCAL_CONFIG = """[Misc]
Executions = 1

[Setup]
ForceSetup = 0
NoAutoLoad = 1

[Screen Setup]
Polymer = 0
ScreenBPP = 32
ScreenHeight = 480
ScreenMode = 0
ScreenWidth = 640
"""
_AUTOEXEC = "echo BLOODMAP_ORACLE_BEGIN\necho BLOODMAP_ORACLE_BOOTSTRAPPED\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_manifest(root: Path) -> dict[str, str]:
    """Hash every regular file under the reference asset root without writes."""
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            manifest[path.relative_to(root).as_posix()] = "symlink"
        elif path.is_file():
            manifest[path.relative_to(root).as_posix()] = _sha256(path)
    return manifest


def _contained(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _overlaps(left: Path, right: Path) -> bool:
    return _contained(left, right) or _contained(right, left)


def _hidden_process_options() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startup, "creationflags": subprocess.CREATE_NO_WINDOW}


def _result_report(path: Path, result: dict[str, Any]) -> None:
    path.write_text(canonical_json(result) + "\n", encoding="utf-8")


def test_load(map_path: str | Path, output_dir: str | Path,
              executable: str | Path, assets_dir: str | Path, *,
              data_root: str | Path, seconds: float = 5.0) -> dict[str, Any]:
    """Run a hidden, bounded NBlood load smoke and return a complete audit.

    The input map, output directory, and process working directory must be
    inside ``data_root``. Executable and game assets are read-only inputs.
    ``seconds`` bounds the engine's lifetime before the wrapper kills only the
    process it launched and waits to reap it.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1 <= seconds <= 10:
        raise ValueError("seconds must be between 1 and 10")
    root = Path(data_root).resolve()
    output_base = Path(output_dir).resolve()
    candidate = Path(map_path).resolve()
    engine = Path(executable).resolve()
    assets = Path(assets_dir).resolve()
    if not _contained(output_base, root):
        raise ValueError("output_dir must be inside data_root")
    if not _contained(candidate, root):
        raise ValueError("map_path must be inside data_root")
    if _overlaps(root, assets):
        raise ValueError("data_root and assets_dir must not overlap")
    if _overlaps(root, engine.parent):
        raise ValueError("data_root and executable directory must not overlap")

    output_base.mkdir(parents=True, exist_ok=True)
    map_digest = _sha256(candidate) if candidate.is_file() else None
    run_dir = output_base / f"nblood-{(map_digest or 'missing')[:12]}-{time.time_ns()}"
    run_dir.mkdir(parents=False, exist_ok=False)
    local_map = run_dir / "candidate.MAP"
    assets_overlay = run_dir / "game-assets"
    cfg_path = run_dir / "local.cfg"
    autoexec_path = run_dir / "autoexec.cfg"
    stdout_path = run_dir / "stdout.txt"
    stderr_path = run_dir / "stderr.txt"
    log_path = run_dir / "nblood.log"
    report_path = run_dir / "report.json"
    result: dict[str, Any] = {
        "schema": "bloodforge.nblood-load-smoke",
        "schema_version": 1,
        "status": "invalid_input",
        "claims": {"map_loaded": False, "engine_initialized": False,
                   "player_spawn_verified": False, "stair_traversal_verified": False,
                   "playability_verified": False},
        "map": {"path": str(candidate), "sha256": map_digest},
        "engine": {"path": str(engine), "sha256": None, "version": None},
        "assets": {"path": str(assets), "unchanged": None},
        "assets_overlay": str(assets_overlay),
        "seconds": float(seconds),
        "command": None,
        "initialized_markers": {key: False for key in _REQUIRED_MARKERS},
        "fatal_indicators": [],
        "terminated_by_wrapper": False,
        "return_code": None,
        "run_dir": str(run_dir),
        "process_cwd": str(run_dir),
        "outputs": {"map": str(local_map), "config": str(cfg_path),
                    "autoexec": str(autoexec_path), "stdout": str(stdout_path),
                    "stderr": str(stderr_path), "log": str(log_path),
                    "report": str(report_path)},
    }

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        result["status"] = status
        result.update(extra)
        result["stdout_text"] = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        result["stderr_text"] = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        result["nblood_log_text"] = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        _result_report(report_path, result)
        return result

    if not candidate.is_file():
        return finish("invalid_input", error="MAP file does not exist")
    try:
        map_data = read_map(candidate)
        validation = validate_map(map_data)
    except (OSError, NativeMapError, ValueError) as exc:
        return finish("invalid_input", error=f"MAP parse failed: {exc}")
    if validation["summary"]["status"] == "fail":
        return finish("invalid_input", error="MAP structural validation failed",
                      validation=validation)
    result["map"]["counts"] = {"sectors": len(map_data.sectors), "walls": len(map_data.walls),
                                 "sprites": len(map_data.sprites)}

    if os.name != "nt":
        return finish("unavailable", reason="the configured NBlood binary requires a Windows host")
    if not engine.is_file():
        return finish("unavailable", reason=f"NBlood executable does not exist: {engine}")
    if not assets.is_dir():
        return finish("unavailable", reason=f"Blood asset directory does not exist: {assets}")

    try:
        result["engine"]["sha256"] = _sha256(engine)
        assets_before = _asset_manifest(assets)
    except OSError as exc:
        return finish("unavailable", reason=f"could not fingerprint read-only inputs: {exc}")
    result["assets_before"] = {"files": len(assets_before), "sha256": hashlib.sha256(
        canonical_json(assets_before).encode("utf-8")).hexdigest(), "manifest": assets_before}
    try:
        # NBlood's OpenGL texture cache is written into the game-data search
        # directory. Give it a private copy inside data_root so the reference
        # asset directory remains read-only in practice as well as by policy.
        shutil.copytree(assets, assets_overlay)
        shutil.copyfile(candidate, local_map)
    except OSError as exc:
        return finish("unavailable", reason=f"could not stage read-only inputs under data_root: {exc}")
    copied_digest = _sha256(local_map)
    result["map"]["copied_sha256"] = copied_digest
    if copied_digest != map_digest:
        return finish("invalid_input", error="MAP contents changed while preparing the runtime input")
    cfg_path.write_text(_LOCAL_CONFIG, encoding="utf-8", newline="\n")
    autoexec_path.write_text(_AUTOEXEC, encoding="utf-8", newline="\n")
    command = [str(engine), "-usecwd", "-game_dir", str(assets_overlay), "-cfg", str(cfg_path),
               "-map", local_map.name, "-noautoload", "-quick", "-nosetup"]
    result["command"] = command
    start_time = time.monotonic()
    process = None
    launch_error = None
    stayed_alive = False
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(command, cwd=run_dir, stdout=stdout, stderr=stderr,
                                       **_hidden_process_options())
        except OSError as exc:
            launch_error = str(exc)
        if process is not None:
            deadline = start_time + float(seconds)
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            stayed_alive = process.poll() is None
            if stayed_alive:
                process.kill()
                result["terminated_by_wrapper"] = True
            try:
                result["return_code"] = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    result["return_code"] = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launch_error = "NBlood process could not be reaped within five seconds after kill"

    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        combined = log_text + "\n" + stdout_text + "\n" + stderr_text
        result["initialized_markers"] = {key: marker in combined for key, marker in _REQUIRED_MARKERS.items()}
        result["fatal_indicators"] = sorted({match.group(0) for match in _FATAL.finditer(combined)})
        version_match = _ENGINE_VERSION.search(combined)
        result["engine"]["version"] = version_match.group(1).strip() if version_match else None
        assets_after = _asset_manifest(assets)
        changed_assets = sorted(path for path in assets_before.keys() | assets_after.keys()
                                if assets_before.get(path) != assets_after.get(path))
        result["assets_after"] = {"files": len(assets_after), "sha256": hashlib.sha256(
            canonical_json(assets_after).encode("utf-8")).hexdigest(), "manifest": assets_after}
        result["assets"]["unchanged"] = not changed_assets
        result["assets"]["changed_paths"] = changed_assets
        result["map"]["source_sha256_after"] = _sha256(candidate)
        result["map"]["source_unchanged"] = result["map"]["source_sha256_after"] == map_digest
        result["engine"]["sha256_after"] = _sha256(engine)
        result["engine"]["unchanged"] = result["engine"]["sha256_after"] == result["engine"]["sha256"]
    except OSError as exc:
        return finish("asset_check_failed", error=f"could not verify reference asset integrity: {exc}")

    if launch_error:
        return finish("crashed", error=launch_error, stayed_alive_for_seconds=time.monotonic() - start_time)
    if result["assets"]["unchanged"] is False:
        return finish("asset_modified", error="reference game assets changed during the smoke test")
    if not result["map"]["source_unchanged"]:
        return finish("map_modified", error="source MAP changed during the smoke test")
    if not result["engine"]["unchanged"]:
        return finish("executable_modified", error="NBlood executable changed during the smoke test")
    if result["fatal_indicators"] or (not stayed_alive and result["return_code"] not in (None, 0)):
        return finish("crashed", stayed_alive_for_seconds=time.monotonic() - start_time)
    if not stayed_alive or not all(result["initialized_markers"].values()):
        return finish("timeout", stayed_alive_for_seconds=time.monotonic() - start_time)
    result["claims"].update(map_loaded=True, engine_initialized=True)
    return finish("initialized", stayed_alive_for_seconds=float(seconds))
