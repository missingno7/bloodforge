import unittest
from shapely.geometry import Point

from bloodforge.native import NativeMap, Sector, Wall
from bloodforge.spatial_geometry import (
    SpatialGeometryError,
    sample_route,
    sector_geometry,
    validate_pose,
)


def map_with_loops(*sector_loops, ceilingz=0, floorz=8192,
                   ceilingstat=0, floorstat=0,
                   ceilingheinum=0, floorheinum=0):
    walls = []
    sectors = []
    for loops in sector_loops:
        first = len(walls)
        for loop in loops:
            offset = len(walls)
            for point in loop:
                walls.append(Wall(x=point[0], y=point[1]))
            for local, _point in enumerate(loop):
                walls[offset + local].point2 = offset + (local + 1) % len(loop)
        count = len(walls) - first
        sectors.append(Sector(wallptr=first, wallnum=count,
                              ceilingz=ceilingz, floorz=floorz,
                              ceilingstat=ceilingstat, floorstat=floorstat,
                              ceilingheinum=ceilingheinum, floorheinum=floorheinum))
    return NativeMap(sectors=sectors, walls=walls)


RECT = [(0, 0), (20, 0), (20, 20), (0, 20)]


class SectorGeometryTests(unittest.TestCase):
    def test_disjoint_short_range_is_not_ambiguous_target_ownership(self):
        level = map_with_loops([RECT], [[(100, 100), (101, 101)]])
        self.assertEqual(sector_geometry(level, 0).area, 400)
        with self.assertRaises(SpatialGeometryError):
            sector_geometry(level, 1)
        pose = {"x": 10, "y": 10, "z": 4096, "sector": 0}
        self.assertEqual(validate_pose(level, pose)["status"], "valid")
        # Automatic resolution is still conservative for unsupported candidates.
        del pose["sector"]
        self.assertEqual(validate_pose(level, pose)["status"], "unsupported")

    def test_irregular_sector_and_even_odd_hole_with_nested_island(self):
        outer = [(0, 0), (20, 0), (20, 12), (12, 12), (12, 20), (0, 20)]
        hole = [(2, 2), (10, 2), (10, 10), (2, 10)]
        island = [(4, 4), (8, 4), (8, 8), (4, 8)]
        level = map_with_loops([outer, hole, island])
        geometry = sector_geometry(level, 0)
        self.assertEqual(geometry.geom_type, "MultiPolygon")
        self.assertEqual(geometry.area, 336 - 64 + 16)
        self.assertTrue(geometry.contains(Point(1, 1)))
        self.assertFalse(geometry.contains(Point(3, 3)))
        self.assertTrue(geometry.contains(Point(5, 5)))

    def test_disjoint_loops_make_multipolygon(self):
        first = [(0, 0), (5, 0), (5, 5), (0, 5)]
        second = [(10, 10), (15, 10), (15, 15), (10, 15)]
        geometry = sector_geometry(map_with_loops([first, second]), 0)
        self.assertEqual(geometry.geom_type, "MultiPolygon")
        self.assertEqual(geometry.area, 50)

    def test_rejects_open_shared_self_intersecting_and_touching_loops(self):
        opened = map_with_loops([RECT])
        opened.walls[0].point2 = 2
        with self.assertRaisesRegex(SpatialGeometryError, "open or shared"):
            sector_geometry(opened, 0)

        bowtie = [(0, 0), (5, 4), (0, 4), (4, 0), (2, -2)]
        with self.assertRaises(SpatialGeometryError):
            sector_geometry(map_with_loops([bowtie]), 0)

        outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
        touching = [(10, 2), (12, 2), (12, 4), (10, 4)]
        with self.assertRaisesRegex(SpatialGeometryError, "cross or touch"):
            sector_geometry(map_with_loops([outer, touching]), 0)

    def test_rejects_range_escape_and_overlapping_sector_ownership(self):
        escaped = map_with_loops([RECT])
        escaped.walls[0].point2 = 50
        with self.assertRaisesRegex(SpatialGeometryError, "escapes sector"):
            sector_geometry(escaped, 0)
        overlap = map_with_loops([RECT], [RECT])
        overlap.sectors[1].wallptr = overlap.sectors[0].wallptr
        with self.assertRaisesRegex(SpatialGeometryError, "overlaps sector"):
            sector_geometry(overlap, 0)


class PoseValidationTests(unittest.TestCase):
    def test_explicit_and_resolved_pose_require_strict_xy_and_flat_z(self):
        level = map_with_loops([RECT])
        self.assertEqual(validate_pose(level, {"x": 10, "y": 10, "z": 4096})["status"], "valid")
        self.assertEqual(validate_pose(level, {"x": 0, "y": 10, "z": 4096, "sector": 0})["status"], "invalid")
        self.assertEqual(validate_pose(level, {"x": 10, "y": 10, "z": 0, "sector": 0})["status"], "invalid")
        self.assertEqual(validate_pose(level, {"x": True, "y": 10, "z": 100})["status"], "invalid")
        self.assertEqual(validate_pose(level, {"x": 10, "y": 10, "z": 4096,
                                              "sector": 9})["status"], "invalid")

    def test_hole_sky_and_slopes_are_distinguished(self):
        hole = [(5, 5), (15, 5), (15, 15), (5, 15)]
        level = map_with_loops([RECT, hole], ceilingstat=1)
        sky = validate_pose(level, {"x": 2, "y": 2, "z": 4096, "sector": 0})
        self.assertEqual(sky["status"], "valid")
        self.assertTrue(sky["sky"])
        self.assertIn("sky", sky["reason"])
        self.assertEqual(validate_pose(level, {"x": 10, "y": 10, "z": 4096,
                                              "sector": 0})["status"], "invalid")
        zero_heinum = map_with_loops([RECT], floorstat=2, floorheinum=0)
        zero_result = validate_pose(zero_heinum, {"x": 10, "y": 10, "z": 4096,
                                                  "sector": 0})
        self.assertEqual(zero_result["status"], "valid")
        self.assertIn("zero-heinum slope flag uses base Z", zero_result["reason"])
        sloped = map_with_loops([RECT], floorstat=2, floorheinum=128)
        self.assertEqual(validate_pose(sloped, {"x": 10, "y": 10, "z": 4096,
                                                "sector": 0})["status"], "unsupported")

    def test_implicit_overlap_is_unsupported(self):
        level = map_with_loops([RECT], [RECT])
        self.assertEqual(validate_pose(level, {"x": 10, "y": 10, "z": 4096})["status"], "unsupported")

    def test_bbox_skips_distant_malformed_loops_but_not_overlapping_or_unknown_ranges(self):
        distant = [(1000, 1000), (1020, 1000), (1020, 1020), (1000, 1020)]
        level = map_with_loops([RECT], [distant])
        level.walls[4].point2 = 6
        result = validate_pose(level, {"x": 10, "y": 10, "z": 4096})
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["sector"], 0)

        overlapping = map_with_loops([RECT], [RECT])
        overlapping.walls[4].point2 = 6
        blocked = validate_pose(overlapping, {"x": 10, "y": 10, "z": 4096})
        self.assertEqual(blocked["status"], "unsupported")

        unknown_range = map_with_loops([RECT], [distant])
        unknown_range.sectors[1].wallptr = -1
        unknown = validate_pose(unknown_range, {"x": 10, "y": 10, "z": 4096})
        self.assertEqual(unknown["status"], "unsupported")

    def test_flat_vertical_stacks_resolve_overlap_before_xyz_ambiguity(self):
        stacked = map_with_loops([RECT], [RECT])
        stacked.sectors[1].ceilingz = 10000
        stacked.sectors[1].floorz = 20000
        self.assertEqual(validate_pose(stacked, {"x": 10, "y": 10, "z": 4096})["sector"], 0)
        upper = validate_pose(stacked, {"x": 10, "y": 10, "z": 15000})
        self.assertEqual((upper["status"], upper["sector"]), ("valid", 1))

        overlapping_z = map_with_loops([RECT], [RECT])
        overlapping_z.sectors[1].ceilingz = 4000
        overlapping_z.sectors[1].floorz = 12000
        ambiguous = validate_pose(overlapping_z, {"x": 10, "y": 10, "z": 6000})
        self.assertEqual(ambiguous["status"], "unsupported")


class RouteSamplingTests(unittest.TestCase):
    def test_samples_are_unique_endpoints_preserved_and_angle_uses_shortest_wrap(self):
        level = map_with_loops([RECT])
        samples = sample_route(level, [
            {"x": 2, "y": 10, "z": 4096, "angle": 2040, "sector": 0},
            {"x": 18, "y": 10, "z": 4096, "angle": 8, "sector": 0},
        ], samples_per_segment=2)
        self.assertEqual(len(samples), 3)
        self.assertEqual((samples[0]["x"], samples[-1]["x"]), (2, 18))
        self.assertEqual([sample["angle"] for sample in samples], [2040, 0, 8])
        self.assertEqual(len({sample["id"] for sample in samples}), len(samples))
        self.assertTrue(all(sample["screenshot"] is False for sample in samples))

    def test_route_rejects_boundary_samples_and_marks_invalid_pose(self):
        level = map_with_loops([RECT])
        with self.assertRaisesRegex(SpatialGeometryError, "route sample"):
            sample_route(level, [
                {"x": 2, "y": 10, "z": 4096},
                {"x": 20, "y": 10, "z": 4096},
            ], samples_per_segment=2)


if __name__ == "__main__":
    unittest.main()
