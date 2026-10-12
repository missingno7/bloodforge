"""Small semantic source model for authored Blood scenes.

The model deliberately describes only constructions that the first compiler
can emit. Native record numbers are assigned by the compiler, never by callers.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal


Direction = Literal["north", "south", "east", "west"]


class SceneError(ValueError):
    """A scene or source transaction violates its semantic preconditions."""


def _integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneError(f"{name} must be an integer")
    return value


def _bounded(name: str, value: object, minimum: int, maximum: int) -> int:
    number = _integer(name, value)
    if not minimum <= number <= maximum:
        raise SceneError(f"{name} must be in {minimum}..{maximum}")
    return number


@dataclass(frozen=True)
class PlaneMaterial:
    """Source-authored flat appearance, expressed in native MAP field units."""

    tile: int = 0
    shade: int = 0
    palette: int = 0
    xpanning: int = 0
    ypanning: int = 0
    stat: int = 0

    def __post_init__(self) -> None:
        _bounded("plane tile", self.tile, 0, 32767)
        _bounded("plane shade", self.shade, -128, 127)
        _bounded("plane palette", self.palette, 0, 255)
        _bounded("plane xpanning", self.xpanning, 0, 255)
        _bounded("plane ypanning", self.ypanning, 0, 255)
        _bounded("plane stat", self.stat, 0, 65535)
        if self.stat & 2:
            raise SceneError("sloped plane stat bit is unsupported for stair appearance")


@dataclass(frozen=True)
class WallMaterial:
    """Source-authored wall appearance; blocking and hitscan bits are excluded."""

    tile: int = 0
    overlay: int = 0
    shade: int = 0
    palette: int = 0
    xrepeat: int = 8
    yrepeat: int = 8
    xpanning: int = 0
    ypanning: int = 0
    flags: int = 0

    def __post_init__(self) -> None:
        _bounded("wall tile", self.tile, 0, 32767)
        _bounded("wall overlay", self.overlay, 0, 32767)
        _bounded("wall shade", self.shade, -128, 127)
        _bounded("wall palette", self.palette, 0, 255)
        _bounded("wall xrepeat", self.xrepeat, 0, 255)
        _bounded("wall yrepeat", self.yrepeat, 0, 255)
        _bounded("wall xpanning", self.xpanning, 0, 255)
        _bounded("wall ypanning", self.ypanning, 0, 255)
        _bounded("wall flags", self.flags, 0, 65535)
        # Native Build wall flags: bit 0 blocks movement, bit 6 blocks
        # hitscan. Keep one-way portals (bit 5) out of appearance as well.
        # Allow bottom-swap/alignment, flips, masking and translucence only
        # (bits 1,2,3,4,7,8,9; nblood/source/build/include/buildtypes.h).
        if self.flags & ~0x039E:
            raise SceneError("wall flags contain unsupported collision, one-way or extension bits")


@dataclass(frozen=True)
class StepAppearance:
    """Per-step material appearance and deterministic native wall anchor."""

    floor: PlaneMaterial = PlaneMaterial()
    ceiling: PlaneMaterial = PlaneMaterial()
    north: WallMaterial = WallMaterial()
    east: WallMaterial = WallMaterial()
    south: WallMaterial = WallMaterial()
    west: WallMaterial = WallMaterial()
    visibility: int = 0
    first_wall: Direction = "north"

    def __post_init__(self) -> None:
        for name in ("floor", "ceiling"):
            if not isinstance(getattr(self, name), PlaneMaterial):
                raise SceneError(f"{name} must be PlaneMaterial")
        for name in ("north", "east", "south", "west"):
            if not isinstance(getattr(self, name), WallMaterial):
                raise SceneError(f"{name} must be WallMaterial")
        _bounded("sector visibility", self.visibility, 0, 255)
        if self.first_wall not in ("north", "east", "south", "west"):
            raise SceneError("first_wall must be north, east, south, or west")


@dataclass(frozen=True)
class StairSpec:
    """A straight run of flat rectangular steps.

    Native Build z grows downward, so each ascending step's floor is lower by
    ``rise`` (its numeric z value decreases). ``origin_x/y`` is the minimum-x,
    minimum-y corner of the first tread. Treads extend in positive coordinate
    directions; subsequent treads translate east/west on x runs and
    south/north on y runs.
    """

    id: str
    count: int
    width: int
    tread: int
    rise: int
    origin_x: int
    origin_y: int
    floor_z: int
    ceiling_z: int
    direction: Direction
    floor_tile: int
    ceiling_tile: int
    wall_tile: int
    appearance: tuple[StepAppearance, ...] | None = None

    def __post_init__(self) -> None:
        _id("stair id", self.id)
        for name in ("count", "width", "tread", "rise"):
            if _integer(name, getattr(self, name)) <= 0:
                raise SceneError(f"{name} must be positive")
        for name in ("origin_x", "origin_y", "floor_z", "ceiling_z"):
            _integer(name, getattr(self, name))
        if self.direction not in ("north", "south", "east", "west"):
            raise SceneError("direction must be north, south, east, or west")
        for name in ("floor_tile", "ceiling_tile", "wall_tile"):
            tile = _integer(name, getattr(self, name))
            if not 0 <= tile <= 32767:
                raise SceneError(f"{name} must fit a non-negative signed 16-bit tile id")
        if self.appearance is not None:
            if not isinstance(self.appearance, tuple) or len(self.appearance) != self.count:
                raise SceneError("appearance must be a tuple with exactly one StepAppearance per step")
            if any(not isinstance(value, StepAppearance) for value in self.appearance):
                raise SceneError("appearance values must be StepAppearance")


@dataclass(frozen=True)
class AreaSpec:
    """A flat rectangular room or corridor, measured in native coordinates."""

    id: str
    origin_x: int
    origin_y: int
    size_x: int
    size_y: int
    floor_z: int
    ceiling_z: int
    appearance: StepAppearance = StepAppearance()
    role: Literal["room", "corridor"] = "room"

    def __post_init__(self) -> None:
        _id("area id", self.id)
        for name in ("origin_x", "origin_y", "floor_z", "ceiling_z"):
            _bounded(name, getattr(self, name), -(1 << 31), (1 << 31) - 1)
        for name in ("size_x", "size_y"):
            _bounded(name, getattr(self, name), 1, (1 << 31) - 1)
        if self.origin_x + self.size_x > (1 << 31) - 1 or self.origin_y + self.size_y > (1 << 31) - 1:
            raise SceneError("area maximum x/y must fit signed 32-bit native coordinates")
        if self.ceiling_z >= self.floor_z:
            raise SceneError("area ceiling_z must be numerically above floor_z")
        if not isinstance(self.appearance, StepAppearance):
            raise SceneError("area appearance must be StepAppearance")
        if self.role not in ("room", "corridor"):
            raise SceneError("area role must be room or corridor")


@dataclass(frozen=True)
class LandingSpec:
    """A flat stair-end tread whose footprint and levels derive from a stair."""

    id: str
    stair_id: str
    end: Literal["bottom", "top"]
    depth: int
    appearance: StepAppearance | None = None

    def __post_init__(self) -> None:
        _id("landing id", self.id)
        _id("landing stair_id", self.stair_id)
        if self.end not in ("bottom", "top"):
            raise SceneError("landing end must be bottom or top")
        _bounded("landing depth", self.depth, 1, (1 << 31) - 1)
        if self.appearance is not None and not isinstance(self.appearance, StepAppearance):
            raise SceneError("landing appearance must be StepAppearance or None")


_CARDINALS = ("north", "east", "south", "west")
_END_SIDES = ("bottom", "top")


@dataclass(frozen=True)
class PortalEndpoint:
    """Named semantic boundary; native wall indexes are never authored."""

    object_id: str
    side: Direction | Literal["bottom", "top"]

    def __post_init__(self) -> None:
        _id("portal endpoint object_id", self.object_id)
        if self.side not in (*_CARDINALS, *_END_SIDES):
            raise SceneError("portal endpoint side must be cardinal, bottom, or top")


@dataclass(frozen=True)
class ConnectionSpec:
    """An explicit shared-boundary connection between semantic constructions."""

    id: str
    a: PortalEndpoint
    b: PortalEndpoint

    def __post_init__(self) -> None:
        _id("connection id", self.id)
        if not isinstance(self.a, PortalEndpoint) or not isinstance(self.b, PortalEndpoint):
            raise SceneError("connection endpoints must be PortalEndpoint values")


def _id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SceneError(f"{name} must be a non-empty string")
    if value == "player_start":
        raise SceneError("'player_start' is reserved and cannot be a construction id")
    return value


@dataclass(frozen=True)
class PlayerStart:
    """Blood player start location; z is the native eye position."""

    x: int
    y: int
    z: int
    angle: int = 0
    stair_id: str | None = None
    area_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("x", "y", "z", "angle"):
            _integer(name, getattr(self, name))
        if not 0 <= self.angle <= 2047:
            raise SceneError("player angle must be in the Build range 0..2047")
        if self.stair_id is not None and (not isinstance(self.stair_id, str) or not self.stair_id):
            raise SceneError("player stair_id must be a non-empty string when supplied")
        if self.area_id is not None and (not isinstance(self.area_id, str) or not self.area_id):
            raise SceneError("player area_id must be a non-empty string when supplied")
        if self.stair_id is not None and self.area_id is not None:
            raise SceneError("player start cannot name both stair_id and area_id")


@dataclass(frozen=True)
class LevelScene:
    """Immutable semantic scene revision for the current construction recipe."""

    stairs: tuple[StairSpec, ...] = ()
    player_start: PlayerStart | None = None
    revision: int = 0
    landings: tuple[LandingSpec, ...] = ()
    areas: tuple[AreaSpec, ...] = ()
    connections: tuple[ConnectionSpec, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stairs, tuple) or any(not isinstance(s, StairSpec) for s in self.stairs):
            raise SceneError("stairs must be a tuple of StairSpec values")
        if not isinstance(self.landings, tuple) or any(not isinstance(item, LandingSpec) for item in self.landings):
            raise SceneError("landings must be a tuple of LandingSpec values")
        if not isinstance(self.areas, tuple) or any(not isinstance(item, AreaSpec) for item in self.areas):
            raise SceneError("areas must be a tuple of AreaSpec values")
        if not isinstance(self.connections, tuple) or any(not isinstance(item, ConnectionSpec) for item in self.connections):
            raise SceneError("connections must be a tuple of ConnectionSpec values")
        all_ids = [item.id for collection in
                   (self.stairs, self.areas, self.landings, self.connections)
                   for item in collection]
        ids = all_ids
        if len(ids) != len(set(ids)):
            raise SceneError("construction ids must be globally unique")
        if self.player_start is not None and not isinstance(self.player_start, PlayerStart):
            raise SceneError("player_start must be PlayerStart or None")
        if _integer("revision", self.revision) < 0:
            raise SceneError("revision must be non-negative")


@dataclass(frozen=True)
class SceneChange:
    """One preconditioned semantic construction or player-start change.

    ``expected`` is compared to the current complete value (including None).
    Use operation ``set_stair`` with an absent expected value to insert, or
    ``remove_stair`` with the current value to remove. Player-start changes use
    ``set_player_start`` and the same exact-value precondition.
    """

    operation: Literal[
        "set_stair", "remove_stair", "set_area", "remove_area",
        "set_landing", "remove_landing", "set_connection", "remove_connection",
        "set_player_start",
    ]
    object_id: str
    expected: StairSpec | AreaSpec | LandingSpec | ConnectionSpec | PlayerStart | None
    value: StairSpec | AreaSpec | LandingSpec | ConnectionSpec | PlayerStart | None = None

    def __post_init__(self) -> None:
        allowed = {"set_stair", "remove_stair", "set_area", "remove_area",
                   "set_landing", "remove_landing", "set_connection",
                   "remove_connection", "set_player_start"}
        if not isinstance(self.operation, str) or self.operation not in allowed:
            raise SceneError(f"unsupported operation {self.operation!r}")
        if not isinstance(self.object_id, str) or not self.object_id.strip():
            raise SceneError("scene change object_id must be a non-empty string")
        if self.operation == "set_player_start":
            if self.object_id != "player_start":
                raise SceneError("player-start change must use id 'player_start'")
        elif self.object_id == "player_start":
            raise SceneError("'player_start' is reserved for player-start changes")


@dataclass(frozen=True)
class Rollback:
    expected_revision: int
    previous: LevelScene


@dataclass(frozen=True)
class TransactionResult:
    scene: LevelScene
    changed_ids: tuple[str, ...]
    committed: bool
    rollback: Rollback | None


def apply_changes(scene: LevelScene, changes: tuple[SceneChange, ...], *,
                  expected_revision: int, dry_run: bool = False) -> TransactionResult:
    """Apply an atomic batch of semantic changes after checking preconditions.

    The input is immutable. A dry run returns the proposed scene at the same
    revision and has no rollback token; a commit advances exactly one revision.
    """
    if scene.revision != expected_revision:
        raise SceneError(f"revision precondition failed: expected {expected_revision}, found {scene.revision}")
    if not isinstance(changes, tuple) or not changes:
        raise SceneError("changes must be a non-empty tuple")
    collections: dict[str, dict[str, object]] = {
        "stair": {item.id: item for item in scene.stairs},
        "area": {item.id: item for item in scene.areas},
        "landing": {item.id: item for item in scene.landings},
        "connection": {item.id: item for item in scene.connections},
    }
    types = {"stair": StairSpec, "area": AreaSpec,
             "landing": LandingSpec, "connection": ConnectionSpec}
    player_start = scene.player_start
    changed: list[str] = []
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, SceneChange):
            raise SceneError("changes must contain SceneChange values")
        if change.object_id in seen:
            raise SceneError(f"duplicate change for {change.object_id!r}")
        seen.add(change.object_id)
        if change.operation == "set_player_start":
            if change.object_id != "player_start" or (change.value is not None and not isinstance(change.value, PlayerStart)):
                raise SceneError("player-start change must use id 'player_start' and a PlayerStart or None value")
            if change.expected is not None and not isinstance(change.expected, PlayerStart):
                raise SceneError("player-start expected value must be PlayerStart or None")
            if player_start != change.expected:
                raise SceneError("player-start precondition failed")
            player_start = change.value
        elif change.operation.startswith("set_") or change.operation.startswith("remove_"):
            verb, kind = change.operation.split("_", 1)
            if kind not in collections:
                raise SceneError(f"unsupported operation {change.operation!r}")
            entity_type = types[kind]
            items = collections[kind]
            current = items.get(change.object_id)
            if verb == "set":
                if not isinstance(change.value, entity_type) or change.value.id != change.object_id:
                    raise SceneError(f"{change.operation} value must be {entity_type.__name__} with matching object_id")
                if change.expected is not None and not isinstance(change.expected, entity_type):
                    raise SceneError(f"{change.operation} expected value must be {entity_type.__name__} or None")
                if current != change.expected:
                    raise SceneError(f"{kind} precondition failed for {change.object_id!r}")
                items[change.object_id] = change.value
            else:
                if not isinstance(change.expected, entity_type):
                    raise SceneError(f"{change.operation} expected value must be {entity_type.__name__}")
                if current is None or current != change.expected or change.value is not None:
                    raise SceneError(f"{kind} precondition failed for {change.object_id!r}")
                del items[change.object_id]
        else:
            raise SceneError(f"unsupported operation {change.operation!r}")
        changed.append(change.object_id)

    candidate = replace(
        scene,
        stairs=tuple(sorted(collections["stair"].values(), key=lambda item: item.id)),
        areas=tuple(sorted(collections["area"].values(), key=lambda item: item.id)),
        landings=tuple(sorted(collections["landing"].values(), key=lambda item: item.id)),
        connections=tuple(sorted(collections["connection"].values(), key=lambda item: item.id)),
        player_start=player_start,
        revision=scene.revision if dry_run else scene.revision + 1)
    rollback = None if dry_run else Rollback(candidate.revision, scene)
    return TransactionResult(candidate, tuple(changed), not dry_run, rollback)


def rollback_changes(scene: LevelScene, rollback: Rollback) -> LevelScene:
    """Restore the prior content if the committed revision is still current."""
    if scene.revision != rollback.expected_revision:
        raise SceneError(f"rollback precondition failed: expected revision {rollback.expected_revision}, found {scene.revision}")
    return replace(rollback.previous, revision=scene.revision + 1)
