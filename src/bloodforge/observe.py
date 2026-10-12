"""Run the native XMapEdit observer in an isolated BloodForge data directory.

This is a process adapter, not a renderer implementation. It writes a request,
captures the observer's own manifest and frames, then emits a compact sidecar
whose claims are limited to process success, returned pose/surface data, and
input/output file integrity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
from typing import Any, Mapping, Sequence


OBSERVER_SCHEMA = "llmapper.xmapedit-visual-observation"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ObserverError(ValueError):
    """Invalid observer inputs or an unsafe output location."""


@dataclass(frozen=True)
class CameraPose:
    """One static view; coordinates use Blood/Build native units."""

    id: str
    x: int
    y: int
    z: int
    angle: int = 0
    horiz: int = 100
    sector: int | None = None
    screenshot: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_RE.fullmatch(self.id):
            raise ObserverError("camera id must contain only letters, digits, '_' or '-' (max 64 chars)")
        for name in ("x", "y", "z", "angle", "horiz"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ObserverError(f"camera {name} must be an integer")
        if self.sector is not None and (
            isinstance(self.sector, bool) or not isinstance(self.sector, int) or self.sector < 0
        ):
            raise ObserverError("camera sector must be a non-negative integer or None")
        if not isinstance(self.screenshot, bool):
            raise ObserverError("camera screenshot must be a boolean")

    def request_record(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id, "x": self.x, "y": self.y, "z": self.z,
            "angle": self.angle, "horiz": self.horiz,
            "screenshot": self.screenshot,
        }
        if self.sector is not None:
            result["sector"] = self.sector
        return result


def _default_data_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _overlaps(path: Path, other: Path) -> bool:
    """Whether two paths are equal or one contains the other."""
    return path == other or _inside(path, other) or _inside(other, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "sha256": _sha256(path)}


def _tree_identity(root: Path) -> list[dict[str, Any]]:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    return [_file_identity(path.resolve()) for path in files]


def _camera(value: CameraPose | Mapping[str, Any]) -> CameraPose:
    if isinstance(value, CameraPose):
        return value
    if not isinstance(value, Mapping):
        raise ObserverError("camera must be a CameraPose or mapping")
    try:
        return CameraPose(**dict(value))
    except TypeError as exc:
        raise ObserverError(f"invalid camera fields: {exc}") from exc


def _png_info(path: Path) -> dict[str, int] | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            return None
        width, height = struct.unpack(">II", header[16:24])
        return {"width": width, "height": height}
    except OSError:
        return None


def observe_map(
    map_path: str | Path,
    output_dir: str | Path,
    executable: str | Path,
    assets_dir: str | Path,
    camera: CameraPose | Mapping[str, Any] | Sequence[CameraPose | Mapping[str, Any]],
    *,
    timeout_seconds: float = 60.0,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Render one or more poses of an existing MAP with XMapEdit's observer.

    The request, renderer outputs, subprocess logs, and returned sidecar must all
    live below ``data_root`` (the project ``data`` directory by default). An
    original MAP may be read from the reference corpus. The map, executable,
    and asset tree are hashed before and after the child run; the wrapper never
    writes to those inputs.
    """
    root = Path(data_root).resolve() if data_root is not None else _default_data_root().resolve()
    out = Path(output_dir).resolve()
    source_map = Path(map_path).resolve()
    binary = Path(executable).resolve()
    assets = Path(assets_dir).resolve()
    if not _inside(out, root) or out == root:
        raise ObserverError(f"output_dir must be a child of the BloodForge data directory: {root}")
    if not source_map.is_file():
        raise ObserverError(f"map file does not exist: {source_map}")
    if not binary.is_file():
        raise ObserverError(f"observer executable does not exist: {binary}")
    if not assets.is_dir():
        raise ObserverError(f"asset directory does not exist: {assets}")
    if _inside(source_map, out):
        raise ObserverError("output_dir cannot contain the source MAP")
    if _overlaps(out, assets):
        raise ObserverError("output_dir must not overlap the read-only asset directory")
    if _overlaps(out, binary.parent):
        raise ObserverError("output_dir must not overlap the observer executable directory")
    asset_names = {p.name.lower() for p in assets.iterdir() if p.is_file()}
    if "blood.rff" not in asset_names or "tiles000.art" not in asset_names:
        raise ObserverError("asset directory must contain BLOOD.RFF and TILES000.ART")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 60):
        raise ObserverError("timeout_seconds must be greater than zero and at most 60")

    if isinstance(camera, (CameraPose, Mapping)):
        poses = [_camera(camera)]
    else:
        poses = [_camera(item) for item in camera]
    if not poses:
        raise ObserverError("at least one camera pose is required")
    if len({pose.id for pose in poses}) != len(poses):
        raise ObserverError("camera ids must be unique")

    # Fresh output avoids stale frames being mistaken for current evidence and
    # avoids deleting or overwriting anything the user already placed there.
    if out.exists() and any(out.iterdir()):
        raise ObserverError(f"output_dir must be empty for a fresh observation: {out}")
    out.mkdir(parents=True, exist_ok=True)
    observer_out = out / "render"
    request_path = out / "request.json"
    manifest_path = observer_out / "observation.json"
    stdout_path, stderr_path = out / "stdout.txt", out / "stderr.txt"

    input_before = {
        "map": _file_identity(source_map),
        "executable": _file_identity(binary),
        "assets": _tree_identity(assets),
    }
    request = {
        "$schema": "llmapper.xmapedit-observation-request",
        "schema_version": 1,
        "map": str(source_map).replace("\\", "/"),
        "resource_dir": str(assets).replace("\\", "/"),
        "output": str(observer_out).replace("\\", "/"),
        "width": 640,
        "height": 480,
        "screenshots": any(pose.screenshot for pose in poses),
        "brightness": 0,
        "views": [pose.request_record() for pose in poses],
    }
    request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")

    exit_code: int | None = None
    stdout = ""
    stderr = ""
    process_error: str | None = None
    try:
        launch_options: dict[str, Any] = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            launch_options = {
                "startupinfo": startup,
                "creationflags": subprocess.CREATE_NO_WINDOW,
            }
        completed = subprocess.run(
            [str(binary), str(request_path)],
            cwd=out,
            capture_output=True,
            text=True,
            timeout=float(timeout_seconds),
            check=False,
            **launch_options,
        )
        exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        process_error = f"observer timed out after {timeout_seconds:g} seconds"
    except OSError as exc:
        process_error = f"could not start observer: {exc}"

    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    manifest: dict[str, Any] | None = None
    manifest_error: str | None = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("$schema") != OBSERVER_SCHEMA:
                manifest_error = "observer manifest schema did not match"
                manifest = None
        except (OSError, json.JSONDecodeError) as exc:
            manifest_error = f"could not read observer manifest: {exc}"
    else:
        manifest_error = "observer wrote no observation.json"

    input_after = {
        "map": _file_identity(source_map),
        "executable": _file_identity(binary),
        "assets": _tree_identity(assets),
    }
    integrity = {
        "map_unchanged": input_before["map"] == input_after["map"],
        "executable_unchanged": input_before["executable"] == input_after["executable"],
        "assets_unchanged": input_before["assets"] == input_after["assets"],
        "before": input_before,
        "after": input_after,
    }
    output_files = sorted(path.resolve() for path in out.rglob("*") if path.is_file())
    outputs_are_contained = all(_inside(path, out) for path in output_files)
    output_records = [
        {"relative_path": (str(path.relative_to(out)).replace("\\", "/")
                            if _inside(path, out) else str(path)),
         "size": path.stat().st_size, "sha256": _sha256(path)}
        for path in output_files
    ]

    views: list[dict[str, Any]] = []
    screenshots: list[dict[str, Any]] = []
    if manifest is not None:
        for view in manifest.get("views", []):
            if not isinstance(view, dict):
                continue
            view_summary = {
                "id": view.get("id"),
                "status": view.get("status"),
                "reason": view.get("reason"),
                "camera": view.get("camera"),
                "frame": view.get("frame"),
                "surfaces": view.get("surfaces", []),
                "occluded": view.get("occluded", []),
                "unorderable": view.get("unorderable", []),
            }
            views.append(view_summary)
            screenshot = view.get("screenshot")
            if isinstance(screenshot, str):
                image_path = (observer_out / screenshot).resolve()
                if not _inside(image_path, observer_out.resolve()) or not image_path.is_file():
                    screenshots.append({"id": view.get("id"), "path": screenshot, "present": False})
                else:
                    png_info = _png_info(image_path)
                    screenshots.append({
                        "id": view.get("id"),
                        "path": str(image_path),
                        "present": True,
                        **(png_info or {}),
                        "valid_png": png_info is not None,
                        "size": image_path.stat().st_size,
                        "sha256": _sha256(image_path),
                    })

    requested_ids = [pose.id for pose in poses]
    returned_ids = [view.get("id") for view in views]
    view_ids_match = returned_ids == requested_ids
    all_views_ok = view_ids_match and all(view.get("status") == "ok" for view in views)
    screenshot_by_id = {shot.get("id"): shot for shot in screenshots}
    requested_screenshots_ok = all(
        not pose.screenshot or (
            screenshot_by_id.get(pose.id, {}).get("present") is True
            and screenshot_by_id[pose.id].get("valid_png") is True
        )
        for pose in poses
    )

    process_ok = exit_code == 0
    manifest_ok = manifest is not None
    integrity_ok = all(integrity[key] for key in (
        "map_unchanged", "executable_unchanged", "assets_unchanged"))
    result: dict[str, Any] = {
        "$schema": "bloodforge.native-observation-summary",
        "schema_version": 1,
        "status": "completed" if (process_ok and manifest_ok and integrity_ok
                                    and outputs_are_contained and all_views_ok
                                    and requested_screenshots_ok) else "failed",
        "claims": {
            "process_ok": process_ok,
            "manifest_ok": manifest_ok,
            "input_files_unchanged": integrity_ok,
            "outputs_contained": outputs_are_contained,
            "requested_views_match": view_ids_match,
            "all_requested_views_rendered": all_views_ok,
            "requested_screenshots_valid": requested_screenshots_ok,
            "visual_quality_judged": False,
            "gameplay_or_door_motion_proven": False,
        },
        "process": {
            "executable": str(binary), "cwd": str(out), "exit_code": exit_code,
            "timeout_seconds": timeout_seconds, "error": process_error,
            "stdout": str(stdout_path), "stderr": str(stderr_path),
        },
        "map": str(source_map),
        "map_info": manifest.get("map") if manifest else None,
        "observer_manifest": str(manifest_path) if manifest_ok else None,
        "manifest_error": manifest_error,
        "renderer": manifest.get("renderer") if manifest else None,
        "timing_ms": manifest.get("timing_ms") if manifest else None,
        "requested_view_ids": requested_ids,
        "returned_view_ids": returned_ids,
        "views": views,
        "screenshots": screenshots,
        "limitations": manifest.get("limitations", []) if manifest else [],
        "input_integrity": integrity,
        "output_integrity": {
            "all_files_within_output_root": outputs_are_contained,
            "files_before_summary_sidecar": output_records,
        },
        "output_root": str(out),
    }
    (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    # Keep full per-file hashes in the sidecar, but keep the API return concise
    # for a CLI or conversation summary.
    compact = dict(result)
    compact["input_integrity"] = {
        "map_unchanged": integrity["map_unchanged"],
        "executable_unchanged": integrity["executable_unchanged"],
        "assets_unchanged": integrity["assets_unchanged"],
        "asset_file_count": len(input_before["assets"]),
    }
    compact["output_integrity"] = {
        "all_files_within_output_root": outputs_are_contained,
        "file_count_before_summary_sidecar": len(output_records),
    }
    return compact
