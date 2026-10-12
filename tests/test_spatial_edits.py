import copy
import unittest

from bloodforge.compiler import compile_scene
from bloodforge.native import NativeMap
from bloodforge.scene import AreaSpec, ConnectionSpec, LevelScene, PortalEndpoint, StepAppearance, WallMaterial
from bloodforge.spatial_edits import intervene
from bloodforge.validation import validate_map


def connected_map():
    left = AreaSpec("left", 0, 0, 100, 100, 0, -12000,
                    StepAppearance(east=WallMaterial(flags=4)))
    right = AreaSpec("right", 100, 0, 100, 100, 0, -12000,
                     StepAppearance(west=WallMaterial(flags=8)))
    scene = LevelScene(areas=(left, right), connections=(ConnectionSpec(
        "door", PortalEndpoint("left", "east"), PortalEndpoint("right", "west")),))
    result = compile_scene(scene)
    native = result.native_map
    portal_wall = result.object_map["connection/door/a"][1]
    partner = native.walls[portal_wall].nextwall
    return native, portal_wall, partner


def room_map():
    scene = LevelScene(areas=(AreaSpec("room", 0, 0, 100, 100, 0, -12000),))
    return compile_scene(scene).native_map


class SpatialEditTests(unittest.TestCase):
    def assertNativeValid(self, native):
        report = validate_map(native)
        self.assertEqual(report["summary"]["error_count"], 0, report["summary"])

    def test_close_portal_changes_both_reciprocal_records_only(self):
        source, wall, partner = connected_map()
        original = copy.deepcopy(source)
        edited, audit = intervene(source, {"kind": "close_portal", "wall": wall,
                                           "expect": {"nextwall": partner}})
        self.assertEqual((edited.walls[wall].nextwall, edited.walls[wall].nextsector), (-1, -1))
        self.assertEqual((edited.walls[partner].nextwall, edited.walls[partner].nextsector), (-1, -1))
        self.assertEqual(edited.walls[wall].x, original.walls[wall].x)
        self.assertEqual(edited.walls[partner].x, original.walls[partner].x)
        self.assertEqual(edited.sectors, original.sectors)
        self.assertEqual(edited.sprites, original.sprites)
        self.assertEqual(source, original)
        self.assertEqual({row["index"] for row in audit["changes"]}, {wall, partner})
        self.assertTrue(all(value for key, value in audit["invariants"].items()
                            if key != "portal_graph_preserved"))
        self.assertFalse(audit["invariants"]["portal_graph_preserved"])
        self.assertNativeValid(edited)

    def test_block_portal_sets_block_flag_on_both_sides_and_preserves_graph(self):
        source, wall, partner = connected_map()
        edited, audit = intervene(source, {"kind": "block_portal", "wall": wall})
        self.assertEqual(edited.walls[wall].cstat, source.walls[wall].cstat | 1)
        self.assertEqual(edited.walls[partner].cstat, source.walls[partner].cstat | 1)
        self.assertEqual((edited.walls[wall].nextwall, edited.walls[wall].nextsector),
                         (source.walls[wall].nextwall, source.walls[wall].nextsector))
        self.assertEqual((edited.walls[partner].nextwall, edited.walls[partner].nextsector),
                         (source.walls[partner].nextwall, source.walls[partner].nextsector))
        self.assertTrue(audit["invariants"]["portal_graph_preserved"])
        self.assertTrue(all(audit["invariants"].values()))
        self.assertNativeValid(edited)

    def test_wall_shade_clamps_at_native_bounds_and_changes_only_target(self):
        source = room_map()
        target = 1
        source.walls[target].shade = 120
        original = copy.deepcopy(source)
        edited, audit = intervene(source, {"kind": "wall_shade", "wall": target,
                                           "delta": 30, "expect": {"shade": 120}})
        self.assertEqual(edited.walls[target].shade, 127)
        self.assertEqual(edited.walls[:target], original.walls[:target])
        self.assertEqual(edited.walls[target + 1:], original.walls[target + 1:])
        self.assertEqual(edited.walls[target].x, original.walls[target].x)
        self.assertEqual(audit["changed_fields"], ["shade"])
        self.assertTrue(all(audit["invariants"].values()))
        self.assertNativeValid(edited)

        lower, _ = intervene(source, {"kind": "wall_shade", "wall": target, "delta": -500})
        self.assertEqual(lower.walls[target].shade, -128)

    def test_sector_ceiling_changes_only_flat_sector_and_keeps_start_inside(self):
        source = room_map()
        original = copy.deepcopy(source)
        edited, audit = intervene(source, {"kind": "sector_ceiling", "sector": 0,
                                           "z": -10000, "expect": {"floorz": 0}})
        self.assertEqual(edited.sectors[0].ceilingz, -10000)
        expected = copy.deepcopy(original.sectors[0])
        expected.ceilingz = -10000
        self.assertEqual(edited.sectors[0], expected)
        self.assertEqual(edited.start_z, original.start_z)
        self.assertTrue(all(audit["invariants"].values()))
        self.assertNativeValid(edited)

    def test_noop_is_audited_successfully_without_mutating_input(self):
        source = room_map()
        edited, audit = intervene(source, {"kind": "wall_shade", "wall": 0, "delta": 0})
        self.assertIsNot(edited, source)
        self.assertEqual(edited, source)
        self.assertEqual(audit["changes"], [])
        self.assertEqual(audit["changed_fields"], [])
        self.assertTrue(all(audit["invariants"].values()))

    def test_rejects_invalid_targets_preconditions_unknown_keys_and_bad_kinds(self):
        source, wall, partner = connected_map()
        invalid = [
            {"kind": "wall_shade", "wall": -1, "delta": 1},
            {"kind": "wall_shade", "wall": True, "delta": 1},
            {"kind": "wall_shade", "wall": wall, "delta": 1 << 40},
            {"kind": "close_portal", "wall": wall, "expect": {"nextwall": partner + 1}},
            {"kind": "block_portal", "wall": wall, "typo": 1},
            {"kind": "not-a-real-edit", "wall": wall},
        ]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                intervene(source, spec)

        malformed = copy.deepcopy(source)
        malformed.walls[partner].nextwall = -1
        for kind in ("close_portal", "block_portal"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "not reciprocal"):
                intervene(malformed, {"kind": kind, "wall": wall})

    def test_rejects_ceiling_bounds_slopes_and_start_exclusion(self):
        source = room_map()
        for spec in (
            {"kind": "sector_ceiling", "sector": 0, "z": 0},
            {"kind": "sector_ceiling", "sector": 0, "z": -5000},
            {"kind": "sector_ceiling", "sector": 0, "z": 1 << 40},
        ):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                intervene(source, spec)

        sloped = copy.deepcopy(source)
        sloped.sectors[0].floorstat |= 2
        sloped.sectors[0].floorheinum = 1
        with self.assertRaisesRegex(ValueError, "flat target"):
            intervene(sloped, {"kind": "sector_ceiling", "sector": 0, "z": -10000})

    def test_zero_heinum_slope_flags_are_effectively_flat(self):
        source = room_map()
        source.sectors[0].ceilingstat |= 2
        source.sectors[0].floorstat |= 2
        edited, audit = intervene(source, {"kind": "sector_ceiling", "sector": 0, "z": -10000})
        self.assertEqual(edited.sectors[0].ceilingz, -10000)
        self.assertEqual(edited.sectors[0].ceilingstat, source.sectors[0].ceilingstat)
        self.assertEqual(edited.sectors[0].floorstat, source.sectors[0].floorstat)
        self.assertEqual(audit["changed_fields"], ["ceilingz"])

    def test_bad_portal_indices_and_wrong_record_types_fail_closed(self):
        source, wall, _ = connected_map()
        for index in (-1, len(source.walls), True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                intervene(source, {"kind": "close_portal", "wall": index})
        with self.assertRaises(ValueError):
            intervene({}, {"kind": "wall_shade", "wall": 0, "delta": 1})
        with self.assertRaises(ValueError):
            intervene(source, {"kind": "sector_ceiling", "sector": len(source.sectors), "z": -1})


if __name__ == "__main__":
    unittest.main()
