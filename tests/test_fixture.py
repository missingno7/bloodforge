from copy import deepcopy
from dataclasses import replace
import unittest

from bloodforge.compiler import compile_scene
from bloodforge.fixture import graft_stair
from bloodforge.native import encode_map
from bloodforge.scene import LevelScene, StairSpec


def maps():
    spec = StairSpec("test", 4, 2048, 256, 2048, 0, 0, 0, -32768, "east", 20, 21, 22)
    source = compile_scene(LevelScene((spec,))).native_map
    fragment = compile_scene(LevelScene((replace(spec, count=3, origin_x=256, floor_z=-2048),))).native_map
    return source, fragment


class FixtureTests(unittest.TestCase):
    def test_graft_keeps_generated_surfaces_and_fixed_context(self):
        source, fragment = maps()
        before = encode_map(source)
        fragment.sectors[0].floorshade = -12
        fragment.walls[0].xpanning = 63
        graft = graft_stair(source, [1, 2, 3], fragment)
        self.assertEqual(encode_map(source), before)
        self.assertEqual(graft.native_map.sectors[1].floorshade, -12)
        self.assertEqual(graft.native_map.walls[4].xpanning, 63)
        self.assertEqual(graft.native_map.sectors[0], source.sectors[0])
        self.assertEqual(graft.native_map.walls[:4], source.walls[:4])
        self.assertEqual(graft.native_map.walls[7].nextwall, 1)
        self.assertEqual(graft.integrity["external_portals_restored"], 1)
        self.assertFalse(graft.integrity["generated_context"])
        self.assertTrue(graft.integrity["source_in_memory_unchanged"])

    def test_rejects_wrong_geometry_topology_anchor_and_mechanism(self):
        for mutation, message in (
            (lambda s, f: setattr(f.sectors[0], "floorz", 99), "heights"),
            (lambda s, f: setattr(f.walls[0], "x", 17), "footprint"),
            (lambda s, f: setattr(f.sectors[0], "wallptr", 1), "point2|loop"),
            (lambda s, f: setattr(f.walls[1], "nextwall", -1), "internal portal"),
            (lambda s, f: setattr(s.walls[7], "lotag", 1), "mechanisms"),
            (lambda s, f: setattr(s.sectors[1], "extra", 1), "mechanisms"),
            (lambda s, f: setattr(s.walls[1], "nextwall", -1), "reciprocal"),
        ):
            with self.subTest(message=message):
                source, fragment = maps()
                mutation(source, fragment)
                before = deepcopy(source)
                with self.assertRaisesRegex(ValueError, message):
                    graft_stair(source, [1, 2, 3], fragment)
                self.assertEqual(source, before)

    def test_rejects_ambiguous_selection_or_extra_output(self):
        source, fragment = maps()
        for indices in ([1, 1, 3], [1, 2, 99], [True, 2, 3], []):
            with self.assertRaises(ValueError):
                graft_stair(source, indices, fragment)
        fragment.walls.append(deepcopy(fragment.walls[0]))
        with self.assertRaisesRegex(ValueError, "only the selected"):
            graft_stair(source, [1, 2, 3], fragment)


if __name__ == "__main__":
    unittest.main()
