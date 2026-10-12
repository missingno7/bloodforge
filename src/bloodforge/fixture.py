"""Source-held-fixed native fixtures for local reconstruction experiments.

This is benchmark apparatus, not a second authoring model. The compiler must
already have produced the same ordered footprint and internal connections.
Only native indices and links to the fixed outside context are restored here;
source surface fields never replace generated surface fields.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace

from .native import NativeMap, encode_map


@dataclass(frozen=True)
class ContextFixture:
    native_map: NativeMap
    correspondence: tuple[dict, ...]
    integrity: dict


def _cycle(level: NativeMap, sector_index: int) -> list[int]:
    sector = level.sectors[sector_index]
    allowed = set(range(sector.wallptr, sector.wallptr + sector.wallnum))
    if sector.wallnum != 4 or min(allowed) < 0 or max(allowed) >= len(level.walls):
        raise ValueError("fixture requires four valid walls per selected sector")
    current, ordered = sector.wallptr, []
    while current not in ordered:
        if current not in allowed:
            raise ValueError("fixture point2 leaves its sector")
        ordered.append(current)
        current = level.walls[current].point2
    if current != sector.wallptr or set(ordered) != allowed:
        raise ValueError("fixture requires one complete wall loop")
    return ordered


def graft_stair(source: NativeMap, source_sectors: list[int], fragment: NativeMap) -> ContextFixture:
    """Replace selected static stair records; retain every outside record.

    Selected source sector IDs are evidence addresses, never authoring IDs.
    Each generated ordinal maps to one source sector. Footprint, height, first
    wall anchor/winding and internal topology must agree before any graft.
    Source tags/extensions on the patch are rejected, not silently discarded.
    The source start, all sprites and global metadata remain fixed.
    """
    if not source_sectors or any(type(i) is not int or not 0 <= i < len(source.sectors) for i in source_sectors):
        raise ValueError("fixture requires valid source sector indices")
    if len(set(source_sectors)) != len(source_sectors):
        raise ValueError("fixture source sectors must be unique")
    if len(fragment.sectors) != len(source_sectors) or len(fragment.walls) != 4 * len(source_sectors) or fragment.sprites:
        raise ValueError("fixture fragment must contain only the selected stair sectors and walls")
    sector_map = dict(enumerate(source_sectors))
    source_to_generated = {target: generated for generated, target in sector_map.items()}
    wall_map: dict[int, int] = {}
    rows: list[dict] = []
    for ordinal, target in sector_map.items():
        old, new = source.sectors[target], fragment.sectors[ordinal]
        for sector in (old, new):
            if sector.extra > 0 or sector.extra_data or sector.lotag or sector.hitag:
                raise ValueError("fixture patch sectors must have no native mechanisms")
            if (sector.floorstat | sector.ceilingstat) & 2:
                raise ValueError("fixture patch requires flat planes")
        if (old.floorz, old.ceilingz) != (new.floorz, new.ceilingz):
            raise ValueError("fixture patch heights differ from source")
        old_cycle, new_cycle = _cycle(source, target), _cycle(fragment, ordinal)
        old_points = [(source.walls[i].x, source.walls[i].y) for i in old_cycle]
        new_points = [(fragment.walls[i].x, fragment.walls[i].y) for i in new_cycle]
        if old_points != new_points:
            raise ValueError("fixture ordered footprint/first-wall anchor differs from source")
        for new_index, old_index in zip(new_cycle, old_cycle):
            if new_index in wall_map or old_index in wall_map.values():
                raise ValueError("fixture sector wall ranges overlap")
            wall_map[new_index] = old_index
        rows.append({"step": ordinal, "generated_sector": ordinal, "source_sector": target,
                     "walls": [{"generated": a, "source": b} for a, b in zip(new_cycle, old_cycle)]})

    # Validate both directions before borrowing any connection to context.
    external = 0
    for ordinal, target in sector_map.items():
        for new_index in _cycle(fragment, ordinal):
            old_index = wall_map[new_index]
            old, new = source.walls[old_index], fragment.walls[new_index]
            for wall in (old, new):
                if wall.extra > 0 or wall.extra_data or wall.lotag or wall.hitag:
                    raise ValueError("fixture patch walls must have no native mechanisms")
            if (old.nextwall < 0) != (old.nextsector < 0):
                raise ValueError("fixture source has an incomplete portal")
            if old.nextsector >= 0:
                if not 0 <= old.nextwall < len(source.walls) or not 0 <= old.nextsector < len(source.sectors):
                    raise ValueError("fixture source portal is out of bounds")
                back = source.walls[old.nextwall]
                owner = source.sectors[old.nextsector]
                if (back.nextwall, back.nextsector) != (old_index, target) or not owner.wallptr <= old.nextwall < owner.wallptr + owner.wallnum:
                    raise ValueError("fixture source portal is not reciprocal")
                if not 0 <= back.point2 < len(source.walls):
                    raise ValueError("fixture source portal endpoint is out of bounds")
                end, back_end = source.walls[old.point2], source.walls[back.point2]
                if (old.x, old.y) != (back_end.x, back_end.y) or (end.x, end.y) != (back.x, back.y):
                    raise ValueError("fixture source portal endpoints do not reverse")
            if old.nextsector in source_to_generated:
                expected = source_to_generated[old.nextsector]
                if new.nextsector != expected or wall_map.get(new.nextwall) != old.nextwall:
                    raise ValueError("fixture generated internal portal differs from source")
                if (fragment.walls[new.nextwall].nextwall, fragment.walls[new.nextwall].nextsector) != (new_index, ordinal):
                    raise ValueError("fixture generated internal portal is not reciprocal")
            elif (new.nextwall, new.nextsector) != (-1, -1):
                raise ValueError("fixture fragment boundary must be closed before context attachment")
            elif old.nextsector >= 0:
                external += 1

    before = encode_map(source)
    result = deepcopy(source)
    for ordinal, target in sector_map.items():
        # Do not copy source material, flags, shade or extension records here.
        result.sectors[target] = replace(fragment.sectors[ordinal], wallptr=source.sectors[target].wallptr)
    for new_index, old_index in wall_map.items():
        old, new = source.walls[old_index], fragment.walls[new_index]
        result.walls[old_index] = replace(new, point2=wall_map[new.point2],
                                        nextwall=old.nextwall, nextsector=old.nextsector)
    unchanged_sectors = all(a == b for i, (a, b) in enumerate(zip(source.sectors, result.sectors)) if i not in source_to_generated)
    selected_walls = set(wall_map.values())
    unchanged_walls = all(a == b for i, (a, b) in enumerate(zip(source.walls, result.walls)) if i not in selected_walls)
    integrity = {"outside_sectors_unchanged": unchanged_sectors, "outside_walls_unchanged": unchanged_walls,
                 "all_sprites_unchanged": source.sprites == result.sprites,
                 "source_in_memory_unchanged": encode_map(source) == before,
                 "external_portals_restored": external,
                 "source_sector_count": len(source.sectors), "generated_sector_count": len(source_sectors),
                 "context_policy": "original map held fixed; only compiled stair records replaced",
                 "generated_context": False, "generated_mechanisms": False}
    if not all(integrity[key] for key in ("outside_sectors_unchanged", "outside_walls_unchanged", "all_sprites_unchanged", "source_in_memory_unchanged")):
        raise AssertionError("fixture modified a fixed input")
    return ContextFixture(result, tuple(rows), integrity)
