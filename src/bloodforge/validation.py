"""Structural checks for native Blood levels.

This module checks static topology and references. It does not simulate the
engine, resolve art, interpret trigger records, or test arbitrary polygon
self-intersections.
"""
from __future__ import annotations

from collections import defaultdict
import struct
from typing import Iterable

from .native import NativeMap


# Heuristic clearance in Blood/Build Z units. NBlood's player posture has an
# eye height, but that is a camera offset rather than a collision-height rule.
# Keep the estimate explicit and report it as an assumption to callers.
PLAYER_CLEARANCE_Z_THRESHOLD = 8192


def _issue(code: str, kind: str, index: int, message: str) -> dict[str, object]:
    return {"code": code, "kind": kind, "index": index, "message": message}


def _dimension(issues: list[dict[str, object]], *, partial: bool = False,
               metrics: dict[str, object] | None = None) -> dict[str, object]:
    errors = [item for item in issues if not str(item["code"]).endswith("_unsupported")
              and not str(item["code"]).endswith("_assumption")
              and str(item["code"]) not in {"runtime_not_simulated", "visual_assets_not_resolved",
                                             "mechanisms_not_interpreted"}]
    status = "fail" if errors else "partial" if partial else "pass"
    return {"status": status, "error_count": len(errors),
            "issues": issues, "metrics": metrics or {}}


def _point_on_segment(px: int, py: int, ax: int, ay: int, bx: int, by: int) -> bool:
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    return cross == 0 and min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def _inside_even_odd(px: int, py: int, loops: list[list[tuple[int, int]]]) -> bool | None:
    """Return strict interior membership; None means the point lies on an edge."""
    inside = False
    for loop in loops:
        for i, (ax, ay) in enumerate(loop):
            bx, by = loop[(i + 1) % len(loop)]
            if _point_on_segment(px, py, ax, ay, bx, by):
                return None
            if (ay > py) != (by > py):
                # Compare without division so integer coordinates remain exact.
                left = (px - ax) * (by - ay)
                right = (bx - ax) * (py - ay)
                crosses_right = left < right if by > ay else left > right
                if crosses_right:
                    inside = not inside
    return inside


def validate_map(level: NativeMap) -> dict[str, object]:
    """Return dimensioned structural findings for *level*.

    Each dimension includes ``status`` (``pass``, ``fail``, or ``partial``),
    ``error_count``, localized ``issues``, and optional numeric ``metrics``.
    Invalid but parseable levels are reported and remain available to ingestion.
    """
    sectors, walls, sprites = level.sectors, level.walls, level.sprites
    native_issues: list[dict[str, object]] = []
    geometry_issues: list[dict[str, object]] = [
        _issue("self_intersection_test_unsupported", "map", 0,
               "arbitrary polygon self-intersections are not tested")]
    portal_issues: list[dict[str, object]] = []
    clearance_issues: list[dict[str, object]] = []

    if level.version != 0x0700:
        native_issues.append(_issue("unsupported_version", "map", 0,
                                    f"validator expects native MAP v7, got 0x{level.version:04x}"))
    for name, records in (("sectors", sectors), ("walls", walls), ("sprites", sprites)):
        if len(records) > 32767:
            native_issues.append(_issue("record_count_out_of_range", "map", 0,
                                        f"{name} count {len(records)} exceeds the signed 16-bit wire limit"))
    expected_skies = 1 << level.sky_bits if 0 <= level.sky_bits <= 8 else None
    if expected_skies is None or len(level.sky_offsets) != expected_skies:
        native_issues.append(_issue("sky_offset_count_mismatch", "map", 0,
                                    f"sky_bits={level.sky_bits} requires {expected_skies or 'a valid'} sky offsets; "
                                    f"found {len(level.sky_offsets)}"))
    try:
        if len(level.raw_extra_header) != 128:
            raise ValueError("extra header length is not 128")
        xsprite_size, xwall_size, xsector_size = struct.unpack_from("<iii", level.raw_extra_header, 64)
        extra_sizes = {"sector": xsector_size, "wall": xwall_size, "sprite": xsprite_size}
        for kind, records in (("sector", sectors), ("wall", walls), ("sprite", sprites)):
            expected_size = extra_sizes[kind]
            if expected_size <= 0:
                native_issues.append(_issue("invalid_extension_size", "map", 0,
                                            f"{kind} extension size {expected_size} is not positive"))
                continue
            for index, record in enumerate(records):
                if record.extra > 0 and record.extra_data and len(record.extra_data) != expected_size:
                    native_issues.append(_issue("extension_payload_size_mismatch", kind, index,
                                                f"extension payload has {len(record.extra_data)} bytes; "
                                                f"header declares {expected_size}"))
    except (TypeError, struct.error, ValueError) as exc:
        native_issues.append(_issue("invalid_extra_header", "map", 0,
                                    f"cannot validate extended record sizes: {exc}"))
    if level.start_sector < 0 or level.start_sector >= len(sectors):
        native_issues.append(_issue("start_sector_out_of_range", "map", 0,
                                    f"start sector {level.start_sector} is outside 0..{len(sectors)-1}"))

    for index, sprite in enumerate(sprites):
        if not 0 <= sprite.sectnum < len(sectors):
            native_issues.append(_issue("sprite_sector_out_of_range", "sprite", index,
                                        f"sector reference {sprite.sectnum} is outside 0..{len(sectors)-1}"))
        if sprite.owner != -1 and not 0 <= sprite.owner < len(sprites):
            native_issues.append(_issue("sprite_owner_out_of_range", "sprite", index,
                                        f"owner reference {sprite.owner} is outside -1 or 0..{len(sprites)-1}"))

    # Establish sector ownership from each declared contiguous wall range.
    wall_owner: list[int | None] = [None] * len(walls)
    sector_loops: dict[int, list[list[tuple[int, int]]]] = defaultdict(list)
    sector_loop_areas: dict[int, list[int]] = defaultdict(list)
    for sector_index, sector in enumerate(sectors):
        start, count = sector.wallptr, sector.wallnum
        end = start + count
        if start < 0 or count < 0 or end > len(walls):
            geometry_issues.append(_issue("sector_wall_range_out_of_bounds", "sector", sector_index,
                                           f"wall range [{start}, {end}) is outside 0..{len(walls)}"))
            continue
        if count < 3:
            geometry_issues.append(_issue("sector_too_few_walls", "sector", sector_index,
                                           f"sector has {count} walls; a closed loop needs at least 3"))
        for wi in range(start, end):
            if wall_owner[wi] is not None:
                geometry_issues.append(_issue("wall_owned_by_multiple_sectors", "wall", wi,
                                               f"wall is claimed by sectors {wall_owner[wi]} and {sector_index}"))
            else:
                wall_owner[wi] = sector_index

        # A valid point2 mapping partitions the range into closed directed loops.
        targets: dict[int, int] = {}
        for wi in range(start, end):
            target = walls[wi].point2
            if not start <= target < end:
                geometry_issues.append(_issue("point2_out_of_sector", "wall", wi,
                                               f"point2 {target} is outside sector {sector_index}'s wall range [{start}, {end})"))
                continue
            targets[wi] = target
            wall, next_wall = walls[wi], walls[target]
            if (wall.x, wall.y) == (next_wall.x, next_wall.y):
                geometry_issues.append(_issue("zero_length_edge", "wall", wi,
                                               f"wall and point2 {target} have identical endpoints"))

        visited: set[int] = set()
        for first in range(start, end):
            if first in visited or first not in targets:
                continue
            path: list[int] = []
            path_positions: dict[int, int] = {}
            current = first
            while current in targets and current not in path_positions and current not in visited:
                path_positions[current] = len(path)
                path.append(current)
                current = targets[current]
            if current in path_positions:
                if path_positions[current] != 0:
                    geometry_issues.append(_issue("loop_repeats_before_closing", "wall", current,
                                                   f"point2 chain enters its own loop at wall position {path_positions[current]}"))
                elif len(path) < 3:
                    geometry_issues.append(_issue("loop_too_short", "wall", first,
                                                   f"closed loop has only {len(path)} walls"))
                else:
                    points = [(walls[wi].x, walls[wi].y) for wi in path]
                    area2 = sum(points[i][0] * points[(i + 1) % len(points)][1]
                                - points[(i + 1) % len(points)][0] * points[i][1]
                                for i in range(len(points)))
                    if area2 == 0:
                        geometry_issues.append(_issue("zero_area_loop", "wall", first,
                                                       "closed loop has zero signed area"))
                    sector_loops[sector_index].append(points)
                    sector_loop_areas[sector_index].append(area2)
            elif current in visited:
                geometry_issues.append(_issue("loops_share_wall_chain", "wall", current,
                                               "point2 chain joins a loop that was already visited"))
            else:
                geometry_issues.append(_issue("open_wall_chain", "wall", first,
                                               "point2 chain does not form a closed loop"))
            visited.update(path)

    for wall_index, owner in enumerate(wall_owner):
        if owner is None:
            geometry_issues.append(_issue("orphan_wall", "wall", wall_index,
                                           "wall is not owned by any sector wall range"))

    for sector_index, sector in enumerate(sectors):
        sloped = bool(sector.ceilingstat & 2 or sector.floorstat & 2)
        if not sloped and sector.ceilingz > sector.floorz:
            geometry_issues.append(_issue("ceiling_below_floor", "sector", sector_index,
                                           f"ceilingz {sector.ceilingz} is below floorz {sector.floorz}"))

    for wall_index, wall in enumerate(walls):
        if wall.nextwall == -1:
            if wall.nextsector != -1:
                portal_issues.append(_issue("portal_sector_without_wall", "wall", wall_index,
                                            f"nextsector is {wall.nextsector} but nextwall is -1"))
            continue
        if not 0 <= wall.nextwall < len(walls):
            portal_issues.append(_issue("nextwall_out_of_range", "wall", wall_index,
                                        f"nextwall {wall.nextwall} is outside 0..{len(walls)-1}"))
            continue
        if not 0 <= wall.nextsector < len(sectors):
            portal_issues.append(_issue("nextsector_out_of_range", "wall", wall_index,
                                        f"nextsector {wall.nextsector} is outside 0..{len(sectors)-1}"))
        other_index = wall.nextwall
        other = walls[other_index]
        if other.nextwall != wall_index:
            portal_issues.append(_issue("portal_not_reciprocal", "wall", wall_index,
                                        f"wall {other_index} points back to {other.nextwall}, not {wall_index}"))
        owner = wall_owner[wall_index]
        other_owner = wall_owner[other_index]
        if wall.nextsector != other_owner:
            portal_issues.append(_issue("portal_sector_mismatch", "wall", wall_index,
                                        f"nextsector {wall.nextsector} does not own nextwall {other_index}"))
        if other.nextsector != owner:
            portal_issues.append(_issue("portal_reverse_sector_mismatch", "wall", other_index,
                                        f"reverse nextsector {other.nextsector} does not own wall {wall_index}"))
        if 0 <= wall.point2 < len(walls) and 0 <= other.point2 < len(walls):
            a0 = (wall.x, wall.y)
            a1 = (walls[wall.point2].x, walls[wall.point2].y)
            b0 = (other.x, other.y)
            b1 = (walls[other.point2].x, walls[other.point2].y)
            if a0 != b1 or a1 != b0:
                portal_issues.append(_issue("portal_endpoints_not_reversed", "wall", wall_index,
                                            f"portal wall {other_index} does not reverse the endpoint pair"))

    # Clearance is a static flat-sector estimate at the native start position.
    clearance_partial = True
    clearance_issues.append(_issue("player_clearance_assumption", "map", 0,
                                   f"uses an assumed {PLAYER_CLEARANCE_Z_THRESHOLD}-unit vertical clearance; "
                                   "this is not a runtime collision guarantee"))
    if 0 <= level.start_sector < len(sectors):
        sector_index = level.start_sector
        sector = sectors[sector_index]
        loops = sector_loops.get(sector_index, [])
        if loops:
            inside = _inside_even_odd(level.start_x, level.start_y, loops)
            if inside is None:
                clearance_issues.append(_issue("start_on_boundary", "sector", sector_index,
                                               "player start lies on a sector boundary"))
            elif not inside:
                clearance_issues.append(_issue("start_outside_sector", "sector", sector_index,
                                               "player start is outside the start sector (including its holes)"))
        else:
            clearance_partial = True
            clearance_issues.append(_issue("start_sector_geometry_unavailable", "sector", sector_index,
                                           "cannot locate player start because this sector has no valid loops"))
        if sector.ceilingstat & 2 or sector.floorstat & 2:
            clearance_issues.append(_issue("sloped_clearance_unsupported", "sector", sector_index,
                                           "player clearance is not estimated for sloped ceiling or floor planes"))
        else:
            span = sector.floorz - sector.ceilingz
            if span < PLAYER_CLEARANCE_Z_THRESHOLD:
                clearance_issues.append(_issue("player_clearance_below_assumption", "sector", sector_index,
                                               f"flat vertical span is {span}; assumed minimum is "
                                               f"{PLAYER_CLEARANCE_Z_THRESHOLD}"))
            if not sector.ceilingz <= level.start_z <= sector.floorz:
                clearance_issues.append(_issue("start_z_outside_vertical_bounds", "sector", sector_index,
                                               f"start z {level.start_z} is outside flat bounds "
                                               f"[{sector.ceilingz}, {sector.floorz}]"))
    else:
        clearance_issues.append(_issue("start_sector_unavailable", "map", 0,
                                       "cannot estimate player clearance without a valid start sector"))

    dimensions: dict[str, dict[str, object]] = {
        "native": _dimension(native_issues),
        "geometry": _dimension(geometry_issues, partial=True,
                                metrics={"wall_count": len(walls),
                                         "closed_loop_count": sum(map(len, sector_loops.values())),
                                         "loop_signed_area2": {str(k): v for k, v in sector_loop_areas.items()}}),
        "portals": _dimension(portal_issues),
        "player_clearance": _dimension(clearance_issues, partial=clearance_partial,
                                        metrics={"vertical_threshold_z": PLAYER_CLEARANCE_Z_THRESHOLD,
                                                 "threshold_kind": "assumption"}),
        "mechanisms": _dimension([_issue("mechanisms_not_interpreted", "map", 0,
                                          "opaque XSECTOR/XWALL/XSPRITE records are not evaluated")], partial=True),
        "runtime": _dimension([_issue("runtime_not_simulated", "map", 0,
                                      "engine traversal and play testing are not performed")], partial=True),
        "visual": _dimension([_issue("visual_assets_not_resolved", "map", 0,
                                     "tile art and palettes are not resolved")], partial=True),
    }
    all_issues = [item for value in dimensions.values() for item in value["issues"]]
    error_count = sum(int(value["error_count"]) for value in dimensions.values())
    statuses = [str(value["status"]) for value in dimensions.values()]
    overall = "fail" if "fail" in statuses else "partial" if "partial" in statuses else "pass"
    return {**dimensions, "summary": {"status": overall, "error_count": error_count,
                                       "issue_count": len(all_issues),
                                       "dimensions": {key: value["status"] for key, value in dimensions.items()}}}
