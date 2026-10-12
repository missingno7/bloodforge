"""Independent structural comparison for reconstructed stair fragments.

This module intentionally does not depend on pattern discovery or the scene
compiler. It inspects the native records present in each map directly.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .native import NativeMap


def _sector_polygon(level: NativeMap, sector_index: int) -> tuple[list[tuple[int, int]] | None, str | None]:
    if not 0 <= sector_index < len(level.sectors):
        return None, "sector index is out of bounds"
    sector = level.sectors[sector_index]
    start, count = sector.wallptr, sector.wallnum
    if count < 3 or start < 0 or start + count > len(level.walls):
        return None, "sector wall range is invalid"
    wall_ids = set(range(start, start + count))
    current = start
    visited: set[int] = set()
    points: list[tuple[int, int]] = []
    for _ in range(count):
        if current not in wall_ids or current in visited:
            return None, "point2 links do not form one sector cycle"
        wall = level.walls[current]
        if wall.point2 not in wall_ids:
            return None, "point2 leaves the sector wall range"
        visited.add(current)
        points.append((wall.x, wall.y))
        current = wall.point2
    if current != start or len(visited) != count:
        return None, "point2 links do not form one sector cycle"
    if len(set(points)) != len(points):
        return None, "sector polygon has repeated vertices"
    return points, None


def _canonical_cycle(points: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Canonicalize polygon start and winding without discarding edge order."""
    variants = []
    for ring in (points, list(reversed(points))):
        for index in range(len(ring)):
            variants.append(tuple(ring[index:] + ring[:index]))
    return min(variants)


def _rectangular_bounds(points: list[tuple[int, int]] | None) -> dict[str, Any]:
    if points is None or len(points) != 4 or len(set(points)) != 4:
        return {"is_rectangle": False, "bounds": None}
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    expected = {(left, top), (right, top), (right, bottom), (left, bottom)}
    axis_edges = all(x0 == x1 or y0 == y1 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))
    valid = left < right and top < bottom and set(points) == expected and axis_edges
    bounds = {
        "min_x": left, "min_y": top, "max_x": right, "max_y": bottom,
        "width": right - left, "height": bottom - top,
    }
    return {"is_rectangle": valid, "bounds": bounds}


def _internal_portals(level: NativeMap, indices: list[int]) -> dict[str, Any]:
    local = {sector: ordinal for ordinal, sector in enumerate(indices)}
    directed: list[tuple[int, int]] = []
    reciprocal: set[tuple[int, int]] = set()
    invalid: list[dict[str, Any]] = []
    external_count = 0
    for sector_index in indices:
        if not 0 <= sector_index < len(level.sectors):
            continue
        sector = level.sectors[sector_index]
        start, end = sector.wallptr, sector.wallptr + sector.wallnum
        if start < 0 or end > len(level.walls):
            invalid.append({"sector_index": sector_index, "reason": "invalid wall range"})
            continue
        source_local = local[sector_index]
        for wall_index in range(start, end):
            wall = level.walls[wall_index]
            destination = wall.nextsector
            if destination < 0:
                continue
            if destination not in local:
                external_count += 1
                continue
            target_local = local[destination]
            directed.append((source_local, target_local))
            if not 0 <= wall.nextwall < len(level.walls):
                invalid.append({"wall_index": wall_index, "reason": "nextwall out of bounds"})
                continue
            back = level.walls[wall.nextwall]
            if back.nextwall != wall_index or back.nextsector != sector_index:
                invalid.append({"wall_index": wall_index, "reason": "portal references are not reciprocal"})
                continue
            if not 0 <= wall.point2 < len(level.walls) or not 0 <= back.point2 < len(level.walls):
                invalid.append({"wall_index": wall_index, "reason": "portal point2 out of bounds"})
                continue
            endpoint = level.walls[wall.point2]
            back_endpoint = level.walls[back.point2]
            if (wall.x, wall.y) != (back_endpoint.x, back_endpoint.y) or (endpoint.x, endpoint.y) != (back.x, back.y):
                invalid.append({"wall_index": wall_index, "reason": "portal edge endpoints do not reverse exactly"})
                continue
            reciprocal.add((source_local, target_local))
    directed.sort()
    reciprocal_sorted = sorted(reciprocal)
    return {
        "directed_links": [list(edge) for edge in directed],
        "reciprocal_links": [list(edge) for edge in reciprocal_sorted],
        "undirected_edges": [list(edge) for edge in sorted({tuple(sorted(edge)) for edge in directed})],
        "reciprocal_consistent": not invalid and all((b, a) in reciprocal for a, b in directed),
        "invalid_links": invalid,
        "external_portal_count": external_count,
    }


def _wall_material(source: NativeMap, source_indices: list[int], reconstructed: NativeMap,
                   reconstructed_indices: list[int]) -> dict[str, Any]:
    source_tiles: list[int] = []
    reconstructed_tiles: list[int] = []
    source_edges: dict[tuple[int, tuple[tuple[int, int], tuple[int, int]]], int] = {}
    reconstructed_edges: dict[tuple[int, tuple[tuple[int, int], tuple[int, int]]], int] = {}

    def gather(level_map: NativeMap, indices: list[int], tiles: list[int], edges: dict) -> None:
        for ordinal, sector_index in enumerate(indices):
            if not 0 <= sector_index < len(level_map.sectors):
                continue
            sector = level_map.sectors[sector_index]
            start, end = sector.wallptr, sector.wallptr + sector.wallnum
            if start < 0 or end > len(level_map.walls):
                continue
            for wall_index in range(start, end):
                wall = level_map.walls[wall_index]
                if not 0 <= wall.point2 < len(level_map.walls):
                    continue
                endpoint = level_map.walls[wall.point2]
                key = tuple(sorted(((wall.x, wall.y), (endpoint.x, endpoint.y))))
                tiles.append(wall.picnum)
                # Include step ordinal so the two sides of an internal portal
                # remain separate material observations.
                edges[(ordinal, key)] = wall.picnum

    gather(source, source_indices, source_tiles, source_edges)
    gather(reconstructed, reconstructed_indices, reconstructed_tiles, reconstructed_edges)
    common_edges = set(source_edges) & set(reconstructed_edges)
    matched = sum(source_edges[edge] == reconstructed_edges[edge] for edge in common_edges)
    return {
        "scope": "primary wall picnum only; palette, shade, panning, repeats, overpicnum, and rendered appearance are not compared",
        "partial": True,
        "source_tile_histogram": {str(k): v for k, v in sorted(Counter(source_tiles).items())},
        "reconstructed_tile_histogram": {str(k): v for k, v in sorted(Counter(reconstructed_tiles).items())},
        "source_uniform": len(set(source_tiles)) <= 1,
        "reconstructed_uniform": len(set(reconstructed_tiles)) <= 1,
        "comparable_edge_count": len(common_edges),
        "exact_tile_matches": matched,
        "exact_fraction": matched / len(common_edges) if common_edges else None,
    }


def compare_stair(source: NativeMap, source_sector_indices: list[int],
                  reconstructed: NativeMap) -> dict[str, Any]:
    """Compare a selected source stair run against a reconstructed native MAP.

    ``source_sector_indices`` defines stair order. The reconstructed fragment
    is compared by ordinal sector index (step 0 is reconstructed sector 0,
    etc.), matching the current compiler's standalone fragment output.
    Source portals to sectors outside the selected run are treated as fragment
    boundaries and reported as omitted external connections.
    """
    if not isinstance(source_sector_indices, list):
        raise TypeError("source_sector_indices must be a list of sector indices")
    source_indices = [int(index) for index in source_sector_indices]
    reconstructed_indices = list(range(len(source_indices)))
    comparisons: list[dict[str, Any]] = []
    floor_errors: list[int] = []
    ceiling_errors: list[int] = []
    polygon_matches = 0
    floor_tile_matches = 0
    ceiling_tile_matches = 0
    valid_comparable_steps = 0
    dimension_matches = 0
    xy_errors: list[int] = []

    for step, source_index in enumerate(source_indices):
        reconstructed_index = step
        source_points, source_error = _sector_polygon(source, source_index)
        reconstructed_points, reconstructed_error = _sector_polygon(reconstructed, reconstructed_index)
        source_rect = _rectangular_bounds(source_points)
        reconstructed_rect = _rectangular_bounds(reconstructed_points)
        source_canonical = _canonical_cycle(source_points) if source_points is not None else None
        reconstructed_canonical = _canonical_cycle(reconstructed_points) if reconstructed_points is not None else None
        polygon_exact = source_canonical is not None and source_canonical == reconstructed_canonical
        if polygon_exact:
            polygon_matches += 1
        xy_error = None
        if source_canonical is not None and reconstructed_canonical is not None and len(source_canonical) == len(reconstructed_canonical):
            xy_error = max((max(abs(x0 - x1), abs(y0 - y1))
                            for (x0, y0), (x1, y1) in zip(source_canonical, reconstructed_canonical)), default=0)
            xy_errors.append(xy_error)
        source_bounds = source_rect["bounds"]
        reconstructed_bounds = reconstructed_rect["bounds"]
        dimensions_match = bool(
            source_rect["is_rectangle"] and reconstructed_rect["is_rectangle"]
            and source_bounds["width"] == reconstructed_bounds["width"]
            and source_bounds["height"] == reconstructed_bounds["height"]
        )
        dimension_matches += dimensions_match

        source_sector = source.sectors[source_index] if 0 <= source_index < len(source.sectors) else None
        reconstructed_sector = reconstructed.sectors[reconstructed_index] if reconstructed_index < len(reconstructed.sectors) else None
        floor_error = abs(source_sector.floorz - reconstructed_sector.floorz) if source_sector and reconstructed_sector else None
        ceiling_error = abs(source_sector.ceilingz - reconstructed_sector.ceilingz) if source_sector and reconstructed_sector else None
        if floor_error is not None:
            floor_errors.append(floor_error)
            ceiling_errors.append(ceiling_error or 0)
            valid_comparable_steps += 1
            floor_tile_matches += source_sector.floorpicnum == reconstructed_sector.floorpicnum
            ceiling_tile_matches += source_sector.ceilingpicnum == reconstructed_sector.ceilingpicnum
        comparisons.append({
            "step_index": step,
            "source_sector_index": source_index,
            "reconstructed_sector_index": reconstructed_index,
            "source_polygon": [list(point) for point in source_canonical] if source_canonical is not None else None,
            "reconstructed_polygon": [list(point) for point in reconstructed_canonical] if reconstructed_canonical is not None else None,
            "source_polygon_error": source_error,
            "reconstructed_polygon_error": reconstructed_error,
            "source_rectangle": source_rect,
            "reconstructed_rectangle": reconstructed_rect,
            "polygon_exact_match": polygon_exact,
            "max_xy_coordinate_error": xy_error,
            "dimensions_match": dimensions_match,
            "floor_z_error": floor_error,
            "ceiling_z_error": ceiling_error,
            "floor_tile_match": (source_sector.floorpicnum == reconstructed_sector.floorpicnum) if source_sector and reconstructed_sector else None,
            "ceiling_tile_match": (source_sector.ceilingpicnum == reconstructed_sector.ceilingpicnum) if source_sector and reconstructed_sector else None,
        })

    source_topology = _internal_portals(source, source_indices)
    reconstructed_topology = _internal_portals(reconstructed, reconstructed_indices)
    graph_matches = source_topology["undirected_edges"] == reconstructed_topology["undirected_edges"]
    topology_matches = (graph_matches and source_topology["reciprocal_consistent"]
                       and reconstructed_topology["reciprocal_consistent"]
                       and source_topology["directed_links"] == reconstructed_topology["directed_links"])
    external_lost = source_topology["external_portal_count"]
    count_matches = len(source_indices) == len(reconstructed.sectors)
    floor_fraction = floor_tile_matches / valid_comparable_steps if valid_comparable_steps else None
    ceiling_fraction = ceiling_tile_matches / valid_comparable_steps if valid_comparable_steps else None
    total = len(source_indices)
    return {
        "source_sector_indices": source_indices,
        "reconstructed_sector_indices": reconstructed_indices,
        "sector_count_matches": count_matches,
        "sector_comparisons": comparisons,
        "geometry": {
            "exact_polygon_matches": polygon_matches,
            "polygon_exact_fraction": polygon_matches / total if total else None,
            "exact_dimension_matches": dimension_matches,
            "dimension_exact_fraction": dimension_matches / total if total else None,
            "max_xy_coordinate_error": max(xy_errors) if xy_errors else None,
            "max_floor_z_error": max(floor_errors) if floor_errors else None,
            "max_ceiling_z_error": max(ceiling_errors) if ceiling_errors else None,
            "all_reconstructed_sectors_rectangular": all(item["reconstructed_rectangle"]["is_rectangle"] for item in comparisons),
        },
        "topology": {
            "internal_graph_matches": graph_matches,
            "internal_topology_matches": topology_matches,
            "source": source_topology,
            "reconstructed": reconstructed_topology,
            "source_external_connections_excluded": True,
            "source_external_connections_lost": external_lost,
            "note": "Source links outside the selected stair sector set are fragment boundaries and are not required in the reconstruction.",
        },
        "floor_tile_exact_fraction": floor_fraction,
        "ceiling_tile_exact_fraction": ceiling_fraction,
        "wall_material": _wall_material(source, source_indices, reconstructed, reconstructed_indices),
        "runtime_verified": False,
        "visual_verified": False,
        "claims": ["Native structural comparison only; no renderer, runtime traversal, or playability claim."],
    }
