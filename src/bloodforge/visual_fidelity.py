"""Bounded pixel comparison for two successful native observer runs.

This module compares static screenshots only. It deliberately does not assign
an overall quality score or infer traversal, gameplay, or semantic correctness.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class VisualFidelityError(ValueError):
    """The observation pair is incomplete, inconsistent, or not comparable."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualFidelityError(f"cannot read {label} JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VisualFidelityError(f"{label} must be a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise VisualFidelityError(f"cannot read recorded output {path}: {exc}") from exc
    return digest.hexdigest()


def _resolved(path: str | Path, label: str) -> Path:
    try:
        return Path(path).resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise VisualFidelityError(f"invalid {label} path: {path!r}") from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _recorded_files(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output = summary.get("output_integrity")
    files = output.get("files_before_summary_sidecar") if isinstance(output, dict) else None
    if not isinstance(files, list):
        raise VisualFidelityError("observation has no output file identity list")
    result: dict[str, dict[str, Any]] = {}
    for record in files:
        if not isinstance(record, dict) or not isinstance(record.get("relative_path"), str):
            raise VisualFidelityError("observation has a malformed output file identity")
        relative = record["relative_path"].replace("\\", "/")
        if relative in result:
            raise VisualFidelityError(f"duplicate output file identity for {relative}")
        result[relative] = record
    return result


def _relative_output(path: Path, output_root: Path) -> str:
    if not _is_within(path, output_root):
        raise VisualFidelityError(f"recorded output escapes its run directory: {path}")
    return path.relative_to(output_root).as_posix()


def _verify_output_identity(path: Path, output_root: Path,
                            recorded_files: dict[str, dict[str, Any]], label: str) -> str:
    relative = _relative_output(path, output_root)
    record = recorded_files.get(relative)
    if record is None:
        raise VisualFidelityError(f"{label} is absent from the observer output identity list: {relative}")
    actual_hash = _sha256(path)
    if actual_hash != record.get("sha256") or path.stat().st_size != record.get("size"):
        raise VisualFidelityError(f"{label} no longer matches its recorded output identity: {relative}")
    return actual_hash


def _validate_run(summary_path: Path, label: str) -> dict[str, Any]:
    summary = _read_json(summary_path, f"{label} summary")
    if summary.get("$schema") != "bloodforge.native-observation-summary" or summary.get("schema_version") != 1:
        raise VisualFidelityError(f"{label} does not use the supported native-observation-summary schema")
    if summary.get("status") != "completed":
        raise VisualFidelityError(f"{label} observation status is not completed")

    claims = summary.get("claims")
    required_claims = (
        "process_ok", "manifest_ok", "input_files_unchanged", "outputs_contained",
        "requested_views_match", "all_requested_views_rendered", "requested_screenshots_valid",
    )
    if not isinstance(claims, dict) or any(claims.get(key) is not True for key in required_claims):
        raise VisualFidelityError(f"{label} observer success/integrity claims are incomplete")
    input_integrity = summary.get("input_integrity")
    if not isinstance(input_integrity, dict):
        raise VisualFidelityError(f"{label} has no input integrity evidence")
    for key in ("map_unchanged", "executable_unchanged", "assets_unchanged"):
        if input_integrity.get(key) is not True:
            raise VisualFidelityError(f"{label} input was not proven unchanged: {key}")
    before, after = input_integrity.get("before"), input_integrity.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise VisualFidelityError(f"{label} is missing before/after input identities")
    for key in ("map", "executable", "assets"):
        if before.get(key) != after.get(key):
            raise VisualFidelityError(f"{label} before/after input identity differs: {key}")
    for key in ("map", "executable"):
        identity = before.get(key)
        if not isinstance(identity, dict) or not isinstance(identity.get("sha256"), str):
            raise VisualFidelityError(f"{label} has no {key} SHA-256 identity")

    run_root = _resolved(summary.get("output_root"), f"{label} output root")
    if run_root != summary_path.resolve().parent:
        raise VisualFidelityError(f"{label} summary is not located in its recorded output root")
    output_integrity = summary.get("output_integrity")
    if not isinstance(output_integrity, dict) or output_integrity.get("all_files_within_output_root") is not True:
        raise VisualFidelityError(f"{label} output containment was not proven")
    recorded_files = _recorded_files(summary)

    process = summary.get("process")
    if not isinstance(process, dict) or _resolved(process.get("cwd"), f"{label} process cwd") != run_root:
        raise VisualFidelityError(f"{label} process cwd differs from its recorded output root")
    request_path = run_root / "request.json"
    _verify_output_identity(request_path, run_root, recorded_files, f"{label} request.json")
    request = _read_json(request_path, f"{label} observer request")
    if request.get("$schema") != "llmapper.xmapedit-observation-request" or request.get("schema_version") != 1:
        raise VisualFidelityError(f"{label} request does not use the supported observer request schema")

    requested = request.get("views")
    if not isinstance(requested, list) or not requested:
        raise VisualFidelityError(f"{label} request contains no camera views")
    if any(not isinstance(view, dict) or view.get("screenshot") is not True for view in requested):
        raise VisualFidelityError(f"{label} must request a screenshot for every compared camera")
    requested_ids = [view.get("id") for view in requested]
    if any(not isinstance(view_id, str) for view_id in requested_ids) or len(set(requested_ids)) != len(requested_ids):
        raise VisualFidelityError(f"{label} request has invalid or duplicate camera ids")
    if summary.get("requested_view_ids") != requested_ids or summary.get("returned_view_ids") != requested_ids:
        raise VisualFidelityError(f"{label} summary view ids do not match its request")

    map_identity = before["map"]
    if _resolved(request.get("map"), f"{label} request MAP") != _resolved(map_identity["path"], f"{label} recorded MAP"):
        raise VisualFidelityError(f"{label} request MAP differs from its input identity")
    if _resolved(summary.get("map"), f"{label} summary MAP") != _resolved(map_identity["path"], f"{label} recorded MAP"):
        raise VisualFidelityError(f"{label} summary MAP differs from its input identity")
    if _resolved(process.get("executable"), f"{label} process executable") != _resolved(before["executable"]["path"], f"{label} recorded executable"):
        raise VisualFidelityError(f"{label} process executable differs from its input identity")

    manifest_path = _resolved(summary.get("observer_manifest"), f"{label} observer manifest")
    _verify_output_identity(manifest_path, run_root, recorded_files, f"{label} observer manifest")
    manifest = _read_json(manifest_path, f"{label} observer manifest")
    if manifest.get("$schema") != "llmapper.xmapedit-visual-observation" or manifest.get("schema_version") != 1:
        raise VisualFidelityError(f"{label} observer manifest schema is unsupported")
    if summary.get("manifest_error") is not None:
        raise VisualFidelityError(f"{label} observer manifest has a recorded error")
    manifest_renderer = manifest.get("renderer")
    summary_renderer = summary.get("renderer")
    if not isinstance(manifest_renderer, dict) or not isinstance(summary_renderer, dict):
        raise VisualFidelityError(f"{label} renderer identity is missing")
    if manifest_renderer != summary_renderer:
        raise VisualFidelityError(f"{label} renderer sidecar disagrees with its manifest")

    manifest_views = manifest.get("views")
    summary_views = summary.get("views")
    if not isinstance(manifest_views, list) or not isinstance(summary_views, list):
        raise VisualFidelityError(f"{label} has no rendered view records")
    if [item.get("id") for item in manifest_views if isinstance(item, dict)] != requested_ids:
        raise VisualFidelityError(f"{label} manifest view ids do not match its request")
    if [item.get("id") for item in summary_views if isinstance(item, dict)] != requested_ids:
        raise VisualFidelityError(f"{label} summary view records do not match its request")
    for item, request_view in zip(manifest_views, requested):
        if not isinstance(item, dict) or item.get("status") != "ok":
            raise VisualFidelityError(f"{label} camera {request_view['id']} did not render successfully")
        camera = item.get("camera")
        if not isinstance(camera, dict):
            raise VisualFidelityError(f"{label} camera {request_view['id']} has no returned pose")
        for field in ("x", "y", "z", "angle", "horiz", "sector"):
            if field in request_view and camera.get(field) != request_view[field]:
                raise VisualFidelityError(f"{label} returned pose differs from request for {request_view['id']}: {field}")
    for item, manifest_view in zip(summary_views, manifest_views):
        if not isinstance(item, dict) or item.get("status") != "ok":
            raise VisualFidelityError(f"{label} summary marks a requested camera unsuccessful")
        if item.get("camera") != manifest_view.get("camera"):
            raise VisualFidelityError(f"{label} summary camera disagrees with its manifest")

    screenshots = summary.get("screenshots")
    if not isinstance(screenshots, list):
        raise VisualFidelityError(f"{label} has no screenshot identities")
    screenshots_by_id = {item.get("id"): item for item in screenshots if isinstance(item, dict)}
    if len(screenshots_by_id) != len(screenshots) or set(screenshots_by_id) != set(requested_ids):
        raise VisualFidelityError(f"{label} screenshot ids do not match requested views")
    images: dict[str, Any] = {}
    image_metadata: dict[str, dict[str, Any]] = {}
    for request_view in requested:
        view_id = request_view["id"]
        screenshot = screenshots_by_id[view_id]
        if screenshot.get("present") is not True or screenshot.get("valid_png") is not True:
            raise VisualFidelityError(f"{label} screenshot is missing or invalid for {view_id}")
        if not isinstance(screenshot.get("sha256"), str):
            raise VisualFidelityError(f"{label} screenshot has no recorded SHA-256 for {view_id}")
        image_path = _resolved(screenshot.get("path"), f"{label} screenshot")
        if not _is_within(image_path, run_root):
            raise VisualFidelityError(f"{label} screenshot escapes its output root: {image_path}")
        current_hash = _verify_output_identity(image_path, run_root, recorded_files, f"{label} screenshot {view_id}")
        if current_hash != screenshot["sha256"]:
            raise VisualFidelityError(f"{label} screenshot no longer matches its summary identity: {view_id}")
        if not isinstance(screenshot.get("width"), int) or not isinstance(screenshot.get("height"), int):
            raise VisualFidelityError(f"{label} screenshot dimensions are missing for {view_id}")
        manifest_view = manifest_views[requested_ids.index(view_id)]
        if manifest_view.get("screenshot") is None:
            raise VisualFidelityError(f"{label} manifest did not record the screenshot for {view_id}")
        manifest_image = (manifest_path.parent / manifest_view["screenshot"]).resolve()
        if manifest_image != image_path:
            raise VisualFidelityError(f"{label} manifest screenshot path disagrees for {view_id}")
        image_metadata[view_id] = {
            "path": str(image_path), "sha256": current_hash,
            "width": screenshot["width"], "height": screenshot["height"],
        }
    return {
        "summary": summary,
        "summary_path": summary_path.resolve(),
        "request": request,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "run_root": run_root,
        "recorded_files": recorded_files,
        "image_metadata": image_metadata,
        "images": images,
    }


def _resource_signature(run: dict[str, Any], label: str) -> tuple[str, tuple[tuple[str, int, str], ...]]:
    request_root = _resolved(run["request"].get("resource_dir"), f"{label} resource directory")
    before = run["summary"]["input_integrity"]["before"]
    assets = before.get("assets")
    if not isinstance(assets, list):
        raise VisualFidelityError(f"{label} has no asset identity list")
    signatures = []
    for item in assets:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise VisualFidelityError(f"{label} has a malformed asset identity")
        asset_path = _resolved(item["path"], f"{label} asset")
        if not _is_within(asset_path, request_root):
            raise VisualFidelityError(f"{label} asset identity lies outside resource_dir: {asset_path}")
        relative = asset_path.relative_to(request_root).as_posix().casefold()
        size, digest = item.get("size"), item.get("sha256")
        if not isinstance(size, int) or not isinstance(digest, str):
            raise VisualFidelityError(f"{label} asset identity is incomplete: {relative}")
        signatures.append((relative, size, digest))
    return str(request_root).casefold(), tuple(sorted(signatures))


def _validate_current_file(identity: dict[str, Any], label: str,
                           hash_cache: dict[tuple[str, int, int], str]) -> None:
    """Recheck that a recorded input is still present and has its recorded bytes."""
    path = _resolved(identity.get("path"), f"{label} input")
    try:
        stat = path.stat()
    except OSError as exc:
        raise VisualFidelityError(f"{label} current input file is missing or unreadable: {path}") from exc
    if not path.is_file():
        raise VisualFidelityError(f"{label} current input is not a regular file: {path}")
    expected_size, expected_hash = identity.get("size"), identity.get("sha256")
    if not isinstance(expected_size, int) or not isinstance(expected_hash, str):
        raise VisualFidelityError(f"{label} recorded input identity is incomplete: {path}")
    if stat.st_size != expected_size:
        raise VisualFidelityError(f"{label} current input size differs from its recorded identity: {path}")
    cache_key = (os.path.normcase(str(path)), stat.st_size, stat.st_mtime_ns)
    actual_hash = hash_cache.get(cache_key)
    if actual_hash is None:
        actual_hash = _sha256(path)
        hash_cache[cache_key] = actual_hash
    if actual_hash != expected_hash:
        raise VisualFidelityError(f"{label} current input SHA-256 differs from its recorded identity: {path}")


def _validate_current_inputs(run: dict[str, Any], label: str,
                             hash_cache: dict[tuple[str, int, int], str]) -> None:
    recorded = run["summary"]["input_integrity"]["before"]
    _validate_current_file(recorded["map"], f"{label} MAP", hash_cache)
    _validate_current_file(recorded["executable"], f"{label} observer executable", hash_cache)
    assets = recorded.get("assets")
    if not isinstance(assets, list):
        raise VisualFidelityError(f"{label} has no recorded resource file identities")
    for index, identity in enumerate(assets):
        if not isinstance(identity, dict):
            raise VisualFidelityError(f"{label} has a malformed resource identity at index {index}")
        _validate_current_file(identity, f"{label} resource file {index}", hash_cache)


def _load_rgb_images(run: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise VisualFidelityError(
            "Pillow is required for pixel comparison; install the optional BloodForge visual dependency."
        ) from exc
    loaded: dict[str, Any] = {}
    request = run["request"]
    expected_size = (request.get("width"), request.get("height"))
    for view_id, metadata in run["image_metadata"].items():
        path = Path(metadata["path"])
        try:
            with Image.open(path) as source:
                source.verify()
            with Image.open(path) as source:
                image = source.convert("RGB")
        except Exception as exc:
            raise VisualFidelityError(f"{label} screenshot cannot be decoded as PNG for {view_id}: {exc}") from exc
        if image.format is not None and image.format != "RGB":
            raise VisualFidelityError(f"{label} screenshot could not be normalized to RGB for {view_id}")
        if image.size != expected_size:
            raise VisualFidelityError(
                f"{label} screenshot dimensions {image.size} do not match request {expected_size} for {view_id}"
            )
        if image.size != (metadata["width"], metadata["height"]):
            raise VisualFidelityError(f"{label} screenshot dimensions disagree with summary for {view_id}")
        loaded[view_id] = image
    return loaded


def _compare_rgb(source: Any, candidate: Any) -> dict[str, Any]:
    width, height = source.size
    source_bytes = source.tobytes()
    candidate_bytes = candidate.tobytes()
    equal = 0
    channel_totals = [0, 0, 0]
    for offset in range(0, len(source_bytes), 3):
        differences = (
            abs(source_bytes[offset] - candidate_bytes[offset]),
            abs(source_bytes[offset + 1] - candidate_bytes[offset + 1]),
            abs(source_bytes[offset + 2] - candidate_bytes[offset + 2]),
        )
        if differences == (0, 0, 0):
            equal += 1
        channel_totals[0] += differences[0]
        channel_totals[1] += differences[1]
        channel_totals[2] += differences[2]
    pixels = width * height
    return {
        "image_dimensions": {"width": width, "height": height},
        "pixel_count": pixels,
        "equal_pixel_count": equal,
        "changed_pixel_count": pixels - equal,
        "mae_per_channel": {
            "red": channel_totals[0] / pixels,
            "green": channel_totals[1] / pixels,
            "blue": channel_totals[2] / pixels,
        },
    }


def compare_observations(source_summary_path: str | Path,
                         candidate_summary_path: str | Path) -> dict[str, Any]:
    """Compare same-pose screenshots from two successful native observer runs.

    Both runs' MAPs, observer binary, and resource files must still be available
    and match the hashes recorded when they were rendered. Both runs must come
    from the same observer binary and resource tree, use
    identical render settings and camera poses, and retain all recorded input
    and screenshot identities. The result is a static paired-pixel report only.
    """
    source_path = _resolved(source_summary_path, "source summary")
    candidate_path = _resolved(candidate_summary_path, "candidate summary")
    source = _validate_run(source_path, "source")
    candidate = _validate_run(candidate_path, "candidate")
    current_hash_cache: dict[tuple[str, int, int], str] = {}
    _validate_current_inputs(source, "source", current_hash_cache)
    _validate_current_inputs(candidate, "candidate", current_hash_cache)

    source_request, candidate_request = source["request"], candidate["request"]
    for key in ("width", "height", "brightness"):
        if source_request.get(key) != candidate_request.get(key):
            raise VisualFidelityError(f"observer request setting differs: {key}")
    for key in ("width", "height"):
        value = source_request.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise VisualFidelityError(f"observer request has an invalid {key}")
    brightness = source_request.get("brightness")
    if isinstance(brightness, bool) or not isinstance(brightness, int):
        raise VisualFidelityError("observer request has an invalid brightness")
    if source_request.get("width") != source["summary"]["renderer"].get("width") or source_request.get("height") != source["summary"]["renderer"].get("height"):
        raise VisualFidelityError("source renderer dimensions disagree with its request")
    if candidate_request.get("width") != candidate["summary"]["renderer"].get("width") or candidate_request.get("height") != candidate["summary"]["renderer"].get("height"):
        raise VisualFidelityError("candidate renderer dimensions disagree with its request")
    if source_request.get("brightness") != source["summary"]["renderer"].get("brightness") or candidate_request.get("brightness") != candidate["summary"]["renderer"].get("brightness"):
        raise VisualFidelityError("renderer brightness disagrees with observer request")

    source_exe = source["summary"]["input_integrity"]["before"]["executable"]
    candidate_exe = candidate["summary"]["input_integrity"]["before"]["executable"]
    if source_exe.get("sha256") != candidate_exe.get("sha256"):
        raise VisualFidelityError("native observer executable SHA-256 differs")
    source_resources = _resource_signature(source, "source")
    candidate_resources = _resource_signature(candidate, "candidate")
    if source_resources != candidate_resources:
        raise VisualFidelityError("native observer resource identities differ")

    source_views = source_request["views"]
    candidate_views = candidate_request["views"]
    if source_views != candidate_views:
        raise VisualFidelityError("camera poses differ; paired views require exact request pose matches")
    if source_request.get("resource_dir") != candidate_request.get("resource_dir"):
        raise VisualFidelityError("observer resource directory differs")
    if source["summary"]["renderer"].get("engine") != candidate["summary"]["renderer"].get("engine"):
        raise VisualFidelityError("native renderer identity differs")

    source_images = _load_rgb_images(source, "source")
    candidate_images = _load_rgb_images(candidate, "candidate")
    views = []
    for request_view in source_views:
        view_id = request_view["id"]
        source_image = source_images[view_id]
        candidate_image = candidate_images[view_id]
        if source_image.size != candidate_image.size:
            raise VisualFidelityError(f"image dimensions differ for camera {view_id}")
        metrics = _compare_rgb(source_image, candidate_image)
        views.append({
            "id": view_id,
            "camera": {key: request_view[key] for key in ("x", "y", "z", "angle", "horiz", "sector") if key in request_view},
            **metrics,
            "source_png": source["image_metadata"][view_id],
            "candidate_png": candidate["image_metadata"][view_id],
        })

    return {
        "$schema": "bloodforge.paired-static-visual-comparison",
        "schema_version": 1,
        "claim": "paired-static-view pixel comparison only",
        "scope": {
            "fixture_context": "Must be supplied and described separately; this API does not determine whether surrounding geometry was generated or held fixed.",
            "limitations": [
                "No overall visual quality score is computed.",
                "No automatic verification or pass/fail quality judgment is made.",
                "Pixel differences do not prove gameplay, traversal, door motion, or semantic equivalence.",
            ],
        },
        "claims": {
            "same_observer_sha256": True,
            "same_resource_identities": True,
            "same_render_settings": True,
            "same_camera_poses": True,
            "source_inputs_unchanged": True,
            "candidate_inputs_unchanged": True,
            "source_png_hashes_match_recorded_outputs": True,
            "candidate_png_hashes_match_recorded_outputs": True,
            "source_current_inputs_match_recorded_hashes": True,
            "candidate_current_inputs_match_recorded_hashes": True,
            "automatic_verification": False,
            "overall_quality_judged": False,
        },
        "runs": {
            "source": {
                "summary": str(source_path),
                "map": source["summary"]["input_integrity"]["before"]["map"],
                "observer": source_exe,
                "resource_dir": source_request["resource_dir"],
                "settings": {key: source_request[key] for key in ("width", "height", "brightness")},
            },
            "candidate": {
                "summary": str(candidate_path),
                "map": candidate["summary"]["input_integrity"]["before"]["map"],
                "observer": candidate_exe,
                "resource_dir": candidate_request["resource_dir"],
                "settings": {key: candidate_request[key] for key in ("width", "height", "brightness")},
            },
        },
        "views": views,
    }
