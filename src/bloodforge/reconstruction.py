"""Fitted surface reconstruction and source-held-fixed diagnostic benchmarks.

Geometry comes from the trusted recipe and its measured instance parameters.
Surface layouts are explicitly fitted from the selected source; they are not
evidence of a learned material grammar or an independently designed new level.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .compiler import compile_scene
from .config import Config
from .fidelity import compare_stair
from .fixture import graft_stair
from .learning import eligible_contents
from .native import NativeMap, encode_map, parse_map
from .scene import LevelScene, PlaneMaterial, StairSpec, StepAppearance, WallMaterial
from .store import artifact_put, canonical_json, database, digest
from .validation import validate_map
from .workspace import create_scene, scene_to_dict


VERSION = "fitted-stair-context-1"


def measure_appearance(source: NativeMap, indices: list[int]) -> tuple[StepAppearance, ...]:
    """Measure directed cardinal wall materials and the actual first-wall anchor.

    Fail on non-rectangles, reversed winding, slopes, or unsupported wall flags;
    never silently default a material or strip a gameplay flag.
    """
    if not indices or len(indices) != len(set(indices)):
        raise ValueError("appearance needs nonempty unique source sectors")
    measured = []
    for index in indices:
        if type(index) is not int or not 0 <= index < len(source.sectors):
            raise ValueError("appearance source sector is out of bounds")
        sector = source.sectors[index]
        if sector.wallnum != 4 or not 0 <= sector.wallptr <= len(source.walls) - 4:
            raise ValueError("appearance requires four-wall sectors")
        sides = {}
        allowed = set(range(sector.wallptr, sector.wallptr + 4))
        cursor, visited, points = sector.wallptr, [], []
        first_side = None
        for _ in range(4):
            if cursor not in allowed or cursor in visited:
                raise ValueError("appearance requires one complete wall loop")
            visited.append(cursor)
            wall = source.walls[cursor]
            if wall.point2 not in allowed:
                raise ValueError("appearance wall endpoint leaves its sector")
            end = source.walls[wall.point2]
            dx, dy = end.x - wall.x, end.y - wall.y
            side = ("north" if dx > 0 else "south") if dy == 0 and dx else (
                ("east" if dy > 0 else "west") if dx == 0 and dy else None)
            if side is None or side in sides:
                raise ValueError("appearance requires cardinal rectangle sides")
            if first_side is None:
                first_side = side
            sides[side] = WallMaterial(tile=wall.picnum, overlay=wall.overpicnum,
                                      shade=wall.shade, palette=wall.pal,
                                      xrepeat=wall.xrepeat, yrepeat=wall.yrepeat,
                                      xpanning=wall.xpanning, ypanning=wall.ypanning, flags=wall.cstat)
            points.append((wall.x, wall.y))
            cursor = wall.point2
        if cursor != sector.wallptr:
            raise ValueError("appearance requires one complete wall loop")
        area_twice = sum(x * y1 - x1 * y for (x, y), (x1, y1) in zip(points, points[1:] + points[:1]))
        if area_twice <= 0:
            raise ValueError("appearance requires the compiler's rectangle winding")

        def plane(prefix):
            return PlaneMaterial(tile=getattr(sector, prefix + "picnum"),
                                 shade=getattr(sector, prefix + "shade"),
                                 palette=getattr(sector, prefix + "pal"),
                                 xpanning=getattr(sector, prefix + "xpanning"),
                                 ypanning=getattr(sector, prefix + "ypanning"),
                                 stat=getattr(sector, prefix + "stat"))
        measured.append(StepAppearance(floor=plane("floor"), ceiling=plane("ceiling"),
                                       **sides, visibility=sector.visibility, first_wall=first_side))
    return tuple(measured)


def reconstruct_fitted(config: Config, recipe_id: str, pattern_id: str, *, with_source_context: bool = False) -> dict:
    """Reconstruct one frozen held-out instance with explicitly fitted materials.

    The baseline benchmark stays immutable. This diagnostic records a separate
    benchmark version and never promotes a recipe to verified.
    """
    with database(config.knowledge_db) as db:
        recipe = db.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
        if recipe is None:
            raise ValueError(f"unknown recipe {recipe_id}")
        definition = json.loads(recipe["definition_json"])
        members = [m for m in definition.get("heldout_snapshot", []) if m["pattern_id"] == pattern_id]
        if len(members) != 1:
            raise ValueError("pattern must belong to the recipe's frozen held-out snapshot")
        member = members[0]
        eligible = {r["sha256"] for r in eligible_contents(db, definition["split"]["population"])}
        if member["sha256"] not in eligible:
            raise ValueError("held-out source is no longer eligible")
        pattern = db.execute("SELECT * FROM patterns WHERE id=?", (pattern_id,)).fetchone()
        if pattern is None or pattern["sha256"] != member["sha256"]:
            raise ValueError("frozen pattern source identity is unavailable or changed")
        parameters = json.loads(pattern["parameters_json"])
        outside = [key for key, (low, high) in definition["parameters"].items() if not low <= parameters[key] <= high]
        if outside or parameters["direction"] not in definition["directions"]:
            raise ValueError(f"pattern is outside the training domain: {outside or ['direction']}")
        indices = json.loads(pattern["evidence_json"])["source_sector_indices"]

    reference = member["source"]
    root = Path(reference["root"]).resolve()
    source_path = (root / reference["relative_path"]).resolve()
    if not source_path.is_relative_to(root):
        raise ValueError("source reference escapes the corpus root")
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != member["sha256"]:
        raise ValueError("source bytes differ from the frozen held-out identity")
    source = parse_map(source_bytes)
    appearance = measure_appearance(source, indices)
    spec = StairSpec(id="reconstructed-stair", **parameters,
                     floor_tile=appearance[0].floor.tile, ceiling_tile=appearance[0].ceiling.tile,
                     wall_tile=appearance[0].north.tile, appearance=appearance)
    scene = LevelScene((spec,))
    compiled = compile_scene(scene)
    payload = encode_map(compiled.native_map)
    fragment = parse_map(payload)
    fragment_sha, fragment_path = artifact_put(config.data_root, payload, ".map")
    scene_sha, scene_path = artifact_put(config.data_root, (canonical_json(scene_to_dict(scene)) + "\n").encode(), ".json")
    project_id = VERSION + "-" + digest([recipe_id, pattern_id, scene_sha])[:20]
    result = {"schema": "bloodforge.fitted-reconstruction.v1", "evaluation_version": VERSION,
              "recipe_id": recipe_id, "pattern_id": pattern_id, "status": "evaluated",
              "source": reference, "source_sectors": indices,
              "split_group": definition["split"]["groups"][member["sha256"]],
              "parameters": parameters, "project_id": project_id,
              "scene_path": str(scene_path), "scene_hash": scene_sha,
              "artifact_hash": fragment_sha, "artifact_path": str(fragment_path),
              "object_map": compiled.object_map, "validation": validate_map(fragment),
              "fidelity": compare_stair(source, indices, fragment),
              "native_roundtrip_exact": encode_map(fragment) == payload,
              "appearance_policy": "measured per-step source fit; no learned material selection claim",
              "benchmark_policy": "post-hoc diagnostic; the original held-out baseline is retained",
              "manual_coordinate_edits": 0, "promoted_to_verified": False,
              "limitations": ["The adapter was developed after examining held-out rendering failures; this is not a blinded generalization test.",
                              "Geometry is reconstructed from recipe parameters; appearance is supplied from the target source.",
                              "Standalone fragment closes external portals; optional fixture retains all source context and sprites.",
                              "No inferred context, mechanisms, progression or whole-level playability claim."],
              "visual": {"status": "not_run"}, "runtime": {"status": "not_run"}}
    maps = [(fragment_sha, str(fragment_path), "blood-map-v7")]
    if with_source_context:
        fixture = graft_stair(source, indices, fragment)
        fixture_bytes = encode_map(fixture.native_map)
        fixture_sha, fixture_path = artifact_put(config.data_root, fixture_bytes, ".map")
        result["context_fixture"] = {"artifact_hash": fixture_sha, "artifact_path": str(fixture_path),
                                     "integrity": fixture.integrity, "correspondence": fixture.correspondence,
                                     "native_roundtrip_exact": encode_map(parse_map(fixture_bytes)) == fixture_bytes,
                                     "fidelity": compare_stair(source, indices, fixture.native_map, indices),
                                     "validation": validate_map(fixture.native_map)}
        maps.append((fixture_sha, str(fixture_path), "source-context-evaluation-fixture"))
    if source_path.read_bytes() != source_bytes:
        raise ValueError("source changed during reconstruction")
    with database(config.workspace_db, workspace=True) as workspace:
        if workspace.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            create_scene(workspace, project_id, "Fitted held-out stair reconstruction", scene)
        with workspace:
            for sha, path, kind in maps:
                workspace.execute("INSERT OR IGNORE INTO outputs VALUES(?,?,?,?,?,?)", (
                    sha, project_id, 0, path, kind, canonical_json({"recipe_id": recipe_id, "pattern_id": pattern_id,
                                                                "source": reference, "evaluation_version": VERSION})))
    with database(config.knowledge_db) as db, db:
        db.execute("INSERT OR IGNORE INTO benchmarks(id,recipe_id,heldout_pattern_id,result_json,artifact_hash) VALUES(?,?,?,?,?)",
                   (digest(result), recipe_id, pattern_id, canonical_json(result), fragment_sha))
    from .evidence import record_tool_evidence
    result.update(record_tool_evidence(config, result, "fitted-reconstruction"))
    return result
