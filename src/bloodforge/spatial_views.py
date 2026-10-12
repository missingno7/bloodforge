"""Source-bound summaries of deterministic native-renderer view sequences.

The observer reports what reached its renderer, not semantic rooms or
traversability. This module joins those native references to caller-supplied
derived regions while preserving that distinction.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from . import visual_fidelity as _visual
from .native import read_map


class SpatialViewError(ValueError):
    """An observation or spatial analysis cannot be safely joined."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _analysis_identity(analysis: dict[str, Any]) -> dict[str, str]:
    """Hash analytical content, excluding report/store location metadata."""
    payload = deepcopy(analysis)
    for key in ("report_path", "report_hash", "artifacts", "knowledge_indexed"):
        payload.pop(key, None)
    source = payload.get("source")
    if isinstance(source, dict):
        source.pop("path", None)
        source.pop("provenance", None)
    return {
        "algorithm": "sha256",
        "sha256": hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest(),
        "payload_definition": (
            "canonical JSON of the supplied analysis, excluding top-level report_path, "
            "report_hash, artifacts, knowledge_indexed and source.path/source.provenance"
        ),
    }


def _pose_key(pose: Mapping[str, Any]) -> str:
    exact = {key: pose[key] for key in ("x", "y", "z", "angle", "horiz", "sector") if key in pose}
    return "pose:" + hashlib.sha256(_canonical(exact).encode("utf-8")).hexdigest()


def _analysis_regions(analysis: object, source_sha256: str, level: Any) -> list[dict[str, Any]]:
    if not isinstance(analysis, dict):
        raise SpatialViewError("analysis must be an object")
    if analysis.get("schema") != "bloodforge.spatial-understanding.v1":
        raise SpatialViewError("analysis does not use the supported spatial-understanding schema")
    source = analysis.get("source")
    if not isinstance(source, dict) or source.get("sha256") != source_sha256:
        raise SpatialViewError("spatial analysis source SHA-256 does not match the observed MAP")
    counts = analysis.get("native_counts")
    if not isinstance(counts, dict) or counts != {
        "sectors": len(level.sectors), "walls": len(level.walls), "sprites": len(level.sprites),
    }:
        raise SpatialViewError("analysis native counts do not match the observed MAP")
    sectors = analysis.get("sectors")
    if not isinstance(sectors, list):
        raise SpatialViewError("analysis sectors must be a list")
    ids: set[int] = set()
    for item in sectors:
        if not isinstance(item, dict):
            raise SpatialViewError("analysis sector record must be an object")
        sector_id = item.get("id")
        if isinstance(sector_id, bool) or not isinstance(sector_id, int) or not 0 <= sector_id < len(level.sectors):
            raise SpatialViewError(f"analysis contains invalid native sector id {sector_id!r}")
        if sector_id in ids:
            raise SpatialViewError(f"analysis repeats native sector id {sector_id}")
        ids.add(sector_id)
    scope = analysis.get("scope")
    selected = scope.get("selected_sectors") if isinstance(scope, dict) else None
    if not isinstance(selected, list) or selected != [item["id"] for item in sectors]:
        raise SpatialViewError("analysis selected-sector scope disagrees with its sector records")
    regions = analysis.get("regions")
    if not isinstance(regions, list):
        raise SpatialViewError("analysis regions must be a list")
    result = []
    region_ids: set[str] = set()
    region_memberships: set[tuple[str, tuple[int, ...]]] = set()
    for item in regions:
        if not isinstance(item, dict):
            raise SpatialViewError("analysis region record must be an object")
        region_id, view, members = item.get("id"), item.get("view"), item.get("sectors")
        if not isinstance(region_id, str) or not region_id or region_id in region_ids:
            raise SpatialViewError(f"analysis has an invalid or repeated region id {region_id!r}")
        if view not in ("coplanar", "finish"):
            raise SpatialViewError(f"region {region_id!r} has unsupported view kind {view!r}")
        if item.get("evidence_kind") != "continuity-hypothesis":
            raise SpatialViewError(f"region {region_id!r} is not a continuity hypothesis")
        if not isinstance(members, list) or not members:
            raise SpatialViewError(f"region {region_id!r} must name one or more native sectors")
        normalized = []
        for member in members:
            if isinstance(member, bool) or not isinstance(member, int) or member not in ids:
                raise SpatialViewError(f"region {region_id!r} references a sector absent from analysis")
            normalized.append(member)
        if len(normalized) != len(set(normalized)):
            raise SpatialViewError(f"region {region_id!r} repeats a native sector")
        membership = (view, tuple(sorted(normalized)))
        if membership in region_memberships:
            raise SpatialViewError(f"analysis repeats region membership for view {view!r}: {membership[1]}")
        region_ids.add(region_id)
        region_memberships.add(membership)
        result.append({"id": region_id, "view": view, "sectors": sorted(normalized)})
    return result


def _wall_owners(level: Any) -> list[int | None]:
    """Map each native wall to its owning sector, rejecting ambiguous ranges."""
    owners: list[int | None] = [None] * len(level.walls)
    for sector_id, sector in enumerate(level.sectors):
        start, count = sector.wallptr, sector.wallnum
        if (isinstance(start, bool) or isinstance(count, bool) or
                not isinstance(start, int) or not isinstance(count, int) or
                start < 0 or count < 0 or start + count > len(level.walls)):
            raise SpatialViewError(f"MAP sector {sector_id} has an invalid wall ownership range")
        for wall_id in range(start, start + count):
            if owners[wall_id] is not None:
                raise SpatialViewError(f"MAP wall {wall_id} has multiple owning sectors")
            owners[wall_id] = sector_id
    return owners


def _native_ref(surface: Mapping[str, Any], sector_count: int,
                wall_count: int, sprite_count: int) -> dict[str, Any]:
    kind = surface.get("kind")
    sector = surface.get("sector")
    pixels = surface.get("pixels")
    if not isinstance(kind, str) or isinstance(sector, bool) or not isinstance(sector, int):
        raise SpatialViewError("observer surface is missing native kind/sector identity")
    if not 0 <= sector < sector_count:
        raise SpatialViewError(f"observer surface has out-of-range sector id {sector}")
    if isinstance(pixels, bool) or not isinstance(pixels, int) or pixels < 0:
        raise SpatialViewError("observer surface has invalid pixel sample count")
    ref: dict[str, Any] = {"kind": kind, "sector": sector}
    for field, limit in (("wall", wall_count), ("sprite", sprite_count)):
        if field in surface:
            value = surface[field]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < limit:
                raise SpatialViewError(f"observer surface has invalid native {field} id {value!r}")
            ref[field] = value
    for field in ("picnum", "shade", "pal", "pixels", "columns", "bbox"):
        if field in surface:
            ref[field] = surface[field]
    return ref


def _validate_surface_owner(surface: Mapping[str, Any], level: Any,
                            wall_owners: list[int | None]) -> None:
    kind, sector = surface["kind"], surface["sector"]
    wall, sprite = surface.get("wall"), surface.get("sprite")
    wall_kinds = {"wall", "upper", "lower", "masked"}
    sector_kinds = {"floor", "ceiling", "sky"}
    if kind in wall_kinds:
        if wall is None or sprite is not None:
            raise SpatialViewError(f"observer {kind} surface must identify exactly one native wall")
        if wall_owners[wall] != sector:
            raise SpatialViewError(
                f"observer {kind} wall {wall} is owned by MAP sector {wall_owners[wall]!r}, not reported sector {sector}"
            )
    elif kind == "sprite":
        if sprite is None or wall is not None:
            raise SpatialViewError("observer sprite surface must identify exactly one native sprite")
        if level.sprites[sprite].sectnum != sector:
            raise SpatialViewError(
                f"observer sprite {sprite} belongs to MAP sector {level.sprites[sprite].sectnum}, not reported sector {sector}"
            )
    elif kind in sector_kinds:
        if wall is not None or sprite is not None:
            raise SpatialViewError(f"observer {kind} surface must not identify a wall or sprite")
    else:
        raise SpatialViewError(f"observer surface has unsupported native kind {kind!r}")


def _surface_sort_key(surface: Mapping[str, Any]) -> tuple[Any, ...]:
    return (-int(surface["pixels"]), str(surface["kind"]), int(surface["sector"]),
            int(surface.get("wall", -1)), int(surface.get("sprite", -1)),
            int(surface.get("picnum", -1)))


def _resource_digest(assets: list[dict[str, Any]]) -> str:
    normalized = sorted(
        ({"path": str(item["path"]).replace("\\", "/").casefold(),
          "size": item["size"], "sha256": item["sha256"]} for item in assets),
        key=lambda item: item["path"],
    )
    return hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()


def _pose_geometry(level: Any, pose: Mapping[str, Any]) -> dict[str, Any]:
    # Kept as a hard dependency: observer acceptance alone is not the separate
    # source geometry check required for a viewpoint sequence.
    try:
        from .spatial_geometry import validate_pose
    except ImportError as exc:
        raise SpatialViewError(f"spatial pose validation dependency is unavailable: {exc}") from exc

    result = validate_pose(level, dict(pose))
    if not isinstance(result, dict) or result.get("status") not in ("valid", "invalid", "unsupported"):
        raise SpatialViewError("spatial_geometry.validate_pose returned an unsupported result")
    return result


def summarize_sequence(summary_path: str | Path, analysis: dict[str, Any]) -> dict[str, Any]:
    """Join a validated observer run to source-bound overlapping region views.

    Region membership comes from ``analysis`` and is not inferred here. Surface
    pixel samples are renderer contributions and may overlap or exceed the
    framebuffer pixel count. Only objects explicitly present in the manifest
    are considered visible or occluded.
    """
    try:
        run = _visual._validate_run(Path(summary_path).resolve(), "spatial sequence")
        _visual._validate_current_inputs(run, "spatial sequence", {})
        _visual._load_rgb_images(run, "spatial sequence")
    except _visual.VisualFidelityError as exc:
        raise SpatialViewError(f"observation validation failed: {exc}") from exc

    summary = run["summary"]
    input_before = summary["input_integrity"]["before"]
    map_identity = input_before["map"]
    try:
        level = read_map(map_identity["path"])
    except Exception as exc:
        raise SpatialViewError(f"cannot parse observed source MAP for pose validation: {exc}") from exc
    regions = _analysis_regions(analysis, map_identity["sha256"], level)
    wall_owners = _wall_owners(level)
    selected_sectors = list(analysis["scope"]["selected_sectors"])
    selected_sector_set = set(selected_sectors)

    views = []
    first_seen_sectors: dict[int, dict[str, Any]] = {}
    first_seen_regions: dict[str, dict[str, Any]] = {}
    seen_sectors: set[int] = set()
    seen_regions: set[str] = set()
    manifest_views = run["manifest"]["views"]
    request_views = run["request"]["views"]
    for index, (manifest_view, request_view) in enumerate(zip(manifest_views, request_views)):
        view_id = request_view["id"]
        if manifest_view.get("status") != "ok":
            raise SpatialViewError(f"view {view_id!r} did not render successfully")
        returned_camera = manifest_view["camera"]
        camera = {key: returned_camera[key] for key in ("x", "y", "z", "angle", "horiz", "sector")
                  if key in returned_camera}
        geometry = _pose_geometry(level, camera)
        if geometry.get("status") == "invalid":
            raise SpatialViewError(f"view {view_id!r} is invalid under source geometry: {geometry.get('reason')}")
        surfaces_value = manifest_view.get("surfaces")
        occluded_value = manifest_view.get("occluded")
        unorderable_value = manifest_view.get("unorderable")
        if not isinstance(surfaces_value, list) or not isinstance(occluded_value, list) or not isinstance(unorderable_value, list):
            raise SpatialViewError(f"view {view_id!r} has malformed renderer object lists")
        surfaces = [_native_ref(item, len(level.sectors), len(level.walls), len(level.sprites))
                    for item in surfaces_value if isinstance(item, dict)]
        if len(surfaces) != len(surfaces_value):
            raise SpatialViewError(f"view {view_id!r} has a malformed rendered surface")
        for surface in surfaces:
            _validate_surface_owner(surface, level, wall_owners)
        occluded = [_native_ref({**item, "pixels": 0}, len(level.sectors), len(level.walls), len(level.sprites))
                    for item in occluded_value if isinstance(item, dict)]
        if len(occluded) != len(occluded_value):
            raise SpatialViewError(f"view {view_id!r} has a malformed occluded object")
        for surface in occluded:
            _validate_surface_owner(surface, level, wall_owners)

        visible_surfaces = [item for item in surfaces if item["pixels"] > 0]
        visible_sectors = sorted({int(item["sector"]) for item in visible_surfaces})
        visible_sectors_in_scope = sorted(selected_sector_set & set(visible_sectors))
        visible_sectors_outside_scope = sorted(set(visible_sectors) - selected_sector_set)
        visible_refs_in_scope = [item for item in visible_surfaces if item["sector"] in selected_sector_set]
        visible_refs_outside_scope = [item for item in visible_surfaces if item["sector"] not in selected_sector_set]
        sector_pixels: dict[int, int] = {}
        for surface in visible_surfaces:
            sector_id = int(surface["sector"])
            sector_pixels[sector_id] = sector_pixels.get(sector_id, 0) + int(surface["pixels"])
        visible_regions = []
        region_pixels: dict[str, int] = {}
        for region in regions:
            contributions = [item for item in visible_surfaces if item["sector"] in region["sectors"]]
            sample_sum = sum(int(item["pixels"]) for item in contributions)
            region_pixels[region["id"]] = sample_sum
            if contributions:
                visible_regions.append({
                    "id": region["id"], "view": region["view"], "sectors": region["sectors"],
                    "contributing_native_refs": contributions,
                    "summed_surface_pixel_samples": sample_sum,
                })
        pose_key = _pose_key(camera)
        current_seen_sectors = set(visible_sectors)
        current_seen_regions = {item["id"] for item in visible_regions}
        for sector_id in sorted(current_seen_sectors - seen_sectors):
            first_seen_sectors[sector_id] = {"view_id": view_id, "pose_key": pose_key, "camera": camera}
        for region_id in sorted(current_seen_regions - seen_regions):
            first_seen_regions[region_id] = {"view_id": view_id, "pose_key": pose_key, "camera": camera}
        seen_sectors.update(current_seen_sectors)
        seen_regions.update(current_seen_regions)

        ordered_surfaces = sorted(visible_surfaces, key=_surface_sort_key)
        views.append({
            "index": index,
            "id": view_id,
            "pose_key": pose_key,
            "camera": camera,
            "pose_geometry": geometry,
            "status": "ok",
            "frame": manifest_view.get("frame"),
            "visible_native_sectors": visible_sectors,
            "visible_native_sectors_in_analysis_scope": visible_sectors_in_scope,
            "visible_native_sectors_outside_analysis_scope": visible_sectors_outside_scope,
            "visible_native_refs": visible_surfaces,
            "visible_native_refs_in_analysis_scope": visible_refs_in_scope,
            "visible_native_refs_outside_analysis_scope": visible_refs_outside_scope,
            "surface_pixel_samples_by_sector": {str(key): sector_pixels[key] for key in sorted(sector_pixels)},
            "visible_regions": visible_regions,
            "summed_region_pixel_samples": region_pixels,
            "dominant_surfaces_top5": ordered_surfaces[:5],
            "occluded_native_refs": occluded,
            "unorderable": manifest_view["unorderable"],
        })

    transitions = []
    for before, after in zip(views, views[1:]):
        sectors_before = set(before["visible_native_sectors"])
        sectors_after = set(after["visible_native_sectors"])
        regions_before = {item["id"] for item in before["visible_regions"]}
        regions_after = {item["id"] for item in after["visible_regions"]}
        sector_ids = sorted(sectors_before | sectors_after)
        transitions.append({
            "from_view": before["id"], "to_view": after["id"],
            "gained_native_sectors": sorted(sectors_after - sectors_before),
            "lost_native_sectors": sorted(sectors_before - sectors_after),
            "gained_region_ids": sorted(regions_after - regions_before),
            "lost_region_ids": sorted(regions_before - regions_after),
            "sector_pixel_sample_deltas": {
                str(sector_id): int(after["surface_pixel_samples_by_sector"].get(str(sector_id), 0))
                - int(before["surface_pixel_samples_by_sector"].get(str(sector_id), 0))
                for sector_id in sector_ids
            },
            "region_pixel_sample_deltas": {
                region_id: int(after["summed_region_pixel_samples"].get(region_id, 0))
                - int(before["summed_region_pixel_samples"].get(region_id, 0))
                for region_id in sorted(set(before["summed_region_pixel_samples"]) |
                                        set(after["summed_region_pixel_samples"]))
            },
        })

    exe = input_before["executable"]
    assets = input_before["assets"]
    return {
        "$schema": "bloodforge.spatial-view-sequence",
        "schema_version": 1,
        "status": "completed",
        "source": {"sha256": map_identity["sha256"], "path": map_identity["path"], "size": map_identity["size"]},
        "observation": {"summary_path": str(run["summary_path"]),
                        "manifest_path": str(run["manifest_path"]),
                        "renderer": summary["renderer"],
                        "settings": {key: run["request"][key]
                                     for key in ("width", "height", "brightness", "resource_dir")}},
        "observer": {"sha256": exe["sha256"], "path": exe["path"], "size": exe["size"]},
        "resources": {"root": run["request"]["resource_dir"], "file_count": len(assets),
                      "identity_sha256": _resource_digest(assets)},
        "analysis": {
            "schema": analysis.get("schema"),
            "extractor": analysis.get("extractor"),
            "selected_native_sectors": selected_sectors,
            "scope_validation": analysis["scope_validation"] if "scope_validation" in analysis else None,
            "scope_validation_supplied": "scope_validation" in analysis,
            "identity": _analysis_identity(analysis),
            "regions": regions,
            "sector_count": len(analysis["sectors"]),
            "region_count": len(regions),
        },
        "claims": {
            "analysis_source_sha256_matches_map": True,
            "current_map_observer_and_resource_hashes_validated": True,
            "manifest_and_requested_views_validated": True,
            "screenshots_validated": True,
            "native_refs_are_map_sha_scoped": True,
            "traversability_inferred": False,
            "depth_inferred": False,
            "negative_space_intent_inferred": False,
        },
        "first_seen": {"sectors": {str(key): value for key, value in sorted(first_seen_sectors.items())},
                       "regions": first_seen_regions},
        "views": views,
        "transitions": transitions,
        "limitations": [
            "sampled_static_views_only",
            "no route traversal or movement was measured",
            "no distance/depth is inferred from surface records",
            "surface pixel samples are nonexclusive renderer contributions and can exceed frame area",
            "region membership is supplied by source analysis; this module does not classify regions",
            "outside-analysis-scope sectors retain renderer evidence only and are not assigned spatial regions",
            "objects absent from the manifest are not inferred to be occluded or unreachable",
            "renderer limitations are inherited from the observation manifest",
        ],
    }


def _region_membership_key(region: Mapping[str, Any]) -> tuple[str, tuple[int, ...]]:
    return str(region.get("view")), tuple(sorted(int(value) for value in region.get("sectors", [])))


def compare_sequences(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare visibility measurements at exact, invariant camera poses.

    Region IDs may change after a map edit, so region results pair only when
    their view kind and exact native-sector membership agree. Sector indexes
    are compared as source-local IDs; the caller must establish that the edit
    preserved those indexes.
    """
    for label, sequence in (("before", before), ("after", after)):
        if not isinstance(sequence, dict) or sequence.get("$schema") != "bloodforge.spatial-view-sequence" or sequence.get("status") != "completed":
            raise SpatialViewError(f"{label} is not a completed spatial view sequence")
    if before.get("observer", {}).get("sha256") != after.get("observer", {}).get("sha256"):
        raise SpatialViewError("observer executable identities differ")
    if before.get("resources", {}).get("identity_sha256") != after.get("resources", {}).get("identity_sha256"):
        raise SpatialViewError("observer resource identities differ")
    if before.get("observation", {}).get("renderer") != after.get("observation", {}).get("renderer"):
        raise SpatialViewError("renderer identities differ")
    if before.get("observation", {}).get("settings") != after.get("observation", {}).get("settings"):
        raise SpatialViewError("render settings differ")
    before_views, after_views = before.get("views"), after.get("views")
    if not isinstance(before_views, list) or not isinstance(after_views, list) or not before_views:
        raise SpatialViewError("both sequences must contain views")
    if [item.get("id") for item in before_views] != [item.get("id") for item in after_views]:
        raise SpatialViewError("viewpoint sequence IDs differ")
    for left, right in zip(before_views, after_views):
        if left.get("camera") != right.get("camera") or left.get("pose_key") != right.get("pose_key"):
            raise SpatialViewError(f"camera pose differs for sequence view {left.get('id')!r}")

    before_regions = before.get("analysis", {}).get("regions", [])
    after_regions = after.get("analysis", {}).get("regions", [])
    before_by_members: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    after_by_members: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    for region in before_regions:
        before_by_members[_region_membership_key(region)] = region
    for region in after_regions:
        after_by_members[_region_membership_key(region)] = region
    matched_region_keys = sorted(set(before_by_members) & set(after_by_members))
    unmatched_before = sorted(set(before_by_members) - set(after_by_members))
    unmatched_after = sorted(set(after_by_members) - set(before_by_members))

    per_view = []
    for left, right in zip(before_views, after_views):
        left_sectors, right_sectors = set(left.get("visible_native_sectors", [])), set(right.get("visible_native_sectors", []))
        left_pixels = {int(key): int(value) for key, value in left.get("surface_pixel_samples_by_sector", {}).items()}
        right_pixels = {int(key): int(value) for key, value in right.get("surface_pixel_samples_by_sector", {}).items()}
        sector_deltas = []
        for sector in sorted(left_sectors | right_sectors | set(left_pixels) | set(right_pixels)):
            was, now = sector in left_sectors, sector in right_sectors
            sector_deltas.append({
                "sector": sector, "before_visible": was, "after_visible": now,
                "visibility_delta": int(now) - int(was),
                "before_pixel_samples": left_pixels.get(sector, 0),
                "after_pixel_samples": right_pixels.get(sector, 0),
                "pixel_sample_delta": right_pixels.get(sector, 0) - left_pixels.get(sector, 0),
            })
        region_deltas = []
        for key in matched_region_keys:
            before_region = before_by_members[key]
            after_region = after_by_members[key]
            before_id, after_id = before_region["id"], after_region["id"]
            before_visible = before_id in {item.get("id") for item in left.get("visible_regions", [])}
            after_visible = after_id in {item.get("id") for item in right.get("visible_regions", [])}
            region_deltas.append({
                "before_id": before_id, "after_id": after_id,
                "view": key[0], "sectors": list(key[1]),
                "before_visible": before_visible, "after_visible": after_visible,
                "visibility_delta": int(after_visible) - int(before_visible),
                "before_pixel_samples": int(left.get("summed_region_pixel_samples", {}).get(before_id, 0)),
                "after_pixel_samples": int(right.get("summed_region_pixel_samples", {}).get(after_id, 0)),
                "pixel_sample_delta": int(right.get("summed_region_pixel_samples", {}).get(after_id, 0))
                                      - int(left.get("summed_region_pixel_samples", {}).get(before_id, 0)),
            })
        per_view.append({"id": left["id"], "camera": left["camera"],
                         "sector_visibility_deltas": sector_deltas,
                         "region_visibility_deltas": region_deltas})

    return {
        "$schema": "bloodforge.spatial-view-sequence-comparison",
        "schema_version": 1,
        "claim": "native renderer visibility comparison at exact matching static camera poses",
        "maps": {"before_sha256": before.get("source", {}).get("sha256"),
                 "after_sha256": after.get("source", {}).get("sha256")},
        "pose_invariants": {"view_ids_match": True, "exact_camera_poses_match": True,
                            "native_sector_indices_match_in_pose_records": True,
                            "same_observer_sha256": True, "same_resource_identities": True,
                            "sector_index_stability_in_maps": "caller must establish from the controlled edit diff"},
        "region_join": {"matched_by": "view kind plus exact sorted native sector membership; region IDs may differ",
                        "matched_count": len(matched_region_keys),
                        "unmatched_before": [{"id": before_by_members[key]["id"], "view": key[0], "sectors": list(key[1])}
                                             for key in unmatched_before],
                        "unmatched_after": [{"id": after_by_members[key]["id"], "view": key[0], "sectors": list(key[1])}
                                            for key in unmatched_after]},
        "per_view": per_view,
        "limitations": [
            "sampled_static_views_only; no traversal or dynamic state is compared",
            "surface pixel samples are nonexclusive renderer contributions, not disjoint screen occupancy",
            "native sector IDs are paired numerically; controlled edits must preserve index meaning",
            "unmatched region memberships are not forced into semantic matches",
        ],
    }
