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
        if not isinstance(self.id, str) or not self.id.strip():
            raise SceneError("stair id must be a non-empty string")
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
class PlayerStart:
    """Blood player start location; z is the native eye position."""

    x: int
    y: int
    z: int
    angle: int = 0
    stair_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("x", "y", "z", "angle"):
            _integer(name, getattr(self, name))
        if not 0 <= self.angle <= 2047:
            raise SceneError("player angle must be in the Build range 0..2047")
        if self.stair_id is not None and (not isinstance(self.stair_id, str) or not self.stair_id):
            raise SceneError("player stair_id must be a non-empty string when supplied")


@dataclass(frozen=True)
class LevelScene:
    """Immutable semantic scene revision for the current construction recipe."""

    stairs: tuple[StairSpec, ...]
    player_start: PlayerStart | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.stairs, tuple) or any(not isinstance(s, StairSpec) for s in self.stairs):
            raise SceneError("stairs must be a tuple of StairSpec values")
        ids = [stair.id for stair in self.stairs]
        if len(ids) != len(set(ids)):
            raise SceneError("stair ids must be unique")
        if self.player_start is not None and not isinstance(self.player_start, PlayerStart):
            raise SceneError("player_start must be PlayerStart or None")
        if _integer("revision", self.revision) < 0:
            raise SceneError("revision must be non-negative")


@dataclass(frozen=True)
class SceneChange:
    """One preconditioned stair or player-start change.

    ``expected`` is compared to the current complete value (including None).
    Use operation ``set_stair`` with an absent expected value to insert, or
    ``remove_stair`` with the current value to remove. Player-start changes use
    ``set_player_start`` and the same exact-value precondition.
    """

    operation: Literal["set_stair", "remove_stair", "set_player_start"]
    object_id: str
    expected: StairSpec | PlayerStart | None
    value: StairSpec | PlayerStart | None = None


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
    stairs = {stair.id: stair for stair in scene.stairs}
    player_start = scene.player_start
    changed: list[str] = []
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, SceneChange):
            raise SceneError("changes must contain SceneChange values")
        if change.object_id in seen:
            raise SceneError(f"duplicate change for {change.object_id!r}")
        seen.add(change.object_id)
        if change.operation == "set_stair":
            if not isinstance(change.value, StairSpec) or change.value.id != change.object_id:
                raise SceneError("set_stair value must be a StairSpec with matching object_id")
            current = stairs.get(change.object_id)
            if current != change.expected:
                raise SceneError(f"stair precondition failed for {change.object_id!r}")
            stairs[change.object_id] = change.value
        elif change.operation == "remove_stair":
            current = stairs.get(change.object_id)
            if current is None or current != change.expected or change.value is not None:
                raise SceneError(f"stair precondition failed for {change.object_id!r}")
            del stairs[change.object_id]
        elif change.operation == "set_player_start":
            if change.object_id != "player_start" or (change.value is not None and not isinstance(change.value, PlayerStart)):
                raise SceneError("player-start change must use id 'player_start' and a PlayerStart or None value")
            if player_start != change.expected:
                raise SceneError("player-start precondition failed")
            player_start = change.value
        else:
            raise SceneError(f"unsupported operation {change.operation!r}")
        changed.append(change.object_id)

    candidate = replace(scene, stairs=tuple(sorted(stairs.values(), key=lambda item: item.id)),
                        player_start=player_start,
                        revision=scene.revision if dry_run else scene.revision + 1)
    rollback = None if dry_run else Rollback(candidate.revision, scene)
    return TransactionResult(candidate, tuple(changed), not dry_run, rollback)


def rollback_changes(scene: LevelScene, rollback: Rollback) -> LevelScene:
    """Restore the prior content if the committed revision is still current."""
    if scene.revision != rollback.expected_revision:
        raise SceneError(f"rollback precondition failed: expected revision {rollback.expected_revision}, found {scene.revision}")
    return replace(rollback.previous, revision=scene.revision + 1)
