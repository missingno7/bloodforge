"""Deterministic compiler from BloodForge scenes to native Blood MAP records."""
from __future__ import annotations

from dataclasses import dataclass

from .native import NativeMap, Sector, Wall
from .scene import LevelScene, PlayerStart, StairSpec, StepAppearance, WallMaterial


class SceneCompileError(ValueError):
    """A semantic scene cannot be represented by the current MAP compiler."""


@dataclass(frozen=True)
class CompilationResult:
    native_map: NativeMap
    object_map: dict[str, tuple[str, int]]


_INT32_MIN = -(1 << 31)
_INT32_MAX = (1 << 31) - 1
_NATIVE_WALL_MAX = 32767


def _int32(name: str, value: int) -> None:
    if not _INT32_MIN <= value <= _INT32_MAX:
        raise SceneCompileError(f"{name}={value} is outside signed 32-bit native range")


def _check_stair(stair: StairSpec) -> None:
    if stair.count > _NATIVE_WALL_MAX // 4:
        raise SceneCompileError("stair count exceeds native wall index capacity")
    if stair.ceiling_z >= stair.floor_z:
        raise SceneCompileError("ceiling_z must be numerically above floor_z in native Build coordinates")
    dx = stair.tread * (1 if stair.direction == "east" else -1 if stair.direction == "west" else 0)
    dy = stair.tread * (1 if stair.direction == "south" else -1 if stair.direction == "north" else 0)
    for index in range(stair.count):
        floor_z = stair.floor_z - index * stair.rise
        if floor_z <= stair.ceiling_z:
            raise SceneCompileError(f"step {index} floor reaches or passes the ceiling")
        _int32(f"step {index} floor_z", floor_z)
        x0, y0, x1, y1 = _step_bounds(stair, index)
        _int32(f"step {index} minimum x", x0)
        _int32(f"step {index} minimum y", y0)
        _int32(f"step {index} maximum x", x1)
        _int32(f"step {index} maximum y", y1)
    _int32("ceiling_z", stair.ceiling_z)


def _step_bounds(stair: StairSpec, index: int) -> tuple[int, int, int, int]:
    dx = stair.tread * (1 if stair.direction == "east" else -1 if stair.direction == "west" else 0)
    dy = stair.tread * (1 if stair.direction == "south" else -1 if stair.direction == "north" else 0)
    x = stair.origin_x + index * dx
    y = stair.origin_y + index * dy
    if stair.direction in ("east", "west"):
        return x, y, x + stair.tread, y + stair.width
    return x, y, x + stair.width, y + stair.tread


def _walls_for_rect(x0: int, y0: int, x1: int, y1: int, *, tile: int,
                    first_index: int, sector_index: int) -> list[Wall]:
    # The loop uses a consistent winding and point2 links around the rectangle.
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    walls = []
    for edge, (x, y) in enumerate(corners):
        walls.append(Wall(x=x, y=y, point2=first_index + (edge + 1) % 4,
                          nextwall=-1, nextsector=-1, picnum=tile,
                          overpicnum=0, xrepeat=8, yrepeat=8))
    return walls


_CARDINAL_EDGES = {"north": 0, "east": 1, "south": 2, "west": 3}
_EDGE_CORNERS = ((0, 1), (1, 2), (2, 3), (3, 0))


def _walls_for_appearance(x0: int, y0: int, x1: int, y1: int, *,
                          first_index: int, appearance: StepAppearance
                          ) -> tuple[list[Wall], dict[str, int]]:
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    first_edge = _CARDINAL_EDGES[appearance.first_wall]
    side_by_edge = ("north", "east", "south", "west")
    order = tuple((first_edge + offset) % 4 for offset in range(4))
    edge_index = {side_by_edge[edge]: first_index + slot for slot, edge in enumerate(order)}
    walls: list[Wall] = []
    for slot, edge in enumerate(order):
        start_corner, _ = _EDGE_CORNERS[edge]
        x, y = corners[start_corner]
        material: WallMaterial = getattr(appearance, side_by_edge[edge])
        walls.append(Wall(x=x, y=y, point2=first_index + (slot + 1) % 4,
                          nextwall=-1, nextsector=-1, cstat=material.flags,
                          picnum=material.tile, overpicnum=material.overlay,
                          shade=material.shade, pal=material.palette,
                          xrepeat=material.xrepeat, yrepeat=material.yrepeat,
                          xpanning=material.xpanning, ypanning=material.ypanning))
    return walls, edge_index


def _start_for(stair: StairSpec, player_start: PlayerStart | None) -> tuple[int, int, int, int, int]:
    if player_start is None:
        x0, y0, x1, y1 = _step_bounds(stair, 0)
        x, y = (x0 + x1) // 2, (y0 + y1) // 2
        z = (stair.ceiling_z + stair.floor_z) // 2
        angle = {"east": 0, "south": 512, "west": 1024, "north": 1536}[stair.direction]
    else:
        if player_start.stair_id is not None and player_start.stair_id != stair.id:
            raise SceneCompileError(
                f"player start names stair {player_start.stair_id!r}, but scene contains {stair.id!r}"
            )
        x, y, z, angle = player_start.x, player_start.y, player_start.z, player_start.angle
    selected = None
    for index in range(stair.count):
        x0, y0, x1, y1 = _step_bounds(stair, index)
        floor_z = stair.floor_z - index * stair.rise
        if x0 <= x < x1 and y0 <= y < y1 and stair.ceiling_z <= z <= floor_z:
            selected = index
            break
    if selected is None:
        raise SceneCompileError("player start must lie inside a stair sector and between its ceiling and floor")
    return x, y, z, angle, selected


def compile_scene(scene: LevelScene) -> CompilationResult:
    """Compile a single connected straight stair recipe into MAP v7 records.

    A scene currently contains exactly one staircase. Adjacent step rectangles
    share reciprocal wall portals; their differing flat floor heights encode
    the stair risers. The first tread receives the default player start.
    """
    if not isinstance(scene, LevelScene):
        raise SceneCompileError("scene must be a LevelScene")
    if len(scene.stairs) != 1:
        raise SceneCompileError("the current compiler requires exactly one connected StairSpec")
    stair = scene.stairs[0]
    _check_stair(stair)

    sectors: list[Sector] = []
    walls: list[Wall] = []
    object_map: dict[str, tuple[str, int]] = {}
    side_indices: list[dict[str, int]] = []
    for index in range(stair.count):
        x0, y0, x1, y1 = _step_bounds(stair, index)
        sector_index = len(sectors)
        first_wall = len(walls)
        appearance = stair.appearance[index] if stair.appearance is not None else None
        if appearance is None:
            sector = Sector(wallptr=first_wall, wallnum=4,
                            ceilingz=stair.ceiling_z,
                            floorz=stair.floor_z - index * stair.rise,
                            ceilingpicnum=stair.ceiling_tile,
                            floorpicnum=stair.floor_tile)
            walls.extend(_walls_for_rect(x0, y0, x1, y1, tile=stair.wall_tile,
                                         first_index=first_wall, sector_index=sector_index))
            side_indices.append({side: first_wall + edge for edge, side in enumerate(
                ("north", "east", "south", "west"))})
        else:
            floor, ceiling = appearance.floor, appearance.ceiling
            sector = Sector(wallptr=first_wall, wallnum=4,
                            ceilingz=stair.ceiling_z,
                            floorz=stair.floor_z - index * stair.rise,
                            ceilingstat=ceiling.stat, floorstat=floor.stat,
                            ceilingpicnum=ceiling.tile, ceilingheinum=0,
                            ceilingshade=ceiling.shade, ceilingpal=ceiling.palette,
                            ceilingxpanning=ceiling.xpanning, ceilingypanning=ceiling.ypanning,
                            floorpicnum=floor.tile, floorheinum=0,
                            floorshade=floor.shade, floorpal=floor.palette,
                            floorxpanning=floor.xpanning, floorypanning=floor.ypanning,
                            visibility=appearance.visibility)
            step_walls, indices = _walls_for_appearance(
                x0, y0, x1, y1, first_index=first_wall, appearance=appearance)
            walls.extend(step_walls)
            side_indices.append(indices)
        sectors.append(sector)
        step_id = f"stair/{stair.id}/step/{index:04d}"
        object_map[f"{step_id}/sector"] = ("sector", sector_index)
        for side in ("north", "east", "south", "west"):
            object_map[f"{step_id}/wall/{side}"] = ("wall", side_indices[-1][side])

    shared_edges = {
        "east": (1, 3),
        "west": (3, 1),
        "south": (2, 0),
        "north": (0, 2),
    }
    current_edge, next_edge = shared_edges[stair.direction]
    for index in range(stair.count - 1):
        current_side = ("north", "east", "south", "west")[current_edge]
        next_side = ("north", "east", "south", "west")[next_edge]
        current_wall = side_indices[index][current_side]
        next_wall = side_indices[index + 1][next_side]
        walls[current_wall].nextwall = next_wall
        walls[current_wall].nextsector = index + 1
        walls[next_wall].nextwall = current_wall
        walls[next_wall].nextsector = index

    x, y, z, angle, start_sector = _start_for(stair, scene.player_start)
    for name, value in (("start_x", x), ("start_y", y), ("start_z", z)):
        _int32(name, value)
    object_map["player_start"] = ("player_start", -1)
    result = NativeMap(sectors=sectors, walls=walls, start_x=x, start_y=y,
                       start_z=z, start_angle=angle, start_sector=start_sector)
    return CompilationResult(result, object_map)
