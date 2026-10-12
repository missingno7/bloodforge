"""Bounded, auditable edits to native Blood map records.

These operations change a supplied in-memory map copy. They do not persist or
publish a variant, assert runtime behavior, or modify a corpus source.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from typing import Any

from .native import NativeMap, Sector, Wall


_SHADE_MIN, _SHADE_MAX = -128, 127
_INT32_MIN, _INT32_MAX = -(1 << 31), (1 << 31) - 1
_WALL_BLOCKING = 1
_SLOPE_BIT = 2


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{name} must be in {low}..{high}")
    return value


def _wall_owner(level: NativeMap) -> list[int | None]:
    owners: list[int | None] = [None] * len(level.walls)
    for sector_index, sector in enumerate(level.sectors):
        start, count = sector.wallptr, sector.wallnum
        if (isinstance(start, bool) or isinstance(count, bool)
                or not isinstance(start, int) or not isinstance(count, int)
                or start < 0 or count < 0 or start + count > len(level.walls)):
            continue
        for index in range(start, start + count):
            if owners[index] is not None:
                owners[index] = -1
            else:
                owners[index] = sector_index
    return owners


def _reciprocal_pair(level: NativeMap, wall_index: int) -> tuple[int, int]:
    wall = level.walls[wall_index]
    other_index = wall.nextwall
    if (isinstance(other_index, bool) or not isinstance(other_index, int)
            or not 0 <= other_index < len(level.walls) or other_index == wall_index):
        raise ValueError(f"wall {wall_index} does not name a valid portal partner")
    other = level.walls[other_index]
    if other.nextwall != wall_index:
        raise ValueError(f"wall {wall_index} portal is not reciprocal")
    if (isinstance(wall.nextsector, bool) or not isinstance(wall.nextsector, int)
            or not 0 <= wall.nextsector < len(level.sectors)
            or isinstance(other.nextsector, bool) or not isinstance(other.nextsector, int)
            or not 0 <= other.nextsector < len(level.sectors)):
        raise ValueError(f"wall {wall_index} portal has an invalid sector reference")
    owners = _wall_owner(level)
    owner, other_owner = owners[wall_index], owners[other_index]
    if owner is None or owner == -1 or other_owner is None or other_owner == -1:
        raise ValueError(f"wall {wall_index} or its partner has ambiguous sector ownership")
    if wall.nextsector != other_owner or other.nextsector != owner:
        raise ValueError(f"wall {wall_index} portal sector references are not reciprocal")
    if (not 0 <= wall.point2 < len(level.walls)
            or not 0 <= other.point2 < len(level.walls)):
        raise ValueError(f"wall {wall_index} portal endpoints are out of range")
    wall_end = level.walls[wall.point2]
    other_end = level.walls[other.point2]
    if (wall.x, wall.y) != (other_end.x, other_end.y) or (wall_end.x, wall_end.y) != (other.x, other.y):
        raise ValueError(f"wall {wall_index} portal endpoints are not reversed")
    return wall_index, other_index


def _check_expect(record: object, expect: Any, allowed: set[str], label: str) -> None:
    if expect is None:
        return
    if not isinstance(expect, dict) or not expect:
        raise ValueError("expect must be a non-empty field-to-value mapping")
    valid_fields = {item.name for item in fields(record)}
    for name, expected in expect.items():
        if name not in allowed or name not in valid_fields:
            raise ValueError(f"unsupported {label} precondition field {name!r}")
        actual = getattr(record, name)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"precondition failed for {label}.{name}: expected {expected!r}, found {actual!r}")


def _record_changes(before: object, after: object, allowed: set[str]) -> dict[str, dict[str, Any]]:
    changed: dict[str, dict[str, Any]] = {}
    for item in fields(before):
        name = item.name
        old, new = getattr(before, name), getattr(after, name)
        if old != new:
            if name not in allowed:
                raise AssertionError(f"spatial edit unexpectedly changed {name}")
            changed[name] = {"before": old, "after": new}
    return changed


def intervene(level: NativeMap, spec: dict[str, Any]) -> tuple[NativeMap, dict[str, Any]]:
    """Apply one supported static edit to a deep copy and return its audit.

    ``spec`` has a ``kind`` and integer ``wall``/``sector`` target. Optional
    ``expect`` values form an optimistic precondition over allowed fields of
    the target record. Supported kinds are ``close_portal``, ``block_portal``,
    ``wall_shade``, and ``sector_ceiling``.
    """
    if not isinstance(level, NativeMap):
        raise ValueError("level must be a NativeMap")
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    if any(not isinstance(key, str) for key in spec):
        raise ValueError("spec field names must be strings")
    kind = spec.get("kind")
    if not isinstance(kind, str) or kind not in {"close_portal", "block_portal", "wall_shade", "sector_ceiling"}:
        raise ValueError(f"unsupported spatial edit kind {kind!r}")
    if "expect" in spec and spec["expect"] is None:
        raise ValueError("expect must be a non-empty field-to-value mapping when provided")

    target_name = "sector" if kind == "sector_ceiling" else "wall"
    target_index = _int(spec.get(target_name), target_name, 0,
                        len(level.sectors if target_name == "sector" else level.walls) - 1)
    allowed_spec = {"kind", target_name, "expect"}
    if kind == "wall_shade":
        allowed_spec.add("delta")
    elif kind == "sector_ceiling":
        allowed_spec.add("z")
    extras = set(spec) - allowed_spec
    if extras:
        raise ValueError(f"unsupported spatial edit fields: {sorted(extras)!r}")

    original = deepcopy(level)
    result = deepcopy(level)
    fields_by_record: dict[int, dict[str, dict[str, Any]]] = {}
    record_kind = target_name

    if target_name == "wall":
        record = result.walls[target_index]
        expect_fields = {"nextwall", "nextsector", "cstat", "shade"}
        _check_expect(record, spec.get("expect"), expect_fields, "wall")
        if kind in {"close_portal", "block_portal"}:
            pair = _reciprocal_pair(result, target_index)
            for index in pair:
                wall = result.walls[index]
                before = deepcopy(wall)
                if kind == "close_portal":
                    wall.nextwall = -1
                    wall.nextsector = -1
                    allowed = {"nextwall", "nextsector"}
                else:
                    wall.cstat |= _WALL_BLOCKING
                    allowed = {"cstat"}
                fields_by_record[index] = _record_changes(before, wall, allowed)
            changed_indices = pair
            topology_preserved = kind == "block_portal"
        else:
            delta = _int(spec.get("delta"), "delta", _INT32_MIN, _INT32_MAX)
            _int(record.shade, "wall shade", _SHADE_MIN, _SHADE_MAX)
            before = deepcopy(record)
            record.shade = max(_SHADE_MIN, min(_SHADE_MAX, record.shade + delta))
            fields_by_record[target_index] = _record_changes(before, record, {"shade"})
            changed_indices = (target_index,)
            topology_preserved = True
    else:
        record = result.sectors[target_index]
        _check_expect(record, spec.get("expect"), {"ceilingz", "floorz", "ceilingstat", "floorstat"}, "sector")
        z = _int(spec.get("z"), "z", _INT32_MIN, _INT32_MAX)
        ceiling_is_sloped = bool(record.ceilingstat & _SLOPE_BIT and record.ceilingheinum != 0)
        floor_is_sloped = bool(record.floorstat & _SLOPE_BIT and record.floorheinum != 0)
        if ceiling_is_sloped or floor_is_sloped:
            raise ValueError("sector_ceiling requires a flat target sector")
        if z >= record.floorz:
            raise ValueError("new ceiling z must be numerically less than the sector floor")
        if target_index == result.start_sector and z > result.start_z:
            raise ValueError("new ceiling would place the player start outside the sector")
        before = deepcopy(record)
        record.ceilingz = z
        fields_by_record[target_index] = _record_changes(before, record, {"ceilingz"})
        changed_indices = (target_index,)
        topology_preserved = True

    changed_fields = sorted({name for change in fields_by_record.values() for name in change})
    changed_wall_xy_preserved = all(
        (before.x, before.y) == (after.x, after.y)
        for before, after in zip(original.walls, result.walls)
    )
    records_unchanged = (
        len(original.sectors) == len(result.sectors)
        and len(original.walls) == len(result.walls)
        and len(original.sprites) == len(result.sprites)
        and all(a == b for i, (a, b) in enumerate(zip(original.sectors, result.sectors))
                if not (record_kind == "sector" and i == target_index))
        and all(a == b for i, (a, b) in enumerate(zip(original.walls, result.walls))
                if not (record_kind == "wall" and i in changed_indices))
        and all(a == b for a, b in zip(original.sprites, result.sprites))
    )
    header_fields = ("start_x", "start_y", "start_z", "start_angle", "start_sector", "version",
                     "visibility", "sky_type", "revision", "sky_bits", "sky_offsets", "matt_id",
                     "raw_extra_header")
    header_preserved = all(getattr(original, name) == getattr(result, name) for name in header_fields)
    only_declared_fields_changed = all(
        set(change).issubset({"nextwall", "nextsector"} if kind == "close_portal"
                             else {"cstat"} if kind == "block_portal"
                             else {"shade"} if kind == "wall_shade" else {"ceilingz"})
        for change in fields_by_record.values()
    )
    invariants = {
        "record_counts_preserved": (len(original.sectors), len(original.walls), len(original.sprites))
                                    == (len(result.sectors), len(result.walls), len(result.sprites)),
        "unaffected_records_preserved": records_unchanged,
        "header_preserved": header_preserved,
        "wall_xy_preserved": changed_wall_xy_preserved,
        "only_declared_fields_changed": only_declared_fields_changed,
        "portal_graph_preserved": topology_preserved,
        "input_preserved": level == original,
    }
    changes = []
    for index in changed_indices:
        if fields_by_record[index]:
            changes.append({"record": record_kind, "index": index,
                            "changed_fields": fields_by_record[index]})
    audit = {
        "kind": kind,
        "target": {target_name: target_index},
        "parameters": ({"delta": spec["delta"]} if kind == "wall_shade"
                       else {"z": spec["z"]} if kind == "sector_ceiling" else {}),
        "changes": changes,
        "changed_fields": changed_fields,
        "invariants": invariants,
        "runtime_behavior_verified": False,
        "scope_limits": ["static record transformation only",
                         "runtime traversal and gameplay behavior are not verified",
                         "opaque dynamic mechanisms are not interpreted"],
    }
    return result, audit
