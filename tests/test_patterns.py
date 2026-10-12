import unittest

from bloodforge.native import NativeMap, Sector, Wall
from bloodforge.patterns import StairCandidate, discover_stairs


def make_stairs(count=3, *, rise=256):
    level = NativeMap()
    width, tread = 512, 384
    first_x, first_y = 100, 200
    for i in range(count):
        left, right = first_x + i * tread, first_x + (i + 1) * tread
        top, bottom = first_y, first_y + width
        base = len(level.walls)
        points = [(left, top), (right, top), (right, bottom), (left, bottom)]
        sector = Sector(wallptr=base, wallnum=4, floorz=4096 - i * rise, ceilingz=0)
        level.sectors.append(sector)
        for j, (x, y) in enumerate(points):
            level.walls.append(Wall(x=x, y=y, point2=base + (j + 1) % 4))
    for i in range(count - 1):
        right_wall = i * 4 + 1
        left_wall = (i + 1) * 4 + 3
        level.walls[right_wall].nextwall = left_wall
        level.walls[right_wall].nextsector = i + 1
        level.walls[left_wall].nextwall = right_wall
        level.walls[left_wall].nextsector = i
    return level


class DiscoverStairsTests(unittest.TestCase):
    def test_finds_and_normalizes_straight_east_run(self):
        candidates = discover_stairs(make_stairs())
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.source_sector_indices, [0, 1, 2])
        self.assertEqual(candidate.portal_wall_ids, [1, 5])
        self.assertEqual(candidate.parameters, {
            "count": 3, "width": 512, "tread": 384, "rise": 256,
            "floor_z": 4096, "ceiling_z": 0,
            "origin_x": 100, "origin_y": 200, "direction": "east",
        })
        self.assertEqual(candidate.status, "candidate")

    def test_round_trip_serialization(self):
        candidate = discover_stairs(make_stairs())[0]
        self.assertEqual(StairCandidate.from_dict(candidate.to_dict()), candidate)

    def test_requires_at_least_three_treads(self):
        self.assertEqual(discover_stairs(make_stairs(2)), [])

    def test_rejects_blocking_portal(self):
        level = make_stairs()
        level.walls[1].cstat = 1
        self.assertEqual(discover_stairs(level), [])
        level = make_stairs()
        level.walls[7].cstat = 1
        self.assertEqual(discover_stairs(level), [])

    def test_rejects_nonuniform_rise(self):
        level = make_stairs()
        level.sectors[2].floorz -= 64
        self.assertEqual(discover_stairs(level), [])

    def test_rejects_slope_and_mechanism_tags(self):
        slope = make_stairs()
        slope.sectors[1].floorstat |= 2
        self.assertEqual(discover_stairs(slope), [])
        tagged = make_stairs()
        tagged.sectors[1].lotag = 5
        self.assertEqual(discover_stairs(tagged), [])

    def test_rejects_nonreciprocal_portal(self):
        level = make_stairs()
        level.walls[5].nextwall = -1
        self.assertEqual(discover_stairs(level), [])

    def test_rejects_ceiling_crossing_and_wrong_tread_dimensions(self):
        level = make_stairs()
        level.sectors[2].ceilingz = level.sectors[2].floorz
        self.assertEqual(discover_stairs(level), [])
        level = make_stairs()
        # Extend the terminal rectangle's far edge while retaining a rectangle.
        level.walls[9].x += 100
        level.walls[10].x += 100
        self.assertEqual(discover_stairs(level), [])


if __name__ == "__main__":
    unittest.main()
