import unittest

from bloodforge.scene import (
    AreaSpec,
    ConnectionSpec,
    LandingSpec,
    LevelScene,
    PlayerStart,
    PortalEndpoint,
    SceneChange,
    SceneError,
    StairSpec,
    apply_changes,
)


def stair(stair_id="s"):
    return StairSpec(stair_id, 2, 128, 64, 32, 0, 0, 8192, 0,
                     "east", 1, 2, 3)


def area(area_id="room"):
    return AreaSpec(area_id, 0, 0, 512, 512, 8192, 0)


class SceneModelExtensionTests(unittest.TestCase):
    def test_area_and_landing_validate_dimensions_types_and_ranges(self):
        self.assertEqual(area().role, "room")
        self.assertEqual(AreaSpec("hall", 0, 0, 128, 512, 9000, 1000,
                                  role="corridor").role, "corridor")
        for kwargs in (
            {"size_x": 0}, {"size_y": True}, {"origin_x": 1 << 31},
            {"floor_z": 0, "ceiling_z": 8192}, {"role": "hall"},
        ):
            fields = dict(id="bad", origin_x=0, origin_y=0, size_x=64,
                          size_y=64, floor_z=8192, ceiling_z=0)
            fields.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(SceneError):
                AreaSpec(**fields)
        with self.assertRaises(SceneError):
            LandingSpec("land", "missing", "middle", 16)
        with self.assertRaises(SceneError):
            LandingSpec("land", "s", "top", 0)

    def test_level_scene_keeps_positional_compatibility_and_leaves_references_to_compiler(self):
        old_positional = LevelScene((stair(),), None, 7)
        self.assertEqual(old_positional.revision, 7)
        self.assertEqual(old_positional.landings, ())
        self.assertEqual(LevelScene().stairs, ())
        valid = LevelScene((stair(),), PlayerStart(10, 20, 30, 4, area_id="land"), 0,
                           landings=(LandingSpec("land", "s", "top", 32),),
                           areas=(area(),))
        self.assertEqual(valid.player_start.area_id, "land")
        unresolved_start = LevelScene((stair(),), PlayerStart(0, 0, 0, stair_id="missing"))
        self.assertEqual(unresolved_start.player_start.stair_id, "missing")
        with self.assertRaises(SceneError):
            PlayerStart(0, 0, 0, stair_id="s", area_id="room")
        # References are intentionally resolved by the compiler, so semantic
        # scenes can be assembled in batches before their final compile check.
        dangling = LevelScene(landings=(LandingSpec("land", "missing", "bottom", 32),))
        self.assertEqual(dangling.landings[0].stair_id, "missing")
        with self.assertRaises(SceneError):
            LevelScene((stair("same"),), areas=(area("same"),))
        with self.assertRaises(SceneError):
            area("player_start")
        with self.assertRaises(SceneError):
            stair("player_start")

    def test_connection_endpoint_sides_are_typed_but_references_are_compiler_owned(self):
        s = stair()
        a = area()
        landing = LandingSpec("land", "s", "top", 32)
        connection = ConnectionSpec("c", PortalEndpoint("land", "north"),
                                    PortalEndpoint("room", "east"))
        LevelScene((s,), landings=(landing,), areas=(a,), connections=(connection,))
        self.assertEqual(LevelScene((s,), connections=(ConnectionSpec(
            "bad-side", PortalEndpoint("s", "north"), PortalEndpoint("room", "east")),),
            areas=(a,)).connections[0].id, "bad-side")
        self.assertEqual(LevelScene((s,), connections=(ConnectionSpec(
            "missing", PortalEndpoint("missing", "north"), PortalEndpoint("s", "top")),)).connections[0].id,
            "missing")
        with self.assertRaises(SceneError):
            PortalEndpoint("room", "left")
        with self.assertRaises(SceneError):
            LevelScene((s,), connections=(ConnectionSpec(
                "s", PortalEndpoint("s", "top"), PortalEndpoint("s", "bottom")),))

    def test_changes_are_typed_atomic_and_final_semantic_references_survive(self):
        base = LevelScene()
        source_stair = stair()
        source_area = area()
        source_landing = LandingSpec("land", "s", "top", 32)
        source_connection = ConnectionSpec("link", PortalEndpoint("land", "north"),
                                           PortalEndpoint("room", "south"))
        changes = (
            SceneChange("set_stair", "s", None, source_stair),
            SceneChange("set_area", "room", None, source_area),
            SceneChange("set_landing", "land", None, source_landing),
            SceneChange("set_connection", "link", None, source_connection),
            SceneChange("set_player_start", "player_start", None,
                        PlayerStart(10, 10, 4096, area_id="land")),
        )
        committed = apply_changes(base, changes, expected_revision=0)
        self.assertEqual(committed.changed_ids, ("s", "room", "land", "link", "player_start"))
        self.assertEqual(committed.scene.revision, 1)
        self.assertEqual(committed.scene.landings, (source_landing,))

        # Candidate revisions are immutable and preserve unresolved references
        # for compiler-owned final geometry checks.
        dangling = apply_changes(committed.scene,
                                 (SceneChange("remove_stair", "s", source_stair),),
                                 expected_revision=1)
        self.assertEqual(dangling.scene.landings[0].stair_id, "s")
        self.assertEqual(committed.scene.stairs, (source_stair,))

        removals = (
            SceneChange("set_player_start", "player_start",
                        committed.scene.player_start, None),
            SceneChange("remove_connection", "link", source_connection),
            SceneChange("remove_landing", "land", source_landing),
            SceneChange("remove_area", "room", source_area),
            SceneChange("remove_stair", "s", source_stair),
        )
        removed = apply_changes(committed.scene, removals, expected_revision=1)
        self.assertEqual(removed.scene, LevelScene(revision=2))

    def test_expected_value_type_mismatch_is_rejected(self):
        with self.assertRaises(SceneError):
            apply_changes(LevelScene(),
                          (SceneChange("set_area", "room", stair(), area()),),
                          expected_revision=0)


if __name__ == "__main__":
    unittest.main()
