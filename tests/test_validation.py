import unittest

from bloodforge.native import NativeMap, Sector, Wall
from bloodforge.validation import PLAYER_CLEARANCE_Z_THRESHOLD, validate_map


def rectangle(x0=0, y0=0, x1=100, y1=100, *, reverse=False):
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if reverse:
        points.reverse()
    base = []
    for x, y in points:
        base.append(Wall(x=x, y=y, point2=-1))
    for i, wall in enumerate(base):
        wall.point2 = (i + 1) % len(base)
    return base


def square_map():
    walls = rectangle()
    return NativeMap(sectors=[Sector(wallptr=0, wallnum=len(walls), floorz=16384)],
                     walls=walls, start_x=25, start_y=25, start_z=8192, start_sector=0)


class ValidationTests(unittest.TestCase):
    def test_valid_flat_sector_reports_scoped_partial_dimensions(self):
        result = validate_map(square_map())
        self.assertEqual(result["native"]["status"], "pass")
        self.assertEqual(result["geometry"]["status"], "partial")
        self.assertEqual(result["geometry"]["metrics"]["closed_loop_count"], 1)
        self.assertEqual(result["portals"]["status"], "pass")
        self.assertEqual(result["player_clearance"]["status"], "partial")
        self.assertTrue(any(i["code"] == "player_clearance_assumption"
                            for i in result["player_clearance"]["issues"]))
        self.assertEqual(result["mechanisms"]["status"], "partial")
        self.assertEqual(result["runtime"]["status"], "partial")
        self.assertEqual(result["visual"]["status"], "partial")
        self.assertEqual(result["summary"]["error_count"], 0)
        self.assertEqual(PLAYER_CLEARANCE_Z_THRESHOLD, 8192)

    def test_even_odd_sector_contains_holes(self):
        outer = rectangle(0, 0, 100, 100)
        inner = rectangle(30, 30, 70, 70, reverse=True)
        for i, wall in enumerate(inner):
            wall.point2 = 4 + ((i + 1) % len(inner))
        walls = outer + inner
        board = NativeMap(sectors=[Sector(wallptr=0, wallnum=8, floorz=16384)],
                          walls=walls, start_x=10, start_y=10, start_z=8192,
                          start_sector=0)
        inside = validate_map(board)
        self.assertNotIn("start_outside_sector",
                         [i["code"] for i in inside["player_clearance"]["issues"]])
        board.start_x = board.start_y = 50
        hole = validate_map(board)
        self.assertIn("start_outside_sector",
                      [i["code"] for i in hole["player_clearance"]["issues"]])

    def test_detects_bad_ownership_point2_and_vertical_inversion(self):
        board = square_map()
        board.sectors[0].wallnum = 3
        board.sectors[0].floorz = -1
        board.walls[0].point2 = 3
        result = validate_map(board)
        codes = [issue["code"] for issue in result["geometry"]["issues"]]
        self.assertIn("point2_out_of_sector", codes)
        self.assertIn("orphan_wall", codes)
        self.assertIn("ceiling_below_floor", codes)
        self.assertEqual(result["geometry"]["status"], "fail")

    def test_checks_portal_reciprocity_and_reversed_endpoints(self):
        # Two adjacent rectangles share vertical edge x=10 in reverse order.
        left = rectangle(0, 0, 10, 10)
        right = rectangle(10, 0, 20, 10)
        walls = left + right
        for i, wall in enumerate(right):
            wall.point2 = 4 + ((i + 1) % 4)
        left[1].nextwall = 7
        left[1].nextsector = 1
        right[3].nextwall = 1
        right[3].nextsector = 0
        board = NativeMap(sectors=[Sector(wallptr=0, wallnum=4, floorz=16384),
                                   Sector(wallptr=4, wallnum=4, floorz=16384)],
                          walls=walls, start_x=5, start_y=5, start_z=8192,
                          start_sector=0)
        self.assertEqual(validate_map(board)["portals"]["status"], "pass")
        walls[7].nextwall = 6
        result = validate_map(board)
        self.assertIn("portal_not_reciprocal",
                      [issue["code"] for issue in result["portals"]["issues"]])

    def test_bounds_and_degenerate_edges_are_localized(self):
        board = square_map()
        board.start_sector = 9
        board.sprites = []
        board.walls[0].point2 = 0
        result = validate_map(board)
        self.assertTrue(any(i["kind"] == "map" and i["code"] == "start_sector_out_of_range"
                            for i in result["native"]["issues"]))
        self.assertTrue(any(i["kind"] == "wall" and i["index"] == 0 and
                            i["code"] == "zero_length_edge"
                            for i in result["geometry"]["issues"]))

    def test_flat_start_z_is_checked_but_slope_order_is_unsupported(self):
        board = square_map()
        board.start_z = -1
        flat = validate_map(board)
        self.assertIn("start_z_outside_vertical_bounds",
                      [i["code"] for i in flat["player_clearance"]["issues"]])

        board.sectors[0].ceilingz = 100
        board.sectors[0].floorz = 0
        board.sectors[0].ceilingstat = 2
        sloped = validate_map(board)
        self.assertNotIn("ceiling_below_floor", [i["code"] for i in sloped["geometry"]["issues"]])
        self.assertIn("sloped_clearance_unsupported",
                      [i["code"] for i in sloped["player_clearance"]["issues"]])
        self.assertEqual(sloped["player_clearance"]["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
