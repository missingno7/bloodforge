"""Deterministic observations, explicitly separate from native records."""
from __future__ import annotations

from collections import Counter
import math

from .native import NativeMap
from .store import digest


def extract(level: NativeMap) -> dict:
    """Measure geometry and material context without assigning room semantics.

    Geometry signatures ignore translation, sector numbering, textures and
    sprites. Similarity buckets additionally quantize dimensions: these are
    conservative split groups / revision leads, never proof of equivalence.
    """
    min_x = min((w.x for w in level.walls), default=0)
    min_y = min((w.y for w in level.walls), default=0)
    min_z = min((s.floorz for s in level.sectors), default=0)
    edges = []
    sector_shapes = []
    observations = []
    degrees: Counter[int] = Counter()
    materials: Counter[tuple[str, int, int, int]] = Counter()
    portal_pairs: set[tuple[int, int]] = set()
    for index, sector in enumerate(level.sectors):
        ids = range(max(0, sector.wallptr), min(len(level.walls), sector.wallptr + sector.wallnum))
        points = []
        neighbors: set[int] = set()
        for wi in ids:
            wall = level.walls[wi]
            points.append((wall.x - min_x, wall.y - min_y))
            if 0 <= wall.nextsector < len(level.sectors) and 0 <= wall.nextwall < len(level.walls):
                opposite = level.walls[wall.nextwall]
                reciprocal = opposite.nextwall == wi and opposite.nextsector == index
                if reciprocal:
                    neighbors.add(wall.nextsector)
                    portal_pairs.add(tuple(sorted((index, wall.nextsector))))
                # Structural adjacency remains distinct from a traversal claim.
                if wi < wall.nextwall:
                    other = level.sectors[wall.nextsector]
                    observations.append({"kind": "portal", "object_id": str(wi), "value": {
                        "source_sector": index, "destination_sector": wall.nextsector,
                        "walls": [wi, wall.nextwall], "reciprocal": reciprocal,
                        "blocking_flags": [wall.cstat & 1, opposite.cstat & 1],
                        "floor_delta_z": other.floorz - sector.floorz,
                        "flat_vertical_opening_z": min(sector.floorz, other.floorz) - max(sector.ceilingz, other.ceilingz),
                        "sloped": bool((sector.floorstat | sector.ceilingstat | other.floorstat | other.ceilingstat) & 2),
                        "claim": "native-adjacency-only"}})
            if 0 <= wall.point2 < len(level.walls):
                end = level.walls[wall.point2]
                edges.append((wall.x - min_x, wall.y - min_y, end.x - min_x, end.y - min_y,
                              wall.nextsector >= 0))
            materials[("wall", wall.picnum, wall.pal, wall.shade)] += 1
        degrees[len(neighbors)] += 1
        sector_shapes.append((sorted(points), sector.floorz - min_z, sector.ceilingz - min_z,
                              sector.floorheinum, sector.ceilingheinum,
                              sector.floorstat & 2, sector.ceilingstat & 2, len(neighbors)))
        materials[("floor", sector.floorpicnum, sector.floorpal, sector.floorshade)] += 1
        materials[("ceiling", sector.ceilingpicnum, sector.ceilingpal, sector.ceilingshade)] += 1
    geometry_signature = digest({"sectors": sorted(sector_shapes), "edges": sorted(edges)})
    # Coarse, rotation-independent histogram buckets. False positives are
    # acceptable for conservative held-out grouping but must be labelled.
    height_bins = Counter(int(round((s.floorz - s.ceilingz) / 1024)) for s in level.sectors)
    extent = sorted((max((w.x - min_x for w in level.walls), default=0),
                     max((w.y - min_y for w in level.walls), default=0)))
    similarity_signature = digest({"counts": [len(level.sectors), len(level.walls)],
                                   "degrees": sorted(degrees.items()),
                                   "heights": sorted(height_bins.items()),
                                   "extent": [int(round(x / 256)) for x in extent]})
    graph = [set() for _ in level.sectors]
    for a, b in portal_pairs:
        graph[a].add(b)
        graph[b].add(a)
    remaining = set(range(len(graph)))
    components = []
    while remaining:
        stack = [min(remaining)]
        remaining.remove(stack[0])
        size = 0
        while stack:
            node = stack.pop()
            size += 1
            unseen = graph[node] & remaining
            remaining.difference_update(unseen)
            stack.extend(sorted(unseen))
        components.append(size)
    observations.append({"kind": "topology", "object_id": "map", "value": {
        "reciprocal_sector_pairs": len(portal_pairs), "component_sizes": sorted(components, reverse=True),
        "degree_histogram": sorted(degrees.items()), "claim": "adjacency-not-playability"}})
    observations.append({"kind": "materials", "object_id": "map", "value": {
        "surface_context_counts": [[*key, count] for key, count in sorted(materials.items())],
        "asset_dimensions": "not_extracted", "rendered_visibility": "not_observed"}})
    observations.append({"kind": "extensions", "object_id": "map", "value": {
        "sectors": sum(bool(x.extra_data) for x in level.sectors),
        "walls": sum(bool(x.extra_data) for x in level.walls),
        "sprites": sum(bool(x.extra_data) for x in level.sprites),
        "claim": "opaque-payload-preserved-no-behavior-inferred"}})
    return {"geometry_signature": geometry_signature, "similarity_signature": similarity_signature,
            "observations": observations}
