import unittest

from bloodforge.fidelity import compare_stair
from bloodforge.native import NativeMap, Sector, Wall


def make_map(*, count=3, external=False, floor_tile=5, ceiling_tile=6, wall_tile=7):
    level = NativeMap()
    rectangles = [(100 + i * 300, 200, 400 + i * 300, 600) for i in range(count)]
    if external:
        rectangles.append((-200, 200, 100, 600))
    for index, (left, top, right, bottom) in enumerate(rectangles):
        first = len(level.walls)
        level.sectors.append(Sector(
            wallptr=first, wallnum=4,
            floorz=4000 - min(index, count - 1) * 256,
            ceilingz=0, floorpicnum=floor_tile, ceilingpicnum=ceiling_tile,
        ))
        corners = ((left, top), (right, top), (right, bottom), (left, bottom))
        for edge, (x, y) in enumerate(corners):
            level.walls.append(Wall(x=x, y=y, point2=first + (edge + 1) % 4, picnum=wall_tile))
    for index in range(count - 1):
        right_wall, left_wall = index * 4 + 1, (index + 1) * 4 + 3
        level.walls[right_wall].nextwall = left_wall
        level.walls[right_wall].nextsector = index + 1
        level.walls[left_wall].nextwall = right_wall
        level.walls[left_wall].nextsector = index
    if external:
        # The appended rectangle lies west of step 0 and shares its left edge.
        outside_wall, stair_wall = count * 4 + 1, 3
        level.walls[outside_wall].nextwall = stair_wall
        level.walls[outside_wall].nextsector = 0
        level.walls[stair_wall].nextwall = outside_wall
        level.walls[stair_wall].nextsector = count
    return level


class CompareStairTests(unittest.TestCase):
    def test_exact_fragment_with_external_source_portal(self):
        source = make_map(external=True)
        reconstructed = make_map()
        result = compare_stair(source, [0, 1, 2], reconstructed)
        self.assertTrue(result["sector_count_matches"])
        self.assertEqual(result["geometry"]["polygon_exact_fraction"], 1.0)
        self.assertEqual(result["geometry"]["max_floor_z_error"], 0)
        self.assertEqual(result["geometry"]["max_ceiling_z_error"], 0)
        self.assertTrue(result["topology"]["internal_topology_matches"])
        self.assertTrue(result["topology"]["source_external_connections_excluded"])
        self.assertEqual(result["topology"]["source_external_connections_lost"], 1)
        self.assertEqual(result["floor_tile_exact_fraction"], 1.0)
        self.assertEqual(result["ceiling_tile_exact_fraction"], 1.0)
        self.assertEqual(result["wall_material"]["exact_fraction"], 1.0)
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(result["visual_verified"])

    def test_detects_moved_vertex_and_nonrectangular_reconstruction(self):
        source, reconstructed = make_map(), make_map()
        reconstructed.walls[9].x += 10
        result = compare_stair(source, [0, 1, 2], reconstructed)
        self.assertFalse(result["sector_comparisons"][2]["polygon_exact_match"])
        self.assertFalse(result["sector_comparisons"][2]["reconstructed_rectangle"]["is_rectangle"])
        self.assertFalse(result["geometry"]["all_reconstructed_sectors_rectangular"])

    def test_detects_broken_reciprocal_internal_topology(self):
        source, reconstructed = make_map(), make_map()
        reconstructed.walls[7].nextwall = -1
        result = compare_stair(source, [0, 1, 2], reconstructed)
        self.assertTrue(result["topology"]["internal_graph_matches"])
        self.assertFalse(result["topology"]["internal_topology_matches"])
        self.assertFalse(result["topology"]["reconstructed"]["reciprocal_consistent"])

    def test_reports_z_and_material_mismatches(self):
        source, reconstructed = make_map(), make_map()
        reconstructed.sectors[1].floorz += 32
        reconstructed.sectors[2].ceilingz -= 16
        reconstructed.sectors[1].floorpicnum = 99
        reconstructed.walls[0].picnum = 101
        result = compare_stair(source, [0, 1, 2], reconstructed)
        self.assertEqual(result["geometry"]["max_floor_z_error"], 32)
        self.assertEqual(result["geometry"]["max_ceiling_z_error"], 16)
        self.assertAlmostEqual(result["floor_tile_exact_fraction"], 2 / 3)
        self.assertLess(result["wall_material"]["exact_fraction"], 1.0)
        self.assertTrue(result["wall_material"]["partial"])


if __name__ == "__main__":
    unittest.main()
