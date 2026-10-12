import tempfile
import unittest
from pathlib import Path

from bloodforge.scene import LevelScene, PlayerStart, SceneChange, SceneError, StairSpec
from bloodforge.store import database
from bloodforge.workspace import (
    WorkspaceError,
    apply_scene_changes,
    create_scene,
    diff_scene,
    inspect_scene,
    scene_from_dict,
    scene_to_dict,
    undo_scene,
)


def stair(**updates):
    values = dict(id="stairs", count=3, width=1024, tread=512, rise=256,
                  origin_x=100, origin_y=200, floor_z=8192, ceiling_z=0,
                  direction="east", floor_tile=10, ceiling_tile=11, wall_tile=12)
    values.update(updates)
    return StairSpec(**values)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_context = database(Path(self.temp.name) / "workspace.db", workspace=True)
        self.db = self.db_context.__enter__()
        self.initial = LevelScene((stair(),), PlayerStart(356, 712, 4096, stair_id="stairs"))
        create_scene(self.db, "demo", "Demo", self.initial)

    def tearDown(self):
        self.db_context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_scene_document_roundtrip_and_diff_semantic_ids(self):
        self.assertEqual(scene_from_dict(scene_to_dict(self.initial)), self.initial)
        changed = LevelScene((stair(count=4),), PlayerStart(356, 712, 4096, stair_id="stairs"), 1)
        self.assertEqual(diff_scene(self.initial, changed), ("stair/stairs",))
        changed_start = LevelScene((stair(),), PlayerStart(400, 700, 4096, stair_id="stairs"), 1)
        self.assertEqual(diff_scene(self.initial, changed_start), ("player_start",))

    def test_dry_run_does_not_write_revision(self):
        change = SceneChange("set_stair", "stairs", stair(), stair(count=4))
        result = apply_scene_changes(self.db, "demo", (change,), expected_revision=0, dry_run=True)
        self.assertFalse(result.committed)
        self.assertEqual(result.scene.revision, 0)
        self.assertEqual(result.scene.stairs[0].count, 4)
        self.assertEqual(inspect_scene(self.db, "demo"), self.initial)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0], 1)

    def test_valid_change_and_undo_append_monotonic_revisions(self):
        change = SceneChange("set_stair", "stairs", stair(), stair(count=4))
        result = apply_scene_changes(self.db, "demo", (change,), expected_revision=0)
        self.assertTrue(result.committed)
        self.assertEqual(result.scene.revision, 1)
        restored = undo_scene(self.db, "demo", expected_revision=1)
        self.assertEqual(restored.revision, 2)
        self.assertEqual(restored.stairs, self.initial.stairs)
        self.assertEqual(inspect_scene(self.db, "demo"), restored)
        rows = self.db.execute("SELECT revision,parent_revision FROM revisions ORDER BY revision").fetchall()
        self.assertEqual([(row[0], row[1]) for row in rows], [(0, None), (1, 0), (2, 1)])

    def test_invalid_compilation_rolls_back_revision_atomically(self):
        invalid = stair(ceiling_z=8192)
        change = SceneChange("set_stair", "stairs", stair(), invalid)
        with self.assertRaises(ValueError):
            apply_scene_changes(self.db, "demo", (change,), expected_revision=0)
        self.assertEqual(inspect_scene(self.db, "demo"), self.initial)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0], 1)

    def test_stale_revision_and_undo_conflicts_are_rejected(self):
        change = SceneChange("set_stair", "stairs", stair(), stair(count=4))
        apply_scene_changes(self.db, "demo", (change,), expected_revision=0)
        with self.assertRaises(SceneError):
            apply_scene_changes(self.db, "demo", (change,), expected_revision=0)
        with self.assertRaises(SceneError):
            undo_scene(self.db, "demo", expected_revision=0)
        self.assertEqual(inspect_scene(self.db, "demo").revision, 1)

    def test_scene_creation_rejects_duplicate_project_and_invalid_document(self):
        with self.assertRaises(WorkspaceError):
            create_scene(self.db, "demo", "Demo", self.initial)
        malformed = {"revision": 0, "stairs": [{}], "player_start": None}
        with self.assertRaises(WorkspaceError):
            scene_from_dict(malformed)


if __name__ == "__main__":
    unittest.main()
