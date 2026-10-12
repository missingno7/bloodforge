from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from bloodforge.cli import _resolve_camera_refs
from bloodforge.compiler import compile_scene
from bloodforge.native import encode_map
from bloodforge.scene import (AreaSpec, ConnectionSpec, LandingSpec, LevelScene, PlayerStart,
                              PortalEndpoint, SceneChange, StairSpec, WallMaterial)
from bloodforge.store import database
from bloodforge.workspace import (apply_scene_changes, create_scene, diff_scene, inspect_scene,
                                  scene_change_from_dict, scene_from_dict, scene_to_dict, undo_scene)


def scene():
    stair = StairSpec("stairs", 3, 1024, 512, 1024, 0, 0, 0, -16384, "east", 20, 454, 68)
    room = AreaSpec("entry", -1536, 0, 1024, 1024, 0, -16384)
    connection = ConnectionSpec("entry-door", PortalEndpoint("entry", "east"),
                                PortalEndpoint("bottom", "west"))
    return LevelScene((stair,), PlayerStart(-1024, 512, -5120, area_id="entry"),
                      landings=(LandingSpec("bottom", "stairs", "bottom", 512),
                                LandingSpec("top", "stairs", "top", 512)),
                      areas=(room,), connections=(connection,))


class AreaWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_context = database(Path(self.temp.name) / "workspace.db", workspace=True)
        self.db = self.db_context.__enter__()
        self.addCleanup(self.db_context.__exit__, None, None, None)
        self.original = scene()
        create_scene(self.db, "connected", "Connected stairs", self.original)

    def test_json_roundtrip_and_legacy_document(self):
        encoded = json.loads(json.dumps(scene_to_dict(self.original)))
        self.assertEqual(scene_from_dict(encoded), self.original)
        legacy = {"stairs": [asdict(self.original.stairs[0])], "player_start": None, "revision": 0}
        decoded = scene_from_dict(legacy)
        self.assertEqual(decoded.areas, ())
        self.assertEqual(decoded.landings, ())
        self.assertEqual(decoded.connections, ())
        self.assertEqual(len(compile_scene(decoded).native_map.sectors), 3)

    def test_area_edit_dry_run_commit_and_undo_preserve_all_collections(self):
        room = self.original.areas[0]
        changed = replace(room, appearance=replace(room.appearance, north=WallMaterial(tile=110, shade=12)))
        change = scene_change_from_dict({"operation": "set_area", "object_id": room.id,
                                         "expected": asdict(room), "value": asdict(changed)})
        dry = apply_scene_changes(self.db, "connected", (change,), expected_revision=0, dry_run=True)
        self.assertEqual(dry.changed_ids, ("entry",))
        self.assertEqual(inspect_scene(self.db, "connected"), self.original)
        committed = apply_scene_changes(self.db, "connected", (change,), expected_revision=0).scene
        self.assertEqual(diff_scene(self.original, committed), ("area/entry",))
        self.assertEqual(committed.stairs, self.original.stairs)
        self.assertEqual(committed.landings, self.original.landings)
        self.assertEqual(committed.connections, self.original.connections)
        before = compile_scene(self.original)
        after = compile_scene(committed)
        room_sector = before.object_map["area/entry/sector"][1]
        selected = before.native_map.sectors[room_sector]
        affected = range(selected.wallptr, selected.wallptr + selected.wallnum)
        self.assertEqual(before.native_map.sectors, after.native_map.sectors)
        self.assertTrue(all(a == b for i, (a, b) in enumerate(zip(before.native_map.walls, after.native_map.walls)) if i not in affected))
        restored = undo_scene(self.db, "connected", expected_revision=1)
        self.assertEqual(restored, replace(self.original, revision=2))

    def test_geometry_and_dangling_reference_fail_atomically(self):
        room = self.original.areas[0]
        for change in (SceneChange("set_area", room.id, room, replace(room, origin_x=-2048)),
                       SceneChange("remove_landing", "bottom", self.original.landings[0])):
            with self.assertRaises(ValueError):
                apply_scene_changes(self.db, "connected", (change,), expected_revision=0)
            self.assertEqual(inspect_scene(self.db, "connected"), self.original)
            self.assertEqual(self.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)

    def test_atomic_add_and_remove_of_connected_area(self):
        area = AreaSpec("side-room", -1536, -1024, 1024, 1024, 0, -16384)
        join = ConnectionSpec("side-door", PortalEndpoint("entry", "north"), PortalEndpoint("side-room", "south"))
        changes = tuple(scene_change_from_dict({"operation": op, "object_id": item.id,
                                                "expected": None, "value": asdict(item)})
                        for op, item in (("set_connection", join), ("set_area", area)))
        committed = apply_scene_changes(self.db, "connected", changes, expected_revision=0).scene
        self.assertEqual(diff_scene(self.original, committed), ("area/side-room", "connection/side-door"))
        removed = apply_scene_changes(self.db, "connected", (
            SceneChange("remove_area", area.id, area), SceneChange("remove_connection", join.id, join)), expected_revision=1)
        self.assertEqual(diff_scene(self.original, removed.scene), ())

    def test_semantic_cameras_are_bound_to_exact_scene_output(self):
        encoded = encode_map(compile_scene(self.original).native_map)
        camera = {"id": "entry", "x": -1024, "y": 512, "z": -5120,
                  "sector_object_id": "area/entry/sector"}
        resolved = _resolve_camera_refs(encoded, [camera], self.original)
        self.assertNotIn("sector_object_id", resolved[0])
        self.assertEqual(resolved[0]["sector"], compile_scene(self.original).object_map["area/entry/sector"][1])
        with self.assertRaisesRegex(ValueError, "current compiled revision"):
            _resolve_camera_refs(encoded[:-1] + bytes([encoded[-1] ^ 1]), [camera], self.original)
        with self.assertRaisesRegex(ValueError, "unknown semantic sector"):
            _resolve_camera_refs(encoded, [camera | {"sector_object_id": "area/missing/sector"}], self.original)
        with self.assertRaisesRegex(ValueError, "either"):
            _resolve_camera_refs(encoded, [camera | {"sector": 0}], self.original)


if __name__ == "__main__":
    unittest.main()
