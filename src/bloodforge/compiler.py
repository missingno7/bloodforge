"""Deterministic compiler from BloodForge scenes to native Blood MAP records."""
from __future__ import annotations

from dataclasses import dataclass, field

from .native import NativeMap, Sector, Wall
from .scene import (
    AreaSpec,
    LevelScene,
    PlaneMaterial,
    PlayerStart,
    PortalEndpoint,
    StairSpec,
    StepAppearance,
    WallMaterial,
)


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
        if x1 - x0 > _INT32_MAX or y1 - y0 > _INT32_MAX:
            raise SceneCompileError(f"step {index} edge length exceeds signed 32-bit native range")
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


def _compile_single_stair(scene: LevelScene) -> CompilationResult:
    """Compile a single connected straight stair recipe into MAP v7 records.

    This legacy compatibility path preserves the original native record layout
    for a stair-only scene. Adjacent steps share reciprocal portals; the first
    tread receives the default player start.
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


@dataclass
class _RectShape:
    key: str
    object_id: str
    kind: str
    x0: int
    y0: int
    x1: int
    y1: int
    floor_z: int
    ceiling_z: int
    appearance: StepAppearance
    step_index: int | None = None
    side_intervals: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)
    side_walls: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)
    sector_index: int = -1


@dataclass(frozen=True)
class _Portal:
    id: str
    shape_a: str
    side_a: str
    shape_b: str
    side_b: str
    lo: int
    hi: int
    explicit_id: str | None = None


_OPPOSITE_SIDE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _appearance_for_stair(stair: StairSpec, index: int) -> StepAppearance:
    if stair.appearance is not None:
        return stair.appearance[index]
    wall = WallMaterial(tile=stair.wall_tile)
    return StepAppearance(floor=PlaneMaterial(tile=stair.floor_tile),
                          ceiling=PlaneMaterial(tile=stair.ceiling_tile),
                          north=wall, east=wall, south=wall, west=wall)


def _int32_rect(name: str, value: int) -> None:
    _int32(name, value)


def _shape_for_area(area: AreaSpec) -> _RectShape:
    x1, y1 = area.origin_x + area.size_x, area.origin_y + area.size_y
    _int32_rect(f"area {area.id} maximum x", x1)
    _int32_rect(f"area {area.id} maximum y", y1)
    if area.size_x > _INT32_MAX or area.size_y > _INT32_MAX:
        raise SceneCompileError(f"area {area.id!r} edge length exceeds signed 32-bit native range")
    return _RectShape(f"area/{area.id}", area.id, "area", area.origin_x, area.origin_y,
                      x1, y1, area.floor_z, area.ceiling_z, area.appearance)


def _boundary(shape: _RectShape, side: str) -> tuple[str, int, int, int, int]:
    """Return axis, fixed coordinate, low/high, and directed start coordinate."""
    if side == "north":
        return "x", shape.y0, shape.x0, shape.x1, shape.x0
    if side == "east":
        return "y", shape.x1, shape.y0, shape.y1, shape.y0
    if side == "south":
        return "x", shape.y1, shape.x0, shape.x1, shape.x1
    if side == "west":
        return "y", shape.x0, shape.y0, shape.y1, shape.y1
    raise SceneCompileError(f"unsupported portal side {side!r}")


def _end_shape(stair: StairSpec, shapes: dict[str, _RectShape], end: str) -> tuple[_RectShape, str]:
    if end == "bottom":
        index = 0
        side = {"east": "west", "west": "east", "south": "north", "north": "south"}[stair.direction]
    elif end == "top":
        index = stair.count - 1
        side = {"east": "east", "west": "west", "south": "south", "north": "north"}[stair.direction]
    else:
        raise SceneCompileError(f"invalid stair endpoint {end!r}")
    return shapes[f"stair/{stair.id}/step/{index:04d}"], side


def _resolve_endpoint(endpoint: PortalEndpoint, shapes: dict[str, _RectShape],
                      stairs: dict[str, StairSpec], object_shapes: dict[str, _RectShape]
                      ) -> tuple[_RectShape, str]:
    if endpoint.object_id in stairs:
        return _end_shape(stairs[endpoint.object_id], shapes, endpoint.side)
    try:
        shape = object_shapes[endpoint.object_id]
    except KeyError as exc:
        raise SceneCompileError(f"connection endpoint {endpoint.object_id!r} does not exist") from exc
    if endpoint.side not in _OPPOSITE_SIDE:
        raise SceneCompileError("area and landing endpoints must name a cardinal side")
    return shape, endpoint.side


def _portal_interval(a: _RectShape, side_a: str, b: _RectShape, side_b: str,
                     *, internal_stair: bool = False) -> tuple[int, int]:
    if a.key == b.key:
        raise SceneCompileError("a connection cannot join an object to itself")
    if side_b != _OPPOSITE_SIDE[side_a]:
        raise SceneCompileError("connection endpoints must face opposite cardinal directions")
    axis_a, fixed_a, lo_a, hi_a, _ = _boundary(a, side_a)
    axis_b, fixed_b, lo_b, hi_b, _ = _boundary(b, side_b)
    if axis_a != axis_b or fixed_a != fixed_b:
        raise SceneCompileError("connection endpoints must lie on the same straight boundary")
    lo, hi = max(lo_a, lo_b), min(hi_a, hi_b)
    if hi <= lo:
        raise SceneCompileError("connection endpoints have no positive-length boundary overlap")
    if not internal_stair and a.floor_z != b.floor_z:
        raise SceneCompileError("connected area floor heights must match")
    floor_limit = min(a.floor_z, b.floor_z) if internal_stair else a.floor_z
    common_ceiling = max(a.ceiling_z, b.ceiling_z)
    if common_ceiling >= floor_limit:
        raise SceneCompileError("connection has no positive vertical opening")
    return lo, hi


def _check_interior_overlaps(shapes: list[_RectShape]) -> None:
    ordered = sorted(shapes, key=lambda s: (s.x0, s.x1, s.y0, s.y1, s.key))
    active: list[_RectShape] = []
    for shape in ordered:
        active = [other for other in active if other.x1 > shape.x0]
        for other in active:
            if min(shape.y1, other.y1) > max(shape.y0, other.y0):
                raise SceneCompileError(f"rectangle interiors overlap: {other.key!r} and {shape.key!r}")
        active.append(shape)


def _check_link_overlaps(links: list[_Portal], shapes: dict[str, _RectShape]) -> None:
    occupied: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
    for link in links:
        for key in ((link.shape_a, link.side_a), (link.shape_b, link.side_b)):
            entries = occupied.setdefault(key, [])
            for lo, hi, other_id in entries:
                if min(hi, link.hi) > max(lo, link.lo):
                    raise SceneCompileError(
                        f"portal interval overlaps on {key[0]}/{key[1]} between {other_id!r} and {link.id!r}"
                    )
            entries.append((link.lo, link.hi, link.id))
            shapes[key[0]].side_intervals.setdefault(key[1], []).append((link.lo, link.hi, link.id))


def _wall_for_material(material: WallMaterial, *, xrepeat: int | None = None,
                       xpanning: int | None = None) -> Wall:
    return Wall(cstat=material.flags, picnum=material.tile, overpicnum=material.overlay,
                shade=material.shade, pal=material.palette,
                xrepeat=material.xrepeat if xrepeat is None else xrepeat,
                yrepeat=material.yrepeat,
                xpanning=material.xpanning if xpanning is None else xpanning,
                ypanning=material.ypanning)


def _build_shape_walls(shape: _RectShape, first_index: int) -> list[Wall]:
    cardinal_order = ("north", "east", "south", "west")
    first_edge = _CARDINAL_EDGES[shape.appearance.first_wall]
    order = tuple((first_edge + offset) % 4 for offset in range(4))
    made: list[tuple[Wall, str, int, int]] = []
    pieces: dict[str, list[tuple[int, int, int]]] = {}
    for edge in order:
        side = cardinal_order[edge]
        axis, _, side_lo, side_hi, directed_start = _boundary(shape, side)
        breakpoints = {side_lo, side_hi}
        for lo, hi, _ in shape.side_intervals.get(side, ()):
            breakpoints.add(lo)
            breakpoints.add(hi)
        directed = sorted(breakpoints, reverse=(side in ("south", "west")))
        material: WallMaterial = getattr(shape.appearance, side)
        side_len = side_hi - side_lo
        segments = []
        if len(directed) > 2 and material.flags & 8:
            raise SceneCompileError(f"cannot preserve x-flipped material phase while splitting {shape.key}/{side}")
        for start_coord, end_coord in zip(directed, directed[1:]):
            if start_coord == end_coord:
                continue
            lo, hi = sorted((start_coord, end_coord))
            segment_len = hi - lo
            start_distance = abs(start_coord - directed_start)
            if len(directed) > 2:
                repeat_num = material.xrepeat * segment_len
                phase_num = 8 * material.xrepeat * start_distance
                if repeat_num % side_len or phase_num % side_len:
                    raise SceneCompileError(
                        f"texture scale/phase is not exactly representable on split wall {shape.key}/{side}"
                    )
                xrepeat = repeat_num // side_len
                if material.xrepeat and xrepeat == 0:
                    raise SceneCompileError(f"split wall repeat rounds to zero on {shape.key}/{side}")
                xpanning = (material.xpanning + phase_num // side_len) % 256
            else:
                xrepeat, xpanning = material.xrepeat, material.xpanning
            if axis == "x":
                x, y = start_coord, shape.y0 if side == "north" else shape.y1
            else:
                x, y = shape.x1 if side == "east" else shape.x0, start_coord
            wall = _wall_for_material(material, xrepeat=xrepeat, xpanning=xpanning)
            wall.x, wall.y = x, y
            made.append((wall, side, lo, hi))
            segments.append((lo, hi, first_index + len(made) - 1))
        pieces[side] = segments

    walls = [item[0] for item in made]
    for slot, (wall, side, lo, hi) in enumerate(made):
        wall.point2 = first_index + (slot + 1) % len(made)
        # Store in directed order by interval. Portal matching uses physical
        # bounds, while semantic segment keys follow the wall-chain order.
    shape.side_walls = pieces
    return walls


def _segment_wall(shape: _RectShape, side: str, lo: int, hi: int) -> int:
    for segment_lo, segment_hi, wall_index in shape.side_walls[side]:
        if segment_lo == lo and segment_hi == hi:
            return wall_index
    raise SceneCompileError(f"internal compiler error: missing split wall for {shape.key}/{side}/{lo}..{hi}")


def _default_start_shape(scene: LevelScene, shapes: list[_RectShape],
                         object_shapes: dict[str, _RectShape]
                         ) -> tuple[int, int, int, int, int]:
    start = scene.player_start
    if start is None:
        if not shapes:
            raise SceneCompileError("scene must contain at least one native sector")
        shape = shapes[0]
        x, y = (shape.x0 + shape.x1) // 2, (shape.y0 + shape.y1) // 2
        z, angle = (shape.ceiling_z + shape.floor_z) // 2, 0
        return x, y, z, angle, shape.sector_index

    if start.area_id is not None:
        shape = object_shapes.get(start.area_id)
        if shape is None:
            raise SceneCompileError(f"player start references missing area or landing {start.area_id!r}")
        targets = [shape]
    elif start.stair_id is not None:
        targets = [shape for shape in shapes if shape.kind == "stair" and shape.object_id == start.stair_id]
    else:
        targets = shapes
    for shape in targets:
        if (shape.x0 <= start.x < shape.x1 and shape.y0 <= start.y < shape.y1
                and shape.ceiling_z <= start.z <= shape.floor_z):
            return start.x, start.y, start.z, start.angle, shape.sector_index
    raise SceneCompileError("player start must lie inside its named construction and between its ceiling and floor")


def _compile_scene_with_areas(scene: LevelScene) -> CompilationResult:
    base_sector_count = sum(stair.count for stair in scene.stairs) + len(scene.areas) + len(scene.landings)
    if base_sector_count > _NATIVE_WALL_MAX:
        raise SceneCompileError("scene sector count exceeds native index capacity")
    if base_sector_count * 4 > _NATIVE_WALL_MAX:
        raise SceneCompileError("base scene wall count exceeds native index capacity before portal splits")

    stairs_by_id = {stair.id: stair for stair in scene.stairs}
    shapes_by_key: dict[str, _RectShape] = {}
    object_shapes: dict[str, _RectShape] = {}
    stair_shapes: dict[str, list[_RectShape]] = {}

    for stair in sorted(scene.stairs, key=lambda value: value.id):
        _check_stair(stair)
        steps = []
        for index in range(stair.count):
            x0, y0, x1, y1 = _step_bounds(stair, index)
            shape = _RectShape(f"stair/{stair.id}/step/{index:04d}", stair.id, "stair",
                               x0, y0, x1, y1, stair.floor_z - index * stair.rise,
                               stair.ceiling_z, _appearance_for_stair(stair, index), index)
            shapes_by_key[shape.key] = shape
            steps.append(shape)
        stair_shapes[stair.id] = steps

    for area in sorted(scene.areas, key=lambda value: value.id):
        shape = _shape_for_area(area)
        if shape.key in shapes_by_key:
            raise SceneCompileError(f"duplicate semantic object {shape.key!r}")
        shapes_by_key[shape.key] = shape
        object_shapes[area.id] = shape

    landing_ends: set[tuple[str, str]] = set()
    for landing in sorted(scene.landings, key=lambda value: value.id):
        stair = stairs_by_id.get(landing.stair_id)
        if stair is None:
            raise SceneCompileError(f"landing {landing.id!r} references missing stair {landing.stair_id!r}")
        end_key = (landing.stair_id, landing.end)
        if end_key in landing_ends:
            raise SceneCompileError(f"stair {landing.stair_id!r} has more than one {landing.end} landing")
        landing_ends.add(end_key)
        if landing.end == "bottom":
            endpoint = stair_shapes[stair.id][0]
        else:
            endpoint = stair_shapes[stair.id][-1]
        x0, y0, x1, y1 = endpoint.x0, endpoint.y0, endpoint.x1, endpoint.y1
        if stair.direction == "east":
            if landing.end == "bottom": x0 -= landing.depth; x1 = endpoint.x0
            else: x0 = endpoint.x1; x1 += landing.depth
        elif stair.direction == "west":
            if landing.end == "bottom": x0 = endpoint.x1; x1 += landing.depth
            else: x0 -= landing.depth; x1 = endpoint.x0
        elif stair.direction == "south":
            if landing.end == "bottom": y0 -= landing.depth; y1 = endpoint.y0
            else: y0 = endpoint.y1; y1 += landing.depth
        else:  # north
            if landing.end == "bottom": y0 = endpoint.y1; y1 += landing.depth
            else: y0 -= landing.depth; y1 = endpoint.y0
        for name, value in (("minimum x", x0), ("minimum y", y0),
                            ("maximum x", x1), ("maximum y", y1)):
            _int32(f"landing {landing.id} {name}", value)
        if x1 - x0 > _INT32_MAX or y1 - y0 > _INT32_MAX:
            raise SceneCompileError(f"landing {landing.id!r} edge length exceeds signed 32-bit native range")
        appearance = landing.appearance or endpoint.appearance
        shape = _RectShape(f"landing/{landing.id}", landing.id, "landing", x0, y0, x1, y1,
                           endpoint.floor_z, endpoint.ceiling_z, appearance)
        shapes_by_key[shape.key] = shape
        object_shapes[landing.id] = shape

    _check_interior_overlaps(list(shapes_by_key.values()))
    shapes = sorted(shapes_by_key.values(), key=lambda shape: shape.key)
    if not shapes:
        raise SceneCompileError("scene must contain at least one native sector")
    if len(shapes) > _NATIVE_WALL_MAX:
        raise SceneCompileError("scene sector count exceeds native index capacity")

    links: list[_Portal] = []
    # Straight-stair internal portals are the only implicit stair portals.
    for stair in sorted(scene.stairs, key=lambda value: value.id):
        side_next = stair.direction
        side_prev = _OPPOSITE_SIDE[side_next]
        steps = stair_shapes[stair.id]
        for index, (a, b) in enumerate(zip(steps, steps[1:])):
            lo, hi = _portal_interval(a, side_next, b, side_prev, internal_stair=True)
            links.append(_Portal(f"stair/{stair.id}/internal/{index:04d}",
                                 a.key, side_next, b.key, side_prev, lo, hi))

    # Landings automatically connect to their named stair endpoint.
    for landing in sorted(scene.landings, key=lambda value: value.id):
        stair = stairs_by_id[landing.stair_id]
        stair_shape, stair_side = _end_shape(stair, shapes_by_key, landing.end)
        landing_shape = object_shapes[landing.id]
        landing_side = _OPPOSITE_SIDE[stair_side]
        lo, hi = _portal_interval(stair_shape, stair_side, landing_shape, landing_side)
        links.append(_Portal(f"landing/{landing.id}/stair/{stair.id}/{landing.end}",
                             stair_shape.key, stair_side, landing_shape.key, landing_side, lo, hi))

    for connection in sorted(scene.connections, key=lambda value: value.id):
        if connection.a.object_id == connection.b.object_id:
            raise SceneCompileError(f"connection {connection.id!r} cannot connect an object to itself")
        a, side_a = _resolve_endpoint(connection.a, shapes_by_key, stairs_by_id, object_shapes)
        b, side_b = _resolve_endpoint(connection.b, shapes_by_key, stairs_by_id, object_shapes)
        lo, hi = _portal_interval(a, side_a, b, side_b)
        links.append(_Portal(f"connection/{connection.id}", a.key, side_a, b.key, side_b,
                             lo, hi, connection.id))

    _check_link_overlaps(links, shapes_by_key)
    for shape in shapes:
        for side in shape.side_intervals:
            axis, fixed, lo, hi, _ = _boundary(shape, side)
            if any(start < lo or end > hi for start, end, _ in shape.side_intervals[side]):
                raise SceneCompileError(f"portal interval lies outside {shape.key}/{side}")

    sectors: list[Sector] = []
    walls: list[Wall] = []
    object_map: dict[str, tuple[str, int]] = {}
    for shape in shapes:
        first_wall = len(walls)
        appearance = shape.appearance
        floor, ceiling = appearance.floor, appearance.ceiling
        sector_index = len(sectors)
        shape.sector_index = sector_index
        sector = Sector(wallptr=first_wall, wallnum=0,
                        ceilingz=shape.ceiling_z, floorz=shape.floor_z,
                        ceilingstat=ceiling.stat, floorstat=floor.stat,
                        ceilingpicnum=ceiling.tile, ceilingheinum=0,
                        ceilingshade=ceiling.shade, ceilingpal=ceiling.palette,
                        ceilingxpanning=ceiling.xpanning, ceilingypanning=ceiling.ypanning,
                        floorpicnum=floor.tile, floorheinum=0,
                        floorshade=floor.shade, floorpal=floor.palette,
                        floorxpanning=floor.xpanning, floorypanning=floor.ypanning,
                        visibility=appearance.visibility)
        new_walls = _build_shape_walls(shape, first_wall)
        sector.wallnum = len(new_walls)
        sectors.append(sector)
        walls.extend(new_walls)
        object_map[f"{shape.key}/sector"] = ("sector", sector_index)

        side_order = ("north", "east", "south", "west")
        first_edge = _CARDINAL_EDGES[appearance.first_wall]
        for edge in tuple((first_edge + offset) % 4 for offset in range(4)):
            side = side_order[edge]
            pieces = shape.side_walls[side]
            if len(pieces) == 1:
                object_map[f"{shape.key}/wall/{side}"] = ("wall", pieces[0][2])
            else:
                for segment_index, (_, _, wall_index) in enumerate(pieces):
                    object_map[f"{shape.key}/wall/{side}/segment/{segment_index:04d}"] = ("wall", wall_index)

    if len(walls) > _NATIVE_WALL_MAX:
        raise SceneCompileError("scene wall count exceeds native index capacity")

    for link in links:
        wall_a = _segment_wall(shapes_by_key[link.shape_a], link.side_a, link.lo, link.hi)
        wall_b = _segment_wall(shapes_by_key[link.shape_b], link.side_b, link.lo, link.hi)
        a_shape, b_shape = shapes_by_key[link.shape_a], shapes_by_key[link.shape_b]
        walls[wall_a].nextwall = wall_b
        walls[wall_a].nextsector = b_shape.sector_index
        walls[wall_b].nextwall = wall_a
        walls[wall_b].nextsector = a_shape.sector_index
        object_map[f"{link.id}/a"] = ("wall", wall_a)
        object_map[f"{link.id}/b"] = ("wall", wall_b)
        if link.explicit_id is not None:
            for shape_key, side, wall_index in ((link.shape_a, link.side_a, wall_a),
                                                (link.shape_b, link.side_b, wall_b)):
                object_map[f"{shape_key}/wall/{side}/connection/{link.explicit_id}"] = ("wall", wall_index)

    if scene.player_start is None and stairs_by_id:
        # Preserve the historical default angle/position for stair scenes.
        first_stair = stairs_by_id[sorted(stairs_by_id)[0]]
        start_values = _start_for(first_stair, None)
        sx, sy, sz, angle, old_index = start_values
        start_shape_key = f"stair/{first_stair.id}/step/{old_index:04d}"
        start_sector = shapes_by_key[start_shape_key].sector_index
    else:
        sx, sy, sz, angle, start_sector = _default_start_shape(
            scene, shapes, object_shapes)
    for name, value in (("start_x", sx), ("start_y", sy), ("start_z", sz)):
        _int32(name, value)
    object_map["player_start"] = ("player_start", -1)
    native = NativeMap(sectors=sectors, walls=walls, start_x=sx, start_y=sy,
                       start_z=sz, start_angle=angle, start_sector=start_sector)
    return CompilationResult(native, object_map)


def compile_scene(scene: LevelScene) -> CompilationResult:
    """Compile stairs, flat rooms/corridors, and explicit shared-boundary portals.

    One historical stair-only recipe retains its exact original native record
    layout. Context geometry is compiled with deterministic semantic ordering;
    it remains a static rectangular topology and does not claim playability.
    """
    if not isinstance(scene, LevelScene):
        raise SceneCompileError("scene must be a LevelScene")
    stair_ids = {stair.id for stair in scene.stairs}
    area_ids = {area.id for area in scene.areas}
    landing_ids = {landing.id for landing in scene.landings}
    known_ids = stair_ids | area_ids | landing_ids
    for landing in scene.landings:
        if landing.stair_id not in stair_ids:
            raise SceneCompileError(f"landing {landing.id!r} references missing stair {landing.stair_id!r}")
    for connection in scene.connections:
        if connection.a.object_id == connection.b.object_id:
            raise SceneCompileError(f"connection {connection.id!r} cannot connect an object to itself")
        for endpoint in (connection.a, connection.b):
            if endpoint.object_id not in known_ids:
                raise SceneCompileError(f"connection {connection.id!r} references missing object {endpoint.object_id!r}")
            if endpoint.object_id in stair_ids and endpoint.side not in ("bottom", "top"):
                raise SceneCompileError("stair connection endpoints must use bottom or top")
            if endpoint.object_id not in stair_ids and endpoint.side not in _OPPOSITE_SIDE:
                raise SceneCompileError("area and landing connection endpoints must use cardinal sides")
    if scene.player_start is not None:
        if scene.player_start.stair_id is not None and scene.player_start.stair_id not in stair_ids:
            raise SceneCompileError(f"player start references missing stair {scene.player_start.stair_id!r}")
        if scene.player_start.area_id is not None and scene.player_start.area_id not in area_ids | landing_ids:
            raise SceneCompileError(f"player start references missing area or landing {scene.player_start.area_id!r}")
    if (len(scene.stairs) == 1 and not scene.landings and not scene.areas
            and not scene.connections
            and (scene.player_start is None or scene.player_start.area_id is None)):
        return _compile_single_stair(scene)
    return _compile_scene_with_areas(scene)
