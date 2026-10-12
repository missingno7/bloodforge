"""SQLite persistence for semantic LevelScene source revisions."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import sqlite3
from typing import Any

from .compiler import compile_scene
from .scene import (
    AreaSpec,
    ConnectionSpec,
    LandingSpec,
    LevelScene,
    PlaneMaterial,
    PlayerStart,
    PortalEndpoint,
    SceneChange,
    SceneError,
    StairSpec,
    StepAppearance,
    TransactionResult,
    WallMaterial,
    apply_changes,
)
from .store import canonical_json


class WorkspaceError(ValueError):
    """A scene project or persisted revision violates workspace preconditions."""


def scene_to_dict(scene: LevelScene) -> dict[str, Any]:
    """Return the stable JSON-compatible representation of a semantic scene."""
    return {
        "revision": scene.revision,
        "stairs": [asdict(stair) for stair in scene.stairs],
        "landings": [asdict(landing) for landing in scene.landings],
        "areas": [asdict(area) for area in scene.areas],
        "connections": [asdict(connection) for connection in scene.connections],
        "player_start": asdict(scene.player_start) if scene.player_start is not None else None,
    }


def scene_from_dict(value: object) -> LevelScene:
    """Build a validated scene from its persisted source document."""
    required = {"revision", "stairs", "player_start"}
    optional = {"landings", "areas", "connections"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise WorkspaceError("scene document requires revision, stairs, player_start; optional landings, areas, connections")
    for key in ("stairs", "landings", "areas", "connections"):
        if not isinstance(value.get(key, []), list):
            raise WorkspaceError(f"scene {key} must be a list")
    try:
        stairs = tuple(stair_from_dict(item) if isinstance(item, dict) else _bad_stair()
                       for item in value["stairs"])
        player_value = value["player_start"]
        player = PlayerStart(**player_value) if isinstance(player_value, dict) else None
        if player_value is not None and player is None:
            raise WorkspaceError("player_start must be an object or null")
        return LevelScene(stairs=stairs, player_start=player, revision=value["revision"],
                          landings=tuple(landing_from_dict(item) for item in value.get("landings", [])),
                          areas=tuple(area_from_dict(item) for item in value.get("areas", [])),
                          connections=tuple(connection_from_dict(item) for item in value.get("connections", [])))
    except (TypeError, SceneError) as exc:
        raise WorkspaceError(f"invalid scene document: {exc}") from exc


def _bad_stair():
    raise WorkspaceError("each stair must be an object")


def appearance_from_dict(value: object) -> StepAppearance:
    """Decode a rectangle's appearance, retaining typed defaults for omitted surfaces."""
    if not isinstance(value, dict):
        raise WorkspaceError("appearance must be an object")
    fields = dict(value)
    for key, cls in (("floor", PlaneMaterial), ("ceiling", PlaneMaterial),
                     ("north", WallMaterial), ("east", WallMaterial),
                     ("south", WallMaterial), ("west", WallMaterial)):
        if key in fields:
            if not isinstance(fields[key], dict):
                raise WorkspaceError(f"{key} material must be an object")
            fields[key] = cls(**fields[key])
    return StepAppearance(**fields)


def area_from_dict(value: object) -> AreaSpec:
    if not isinstance(value, dict):
        raise WorkspaceError("each area must be an object")
    fields = dict(value)
    if "appearance" in fields:
        fields["appearance"] = appearance_from_dict(fields["appearance"])
    return AreaSpec(**fields)


def landing_from_dict(value: object) -> LandingSpec:
    if not isinstance(value, dict):
        raise WorkspaceError("each landing must be an object")
    fields = dict(value)
    if fields.get("appearance") is not None:
        fields["appearance"] = appearance_from_dict(fields["appearance"])
    return LandingSpec(**fields)


def connection_from_dict(value: object) -> ConnectionSpec:
    if not isinstance(value, dict):
        raise WorkspaceError("each connection must be an object")
    fields = dict(value)
    for endpoint in ("a", "b"):
        if not isinstance(fields.get(endpoint), dict):
            raise WorkspaceError("connections require a and b endpoint objects")
        fields[endpoint] = PortalEndpoint(**fields[endpoint])
    return ConnectionSpec(**fields)


def stair_from_dict(value: dict[str, Any]) -> StairSpec:
    """Decode both legacy stairs and JSON scene stairs with appearance data."""
    fields = dict(value)
    appearance = fields.get("appearance")
    if appearance is not None:
        if not isinstance(appearance, (list, tuple)):
            raise WorkspaceError("stair appearance must be a list, tuple, or null")
        fields["appearance"] = tuple(appearance_from_dict(item) for item in appearance)
    return StairSpec(**fields)


def scene_change_from_dict(value: object) -> SceneChange:
    """Decode CLI/workspace change JSON, including nested material values."""
    if not isinstance(value, dict):
        raise WorkspaceError("scene change must be an object")
    operation = value.get("operation")
    object_id = value.get("object_id")
    if not isinstance(operation, str) or not isinstance(object_id, str):
        raise WorkspaceError("scene change requires operation and object_id strings")

    decoders = {"set_stair": stair_from_dict, "remove_stair": stair_from_dict,
                "set_area": area_from_dict, "remove_area": area_from_dict,
                "set_landing": landing_from_dict, "remove_landing": landing_from_dict,
                "set_connection": connection_from_dict, "remove_connection": connection_from_dict,
                "set_player_start": lambda part: PlayerStart(**part)}
    if operation not in decoders:
        raise WorkspaceError(f"unsupported scene operation {operation!r}")

    def decode(part: object):
        if part is None:
            return None
        if not isinstance(part, dict):
            raise WorkspaceError("scene change values must be objects or null")
        return decoders[operation](part)

    try:
        return SceneChange(operation, object_id, decode(value.get("expected")),
                           decode(value.get("value")))
    except (TypeError, SceneError) as exc:
        raise WorkspaceError(f"invalid scene change: {exc}") from exc


def _load(db: sqlite3.Connection, project_id: str, revision: int | None = None) -> LevelScene:
    if revision is None:
        row = db.execute("SELECT revision,scene_json FROM revisions WHERE project_id=? ORDER BY revision DESC LIMIT 1",
                         (project_id,)).fetchone()
    else:
        row = db.execute("SELECT revision,scene_json FROM revisions WHERE project_id=? AND revision=?",
                         (project_id, revision)).fetchone()
    if row is None:
        raise WorkspaceError(f"scene project {project_id!r} has no requested revision")
    try:
        scene = scene_from_dict(json.loads(row["scene_json"]))
    except (json.JSONDecodeError, WorkspaceError) as exc:
        raise WorkspaceError(f"stored scene revision is invalid: {exc}") from exc
    if scene.revision != row["revision"]:
        raise WorkspaceError("stored scene revision number does not match its revision row")
    return scene


def _append_revision(db: sqlite3.Connection, project_id: str, scene: LevelScene,
                     parent_revision: int | None, operation: object) -> None:
    db.execute("INSERT INTO revisions(project_id,revision,parent_revision,scene_json,operation_json) VALUES(?,?,?,?,?)",
               (project_id, scene.revision, parent_revision,
                canonical_json(scene_to_dict(scene)), canonical_json(operation)))


def create_scene(db: sqlite3.Connection, project_id: str, name: str,
                 scene: LevelScene) -> LevelScene:
    """Create one scene project at revision zero after compiling it successfully."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise WorkspaceError("project_id must be a non-empty string")
    if not isinstance(name, str) or not name.strip():
        raise WorkspaceError("name must be a non-empty string")
    if not isinstance(scene, LevelScene) or scene.revision != 0:
        raise WorkspaceError("new scene must be a LevelScene at revision zero")
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
        if existing is not None:
            count = db.execute("SELECT COUNT(*) FROM revisions WHERE project_id=?", (project_id,)).fetchone()[0]
            if count:
                raise WorkspaceError(f"scene project {project_id!r} already has revisions")
            db.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))
        else:
            db.execute("INSERT INTO projects(id,name) VALUES(?,?)", (project_id, name))
        compile_scene(scene)
        _append_revision(db, project_id, scene, None, {"operation": "create_scene"})
        db.commit()
        return scene
    except BaseException:
        db.rollback()
        raise


def inspect_scene(db: sqlite3.Connection, project_id: str) -> LevelScene:
    """Load and validate the latest persisted scene source revision."""
    return _load(db, project_id)


def diff_scene(before: LevelScene, after: LevelScene) -> tuple[str, ...]:
    """Return sorted semantic IDs whose source values differ."""
    before_stairs = {stair.id: stair for stair in before.stairs}
    after_stairs = {stair.id: stair for stair in after.stairs}
    changed = [f"stair/{stair_id}" for stair_id in before_stairs.keys() | after_stairs.keys()
               if before_stairs.get(stair_id) != after_stairs.get(stair_id)]
    for kind, attribute in (("area", "areas"), ("landing", "landings"), ("connection", "connections")):
        first = {item.id: item for item in getattr(before, attribute)}
        second = {item.id: item for item in getattr(after, attribute)}
        changed.extend(f"{kind}/{key}" for key in first.keys() | second.keys() if first.get(key) != second.get(key))
    if before.player_start != after.player_start:
        changed.append("player_start")
    return tuple(sorted(changed))


def _change_json(change: SceneChange) -> dict[str, Any]:
    return {
        "operation": change.operation,
        "object_id": change.object_id,
        "expected": asdict(change.expected) if change.expected is not None else None,
        "value": asdict(change.value) if change.value is not None else None,
    }


def apply_scene_changes(db: sqlite3.Connection, project_id: str,
                        changes: tuple[SceneChange, ...], *, expected_revision: int,
                        dry_run: bool = False) -> TransactionResult:
    """Apply one atomic, preconditioned revision and validate it before commit."""
    db.execute("BEGIN IMMEDIATE")
    try:
        current = _load(db, project_id)
        if current.revision != expected_revision:
            raise SceneError(f"revision precondition failed: expected {expected_revision}, found {current.revision}")
        proposed = apply_changes(current, changes, expected_revision=expected_revision,
                                 dry_run=dry_run)
        compile_scene(proposed.scene)
        if dry_run:
            db.rollback()
            return proposed
        _append_revision(db, project_id, proposed.scene, current.revision,
                         {"operation": "apply_changes", "changes": [_change_json(change) for change in changes]})
        db.commit()
        return proposed
    except BaseException:
        db.rollback()
        raise


def undo_scene(db: sqlite3.Connection, project_id: str, *, expected_revision: int) -> LevelScene:
    """Append a new revision containing the prior revision's scene content."""
    db.execute("BEGIN IMMEDIATE")
    try:
        current = _load(db, project_id)
        if current.revision != expected_revision:
            raise SceneError(f"revision precondition failed: expected {expected_revision}, found {current.revision}")
        if current.revision == 0:
            raise WorkspaceError("revision zero has no prior scene content to undo")
        previous = _load(db, project_id, current.revision - 1)
        restored = replace(previous, revision=current.revision + 1)
        compile_scene(restored)
        _append_revision(db, project_id, restored, current.revision,
                         {"operation": "undo", "undid_revision": current.revision})
        db.commit()
        return restored
    except BaseException:
        db.rollback()
        raise
