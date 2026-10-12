from dataclasses import replace
import unittest

from bloodforge.compiler import compile_scene
from bloodforge.fidelity import compare_stair
from bloodforge.reconstruction import measure_appearance
from bloodforge.scene import LevelScene, StairSpec, WallMaterial


class AppearanceMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.spec = StairSpec("measured", 3, 1024, 256, 1024, 0, 0, 8192, -16384, "west", 20, 21, 22)
        self.source = compile_scene(LevelScene((self.spec,))).native_map

    def test_fit_measures_all_fields_without_changing_geometry(self):
        self.source.sectors[0].floorstat = 68
        self.source.sectors[0].floorshade = -12
        self.source.sectors[1].ceilingypanning = 67
        self.source.sectors[2].visibility = 22
        self.source.walls[3].shade = 45
        self.source.walls[3].xrepeat = 16
        self.source.walls[3].xpanning = 100
        appearance = measure_appearance(self.source, [0, 1, 2])
        self.assertEqual(appearance[0].west.xpanning, 100)
        self.assertEqual(appearance[1].ceiling.ypanning, 67)
        self.assertEqual(appearance[2].visibility, 22)
        fitted = compile_scene(LevelScene((replace(self.spec, appearance=appearance),))).native_map
        self.assertEqual(self.source, fitted)
        self.assertEqual(compare_stair(self.source, [0, 1, 2], fitted)["geometry"]["polygon_exact_fraction"], 1)

    def test_unsupported_materials_are_not_silently_discarded(self):
        for flags in (1, 32, 64, 1024, 32768):
            with self.assertRaises(ValueError):
                WallMaterial(flags=flags)
        self.assertEqual(WallMaterial(flags=2 | 256 | 512).flags, 770)
        self.source.walls[0].cstat = 1
        with self.assertRaises(ValueError):
            measure_appearance(self.source, [0, 1, 2])
        self.source.walls[0].cstat = 0
        self.source.sectors[0].floorstat = 2
        with self.assertRaises(ValueError):
            measure_appearance(self.source, [0, 1, 2])

    def test_invalid_loops_and_indices_fail(self):
        for indices in ([], [0, 0], [True], [99]):
            with self.assertRaises(ValueError):
                measure_appearance(self.source, indices)
        self.source.walls[0].point2 = 4
        with self.assertRaisesRegex(ValueError, "leaves"):
            measure_appearance(self.source, [0])


if __name__ == "__main__":
    unittest.main()
