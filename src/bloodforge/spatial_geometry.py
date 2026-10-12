"""Strict Shapely-backed XY geometry and static camera-pose checks.

This module interprets native wall ``point2`` loops only. It does not repair
invalid rings, infer portals, or make engine/runtime navigation claims.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any
from shapely.geometry.base import BaseGeometry

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from .native import NativeMap
from .observe import CameraPose


class SpatialGeometryError(ValueError):
    """Native sector loops cannot be represented as a valid 2D area."""


def _sector(level: NativeMap, index: int):
    if not isinstance(level, NativeMap):
        raise SpatialGeometryError("level must be a NativeMap")
    if isinstance(index, bool) or not isinstance(index, int):
        raise SpatialGeometryError("sector index must be an integer")
    if not 0 <= index < len(level.sectors):
        raise SpatialGeometryError(f"sector index {index} is out of range")
    return level.sectors[index]


def _sector_range(level: NativeMap, index: int) -> tuple[int, int]:
    """Validate ownership ranges, then require a polygon-sized target range.

    A disjoint zero/two-wall range is insufficient for that sector's polygon,
    but it does not make ownership of the target sector's walls ambiguous.
    """
    sector = _sector(level, index)
    start, count = sector.wallptr, sector.wallnum
    if (isinstance(start, bool) or isinstance(count, bool) or
            not isinstance(start, int) or not isinstance(count, int)):
        raise SpatialGeometryError(f"sector {index} wall range is not integral")
    end = start + count
    if start < 0 or count < 3 or end > len(level.walls):
        raise SpatialGeometryError(f"sector {index} wall range [{start}, {end}) is invalid")
    for other_index, other in enumerate(level.sectors):
        other_start, other_count = other.wallptr, other.wallnum
        if (isinstance(other_start, bool) or isinstance(other_count, bool) or
                not isinstance(other_start, int) or not isinstance(other_count, int)):
            raise SpatialGeometryError(f"sector {other_index} wall range is not integral")
        other_end = other_start + other_count
        if other_start < 0 or other_count < 0 or other_end > len(level.walls):
            raise SpatialGeometryError(
                f"sector {other_index} wall range [{other_start}, {other_end}) is invalid"
            )
        if other_index != index and max(start, other_start) < min(end, other_end):
            raise SpatialGeometryError(
                f"sector {index} wall range overlaps sector {other_index}"
            )
    return start, end


def _sector_bbox(level: NativeMap, index: int) -> tuple[int, int, int, int]:
    """Return a safe wall-coordinate bbox for excluding impossible sectors."""
    start, end = _sector_range(level, index)
    coordinates: list[tuple[int, int]] = []
    for wi in range(start, end):
        wall = level.walls[wi]
        if any(isinstance(value, bool) or not isinstance(value, int)
               for value in (wall.x, wall.y)):
            raise SpatialGeometryError(f"wall {wi} has non-integer XY coordinates")
        coordinates.append((wall.x, wall.y))
    xs, ys = zip(*coordinates)
    return min(xs), min(ys), max(xs), max(ys)


def _loops(level: NativeMap, index: int) -> list[list[tuple[int, int]]]:
    start, end = _sector_range(level, index)
    count = end - start
    owned = set(range(start, end))
    targets: dict[int, int] = {}
    for wi in range(start, end):
        wall = level.walls[wi]
        if isinstance(wall.point2, bool) or not isinstance(wall.point2, int) or wall.point2 not in owned:
            raise SpatialGeometryError(f"wall {wi} point2={wall.point2!r} escapes sector {index}")
        if wi == wall.point2:
            raise SpatialGeometryError(f"wall {wi} has a zero-length point2 loop edge")
        targets[wi] = wall.point2
    indegrees = Counter(targets.values())
    bad = [target for target in owned if indegrees[target] != 1]
    if bad:
        raise SpatialGeometryError(f"sector {index} point2 mapping is open or shared at wall {min(bad)}")

    loops: list[list[tuple[int, int]]] = []
    visited: set[int] = set()
    for origin in range(start, end):
        if origin in visited:
            continue
        loop: list[tuple[int, int]] = []
        current = origin
        while True:
            if current in visited:
                if current != origin:
                    raise SpatialGeometryError(
                        f"sector {index} point2 chain joins an earlier loop at wall {current}"
                    )
                break
            visited.add(current)
            wall = level.walls[current]
            x, y = wall.x, wall.y
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (x, y)):
                raise SpatialGeometryError(f"wall {current} has non-integer XY coordinates")
            loop.append((x, y))
            current = targets[current]
        if len(loop) < 3:
            raise SpatialGeometryError(f"sector {index} loop beginning at wall {origin} has fewer than 3 edges")
        if len(set(loop)) != len(loop):
            raise SpatialGeometryError(f"sector {index} loop beginning at wall {origin} repeats a vertex")
        loops.append(loop)
    if len(visited) != count:
        raise SpatialGeometryError(f"sector {index} point2 mapping does not partition its wall range")
    return loops


def sector_geometry(level: NativeMap, index: int) -> BaseGeometry:
    """Return one sector's even-odd fill as Polygon or MultiPolygon.

    Every native point2 chain must form a simple closed loop wholly inside the
    sector wall range. Distinct loops may nest, but may not cross or touch.
    Invalid input raises :class:`SpatialGeometryError`; there is no repair.
    """
    loops = _loops(level, index)
    polygons: list[Polygon] = []
    for loop_index, loop in enumerate(loops):
        polygon = Polygon(loop)
        if polygon.is_empty or polygon.area == 0:
            raise SpatialGeometryError(f"sector {index} loop {loop_index} has zero area")
        if not polygon.exterior.is_simple or not polygon.is_valid:
            raise SpatialGeometryError(f"sector {index} loop {loop_index} is self-intersecting or invalid")
        polygons.append(polygon)

    boundaries = [polygon.boundary for polygon in polygons]
    if len(boundaries) > 1:
        tree = STRtree(boundaries)
        for first, boundary in enumerate(boundaries):
            for raw_second in sorted(int(item) for item in tree.query(boundary)):
                second = raw_second
                if second > first and boundary.intersects(boundaries[second]):
                    raise SpatialGeometryError(
                        f"sector {index} loops {first} and {second} cross or touch"
                    )

    geometry = polygons[0]
    for polygon in polygons[1:]:
        geometry = geometry.symmetric_difference(polygon)
    if geometry.is_empty or not geometry.is_valid:
        raise SpatialGeometryError(f"sector {index} even-odd loop fill is empty or invalid")
    return geometry


def _pose_values(pose: CameraPose | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(pose, CameraPose):
        return {"id": pose.id, "x": pose.x, "y": pose.y, "z": pose.z,
                "angle": pose.angle, "horiz": pose.horiz,
                "sector": pose.sector, "screenshot": pose.screenshot}
    if isinstance(pose, Mapping):
        return dict(pose)
    raise TypeError("pose must be a CameraPose or mapping")


def _integer_xyz(values: Mapping[str, Any]) -> bool:
    return all(not isinstance(values.get(name), bool) and isinstance(values.get(name), int)
               and -(1 << 31) <= values[name] <= (1 << 31) - 1
               for name in ("x", "y", "z"))


def validate_pose(level: NativeMap, pose: CameraPose | Mapping[str, Any]) -> dict[str, Any]:
    """Check strict XY and flat vertical containment for a single static pose.

    ``sector`` is honored as an explicit hint. Without it, exactly one sector
    must strictly contain XY. Sky/parallax planes retain their native flat Z
    envelope, but the result labels that limitation. No runtime assertion is
    made.
    """
    if not isinstance(level, NativeMap):
        return {"status": "invalid", "sector": None, "reason": "level must be a NativeMap"}
    try:
        values = _pose_values(pose)
    except TypeError as exc:
        return {"status": "invalid", "sector": None, "reason": str(exc)}
    hint = values.get("sector")
    if hint is not None and (isinstance(hint, bool) or not isinstance(hint, int)):
        return {"status": "invalid", "sector": None, "reason": "sector hint must be an integer"}
    if not _integer_xyz(values):
        return {"status": "invalid", "sector": hint, "reason": "x, y, and z must be signed 32-bit integers"}
    x, y, z = values["x"], values["y"], values["z"]
    point = Point(x, y)

    candidates: list[int] = []
    sectors_to_check = [hint] if hint is not None else list(range(len(level.sectors)))
    geometry_failures: list[tuple[int, str]] = []
    for index in sectors_to_check:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(level.sectors):
            return {"status": "invalid", "sector": index, "reason": "sector hint is out of range"}
        sector = level.sectors[index]
        sloped = bool((sector.ceilingstat & 2 and sector.ceilingheinum != 0) or
                      (sector.floorstat & 2 and sector.floorheinum != 0))
        if hint is None:
            try:
                minx, miny, maxx, maxy = _sector_bbox(level, index)
            except SpatialGeometryError as exc:
                geometry_failures.append((index, str(exc)))
                continue
            # BBox boundaries are retained for exact polygon checking because
            # a point there may be an edge, vertex or interior of a concavity.
            if x < minx or x > maxx or y < miny or y > maxy:
                continue
        try:
            geometry = sector_geometry(level, index)
        except SpatialGeometryError as exc:
            geometry_failures.append((index, str(exc)))
            continue
        if geometry.contains(point):
            if sloped:
                return {"status": "unsupported", "sector": index,
                        "reason": "XY is inside a sector with sloped ceiling/floor; vertical containment is unsupported"}
            candidates.append(index)
    if hint is not None:
        if not candidates:
            if geometry_failures:
                return {"status": "unsupported", "sector": hint,
                        "reason": f"sector geometry unavailable: {geometry_failures[0][1]}"}
            return {"status": "invalid", "sector": hint,
                    "reason": "XY is outside sector interior or on a boundary/hole"}
        selected = hint
    else:
        if geometry_failures:
            failed_index, reason = geometry_failures[0]
            return {"status": "unsupported", "sector": failed_index,
                    "reason": f"cannot uniquely resolve against malformed sector geometry: {reason}"}
        if not candidates:
            return {"status": "invalid", "sector": None,
                    "reason": "XY is outside all sector interiors or on a boundary/hole"}
        if len(candidates) > 1:
            vertically_containing = [candidate for candidate in candidates
                                     if level.sectors[candidate].ceilingz < z < level.sectors[candidate].floorz]
            if len(vertically_containing) == 1:
                selected = vertically_containing[0]
            elif len(vertically_containing) > 1:
                return {"status": "unsupported", "sector": None,
                        "reason": f"XYZ is ambiguously inside sectors {vertically_containing}"}
            else:
                return {"status": "invalid", "sector": None,
                        "reason": "z is outside every flat sector containing this XY point"}
        else:
            selected = candidates[0]

    sector = level.sectors[selected]
    if sector.ceilingz >= sector.floorz:
        return {"status": "invalid", "sector": selected,
                "reason": "flat sector ceiling_z must be strictly less than floor_z"}
    if not sector.ceilingz < z < sector.floorz:
        return {"status": "invalid", "sector": selected,
                "reason": f"z must be strictly between ceiling {sector.ceilingz} and floor {sector.floorz}"}
    sky = bool((sector.ceilingstat | sector.floorstat) & 1)
    zero_heinum_flag = bool((sector.ceilingstat & 2 and sector.ceilingheinum == 0) or
                            (sector.floorstat & 2 and sector.floorheinum == 0))
    reason = "inside flat sky sector native Z envelope" if sky else "inside flat sector"
    if zero_heinum_flag:
        reason += "; zero-heinum slope flag uses base Z"
    return {"status": "valid", "sector": selected,
            "reason": reason,
            "sky": sky, "validation_scope": "static geometry only; no runtime claim"}


def _round_int(value: float) -> int:
    # Explicit ties-to-even is deterministic and agrees with Python's round.
    return int(round(value))


def _shortest_angle(start: int, end: int, t: float) -> int:
    delta = (end - start + 1024) % 2048 - 1024
    return _round_int(start + delta * t) % 2048


def sample_route(level: NativeMap, waypoints: list[dict[str, Any]],
                 samples_per_segment: int = 3) -> list[dict[str, Any]]:
    """Linearly sample integer camera poses and validate every sample.

    ``samples_per_segment`` is the number of equal intervals per waypoint
    pair. All endpoints are included exactly once. This validates sampled
    poses only; it does not assert segment continuity or portal navigability.
    Raises ``SpatialGeometryError`` when any sample is invalid/unsupported.
    """
    if not isinstance(waypoints, list) or not waypoints:
        raise SpatialGeometryError("waypoints must be a non-empty list")
    if len(waypoints) > 1000:
        raise SpatialGeometryError("waypoints is limited to 1000 records")
    if (isinstance(samples_per_segment, bool) or not isinstance(samples_per_segment, int)
            or not 1 <= samples_per_segment <= 10000):
        raise SpatialGeometryError("samples_per_segment must be an integer in 1..10000")
    if len(waypoints) > 1 and (len(waypoints) - 1) * samples_per_segment + 1 > 10000:
        raise SpatialGeometryError("sampled route is limited to 10000 poses")
    values: list[dict[str, Any]] = []
    for index, item in enumerate(waypoints):
        if not isinstance(item, Mapping):
            raise SpatialGeometryError(f"waypoint {index} must be a mapping")
        if not _integer_xyz(item):
            raise SpatialGeometryError(f"waypoint {index} x/y/z must be signed 32-bit integers")
        angle = item.get("angle", 0)
        if isinstance(angle, bool) or not isinstance(angle, int) or not 0 <= angle <= 2047:
            raise SpatialGeometryError(f"waypoint {index} angle must be an integer in 0..2047")
        sector = item.get("sector")
        if sector is not None and (isinstance(sector, bool) or not isinstance(sector, int)):
            raise SpatialGeometryError(f"waypoint {index} sector must be an integer or absent")
        horiz = item.get("horiz", 100)
        if isinstance(horiz, bool) or not isinstance(horiz, int):
            raise SpatialGeometryError(f"waypoint {index} horiz must be an integer")
        values.append({"x": item["x"], "y": item["y"], "z": item["z"],
                       "angle": angle, "sector": sector, "horiz": horiz})

    if len(values) == 1:
        pairs = [(values[0], None, 0)]
    else:
        pairs = []
        for segment in range(len(values) - 1):
            left, right = values[segment], values[segment + 1]
            for step in range(samples_per_segment + 1):
                if segment and step == 0:
                    continue
                t = step / samples_per_segment
                is_endpoint = step == samples_per_segment
                if step == 0:
                    point = dict(left)
                elif is_endpoint:
                    point = dict(right)
                else:
                    point = {name: _round_int(left[name] + (right[name] - left[name]) * t)
                             for name in ("x", "y", "z")}
                    point["angle"] = _shortest_angle(left["angle"], right["angle"], t)
                    point["horiz"] = _round_int(left["horiz"] + (right["horiz"] - left["horiz"]) * t)
                    point["sector"] = None
                pairs.append((point, right if is_endpoint else None, segment))

    output: list[dict[str, Any]] = []
    for sequence, (point, _endpoint, segment) in enumerate(pairs):
        pose_values = dict(point)
        pose_values["id"] = f"route_{sequence:04d}"
        result = validate_pose(level, pose_values)
        if result["status"] != "valid":
            raise SpatialGeometryError(
                f"route sample {sequence} on segment {segment} is {result['status']}: {result['reason']}"
            )
        # Preserve explicit waypoint-sector hints and use resolved sectors on
        # samples between waypoints. Never interpolate sector indexes.
        sector = point.get("sector")
        if sector is None:
            sector = result["sector"]
        record = {"id": pose_values["id"], "x": point["x"], "y": point["y"],
                  "z": point["z"], "angle": point["angle"], "horiz": point["horiz"],
                  "sector": sector, "screenshot": False}
        output.append(record)
    return output
