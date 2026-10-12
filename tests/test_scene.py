import unittest

from bloodforge.compiler import SceneCompileError, compile_scene
from bloodforge.native import encode_map, parse_map
from bloodforge.scene import (
    LevelScene,
    PlaneMaterial,
    PlayerStart,
    SceneChange,
    SceneError,
    StairSpec,
    StepAppearance,
    WallMaterial,
    apply_changes,
    rollback_changes,
)


def sample_stair(direction="east"):
    return StairSpec(
        id="entry",
        count=3,
        width=1024,
        tread=512,
        rise=256,
        origin_x=100,
        origin_y=200,
        floor_z=8192,
        ceiling_z=0,
        direction=direction,
        floor_tile=10,
        ceiling_tile=11,
        wall_tile=12,
    )


class SceneModelTests(unittest.TestCase):
    def test_stair_parameters_are_strictly_validated(self):
        with self.assertRaises(SceneError):
            StairSpec(**{**sample_stair().__dict__, "rise": 0})
        with self.assertRaises(SceneError):
            StairSpec(**{**sample_stair().__dict__, "floor_tile": 32768})
        with self.assertRaises(SceneError):
            StairSpec(**{**sample_stair().__dict__, "count": True})

    def test_step_appearance_requires_exact_count_and_native_visual_ranges(self):
        appearance = StepAppearance(floor=PlaneMaterial(tile=21, stat=16),
                                    north=WallMaterial(tile=31, flags=4),
                                    first_wall="east")
        self.assertEqual(appearance.floor.tile, 21)
        with self.assertRaises(SceneError):
            StairSpec(**{**sample_stair().__dict__, "appearance": (appearance,)})
        with self.assertRaises(SceneError):
            PlaneMaterial(stat=2)
        with self.assertRaises(SceneError):
            WallMaterial(flags=1)
        with self.assertRaises(SceneError):
            WallMaterial(tile=32768)

    def test_transaction_preconditions_dry_run_and_rollback(self):
        scene = LevelScene((sample_stair(),))
        updated = StairSpec(**{**sample_stair().__dict__, "count": 4})
        change = SceneChange("set_stair", "entry", sample_stair(), updated)
        dry = apply_changes(scene, (change,), expected_revision=0, dry_run=True)
        self.assertFalse(dry.committed)
        self.assertEqual(scene.stairs[0].count, 3)
        self.assertEqual(dry.scene.stairs[0].count, 4)
        self.assertEqual(dry.changed_ids, ("entry",))

        commit = apply_changes(scene, (change,), expected_revision=0)
        self.assertEqual(commit.scene.revision, 1)
        restored = rollback_changes(commit.scene, commit.rollback)
        self.assertEqual(restored.stairs[0].count, 3)
        self.assertEqual(restored.revision, 2)
        with self.assertRaises(SceneError):
            apply_changes(scene, (change,), expected_revision=1)


class SceneCompilerTests(unittest.TestCase):
    def test_east_run_compiles_to_reciprocal_portals_and_roundtrips(self):
        result = compile_scene(LevelScene((sample_stair(),)))
        native = result.native_map
        self.assertEqual(len(native.sectors), 3)
        self.assertEqual(len(native.walls), 12)
        self.assertEqual([s.floorz for s in native.sectors], [8192, 7936, 7680])
        self.assertEqual([s.wallptr for s in native.sectors], [0, 4, 8])
        self.assertEqual(native.start_sector, 0)
        self.assertEqual((native.start_x, native.start_y), (356, 712))
        self.assertEqual(native.start_z, 4096)
        for i, (a_edge, b_edge) in enumerate(((1, 3), (1, 3))):
            a = i * 4 + a_edge
            b = (i + 1) * 4 + b_edge
            self.assertEqual(native.walls[a].nextwall, b)
            self.assertEqual(native.walls[a].nextsector, i + 1)
            self.assertEqual(native.walls[b].nextwall, a)
            self.assertEqual(native.walls[b].nextsector, i)
        self.assertEqual(result.object_map["stair/entry/step/0001/sector"], ("sector", 1))

        encoded = encode_map(native)
        decoded = parse_map(encoded)
        self.assertEqual(decoded.start_sector, 0)
        self.assertEqual([(s.floorz, s.ceilingz) for s in decoded.sectors],
                         [(8192, 0), (7936, 0), (7680, 0)])
        self.assertEqual([w.nextwall for w in decoded.walls],
                         [-1, 7, -1, -1, -1, 11, -1, 1, -1, -1, -1, 5])

    def test_all_cardinal_runs_share_matching_edges(self):
        shared = {"east": (1, 3), "west": (3, 1), "south": (2, 0), "north": (0, 2)}
        bounds = {
            "east": ((100, 200, 612, 1224), (612, 200, 1124, 1224)),
            "west": ((100, 200, 612, 1224), (-412, 200, 100, 1224)),
            "south": ((100, 200, 1124, 712), (100, 712, 1124, 1224)),
            "north": ((100, 200, 1124, 712), (100, -312, 1124, 200)),
        }
        for direction, (edge, reverse) in shared.items():
            with self.subTest(direction=direction):
                native = compile_scene(LevelScene((sample_stair(direction),))).native_map
                self.assertEqual(native.walls[edge].nextwall, 4 + reverse)
                self.assertEqual(native.walls[4 + reverse].nextwall, edge)
                for sector_index, expected in enumerate(bounds[direction]):
                    base = native.sectors[sector_index].wallptr
                    xcoords = [native.walls[base + i].x for i in range(4)]
                    ycoords = [native.walls[base + i].y for i in range(4)]
                    self.assertEqual((min(xcoords), min(ycoords), max(xcoords), max(ycoords)), expected)
                first_wall = native.walls[edge]
                first_end = native.walls[first_wall.point2]
                next_wall = native.walls[4 + reverse]
                next_end = native.walls[next_wall.point2]
                self.assertEqual((first_wall.x, first_wall.y), (next_end.x, next_end.y))
                self.assertEqual((first_end.x, first_end.y), (next_wall.x, next_wall.y))

    def test_rejects_impossible_geometry_and_outside_start(self):
        stair = StairSpec(**{**sample_stair().__dict__, "ceiling_z": 7800})
        with self.assertRaises(SceneCompileError):
            compile_scene(LevelScene((stair,)))
        with self.assertRaises(SceneCompileError):
            compile_scene(LevelScene((sample_stair(),), PlayerStart(99999, 0, 4000)))
        with self.assertRaises(SceneCompileError):
            compile_scene(LevelScene((sample_stair(),), PlayerStart(356, 712, 4096, stair_id="wrong")))
        with self.assertRaises(SceneCompileError):
            second = StairSpec(**{**sample_stair("west").__dict__, "id": "second"})
            compile_scene(LevelScene((sample_stair(), second)))

    def test_appearance_and_rotated_first_wall_keep_cardinal_portals_and_ids(self):
        appearance = tuple(
            StepAppearance(
                floor=PlaneMaterial(tile=20 + i, shade=-i, palette=3,
                                    xpanning=i, ypanning=4, stat=16),
                ceiling=PlaneMaterial(tile=60 + i, shade=2, palette=5,
                                      xpanning=6, ypanning=i),
                north=WallMaterial(tile=100 + i, overlay=200 + i, shade=7,
                                   palette=8, xrepeat=9, yrepeat=10,
                                   xpanning=11, ypanning=12, flags=4),
                east=WallMaterial(tile=110 + i, xrepeat=20 + i),
                south=WallMaterial(tile=120 + i),
                west=WallMaterial(tile=130 + i),
                visibility=40 + i,
                first_wall="east",
            ) for i in range(3)
        )
        stair = StairSpec(**{**sample_stair().__dict__, "appearance": appearance})
        result = compile_scene(LevelScene((stair,)))
        native = result.native_map
        self.assertEqual([sector.floorpicnum for sector in native.sectors], [20, 21, 22])
        self.assertEqual([sector.ceilingpicnum for sector in native.sectors], [60, 61, 62])
        self.assertEqual([sector.visibility for sector in native.sectors], [40, 41, 42])
        self.assertEqual(native.walls[0].picnum, 110)
        self.assertEqual(native.walls[0].xrepeat, 20)
        self.assertEqual(native.walls[1].picnum, 120)
        self.assertEqual(native.walls[2].picnum, 130)
        self.assertEqual(native.walls[3].picnum, 100)
        self.assertEqual(result.object_map["stair/entry/step/0000/wall/north"], ("wall", 3))
        east = result.object_map["stair/entry/step/0000/wall/east"][1]
        west_next = result.object_map["stair/entry/step/0001/wall/west"][1]
        self.assertEqual(native.walls[east].nextwall, west_next)
        self.assertEqual(native.walls[west_next].nextwall, east)
        decoded = parse_map(encode_map(native))
        self.assertEqual(decoded.sectors[0].floorstat, 16)
        self.assertEqual(decoded.walls[east].picnum, 110)


if __name__ == "__main__":
    unittest.main()
