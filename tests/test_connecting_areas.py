import json
import unittest
from dataclasses import replace
from pathlib import Path

from bloodforge.compiler import SceneCompileError, compile_scene
from bloodforge.native import encode_map
from bloodforge.scene import (
    AreaSpec,
    ConnectionSpec,
    LandingSpec,
    LevelScene,
    PlaneMaterial,
    PlayerStart,
    PortalEndpoint,
    StairSpec,
    StepAppearance,
    WallMaterial,
)
from bloodforge.workspace import scene_from_dict


def stair(direction="east", *, id="stairs", origin=(0, 0), count=3,
          width=20, tread=10, rise=100, floor_z=0, ceiling_z=-1000,
          appearance=None):
    return StairSpec(id, count, width, tread, rise, origin[0], origin[1],
                     floor_z, ceiling_z, direction, 20, 21, 22, appearance)


def reciprocal(native, wall_index):
    wall = native.walls[wall_index]
    other = native.walls[wall.nextwall]
    return (wall.nextwall >= 0 and wall.nextsector >= 0
            and other.nextwall == wall_index and other.nextsector >= 0
            and (wall.x, wall.y) == (native.walls[other.point2].x, native.walls[other.point2].y)
            and (native.walls[wall.point2].x, native.walls[wall.point2].y) == (other.x, other.y))


class ConnectingAreaCompilerTests(unittest.TestCase):
    def test_area_only_scene_compiles_and_defaults_player_start(self):
        room = AreaSpec("room", -100, 200, 300, 400, 0, -8192)
        result = compile_scene(LevelScene(areas=(room,)))
        native = result.native_map
        self.assertEqual((len(native.sectors), len(native.walls)), (1, 4))
        self.assertEqual((native.start_x, native.start_y, native.start_z, native.start_sector),
                         (50, 400, -4096, 0))
        self.assertEqual(result.object_map["area/room/sector"], ("sector", 0))
        self.assertTrue(all(wall.nextwall == -1 for wall in native.walls))

    def test_connecting_stairs_example_splits_walls_and_preserves_portals(self):
        source = Path(__file__).parents[1] / "examples" / "connected-stairs.scene.json"
        scene = scene_from_dict(json.loads(source.read_text(encoding="utf-8")))
        result = compile_scene(scene)
        native = result.native_map
        self.assertEqual((len(native.sectors), len(native.walls)), (10, 46))
        self.assertEqual(native.start_sector, result.object_map["area/lower-room/sector"][1])

        lower = [result.object_map[f"area/lower-room/wall/east/segment/{i:04d}"][1]
                 for i in range(3)]
        self.assertEqual([native.walls[index].xrepeat for index in lower], [8, 32, 8])
        self.assertEqual([native.walls[index].xpanning for index in lower], [0, 64, 64])
        for key in ("connection/lower-room-to-bottom-landing/a",
                    "connection/lower-room-to-bottom-landing/b",
                    "connection/top-landing-to-upper-room/a",
                    "connection/upper-room-to-corridor/a"):
            index = result.object_map[key][1]
            self.assertTrue(reciprocal(native, index), key)
        self.assertEqual(native.sectors[result.object_map["landing/bottom-landing/sector"][1]].floorz, 0)
        self.assertEqual(native.sectors[result.object_map["landing/top-landing/sector"][1]].floorz, -8192)
        self.assertEqual(encode_map(native), encode_map(compile_scene(scene).native_map))

    def test_split_context_area_keeps_rotated_wall_anchor_and_plane_alignment(self):
        appearance = StepAppearance(
            floor=PlaneMaterial(tile=31, xpanning=17, ypanning=29),
            ceiling=PlaneMaterial(tile=32, xpanning=41, ypanning=53),
            east=WallMaterial(tile=44, xrepeat=48, xpanning=0),
            first_wall="south",
        )
        room = AreaSpec("room", -4096, 0, 3072, 3072, 0, -32768, appearance)
        landing = AreaSpec("landing", -1024, 512, 2048, 2048, 0, -32768)
        connection = ConnectionSpec("opening", PortalEndpoint("room", "east"),
                                    PortalEndpoint("landing", "west"))
        result = compile_scene(LevelScene(areas=(landing, room), connections=(connection,)))
        native = result.native_map
        sector_index = result.object_map["area/room/sector"][1]
        sector = native.sectors[sector_index]
        loop = native.walls[sector.wallptr:sector.wallptr + sector.wallnum]

        # Rotated first_wall means the native loop begins on the south edge,
        # then follows the same directed cycle around the split east wall.
        self.assertEqual((loop[0].x, loop[0].y), (-1024, 3072))
        self.assertEqual((loop[0].xrepeat, loop[0].xpanning), (8, 0))
        for slot, wall in enumerate(loop):
            self.assertEqual(wall.point2, sector.wallptr + (slot + 1) % len(loop))
            endpoint = native.walls[wall.point2]
            self.assertTrue(any((wall.x, wall.y) == (edge.x, edge.y) for edge in loop))
            self.assertTrue(any((endpoint.x, endpoint.y) == (edge.x, edge.y) for edge in loop))

        east_segments = [native.walls[result.object_map[
            f"area/room/wall/east/segment/{index:04d}"][1]] for index in range(3)]
        self.assertEqual([(wall.x, wall.y) for wall in east_segments],
                         [(-1024, 0), (-1024, 512), (-1024, 2560)])
        self.assertEqual([(wall.xrepeat, wall.xpanning) for wall in east_segments],
                         [(8, 0), (32, 64), (8, 64)])
        self.assertTrue(reciprocal(native, result.object_map["connection/opening/a"][1]))
        self.assertEqual((sector.floorpicnum, sector.floorxpanning, sector.floorypanning),
                         (31, 17, 29))
        self.assertEqual((sector.ceilingpicnum, sector.ceilingxpanning, sector.ceilingypanning),
                         (32, 41, 53))

    def test_explicit_area_connections_to_both_landings_in_all_directions(self):
        expected = {
            "east": ((-17, 0, 10, 20, "east", "west"),
                     (37, 0, 10, 20, "east", "west")),
            "west": ((17, 0, 10, 20, "west", "east"),
                     (-37, 0, 10, 20, "west", "east")),
            "south": ((0, -17, 20, 10, "south", "north"),
                      (0, 37, 20, 10, "south", "north")),
            "north": ((0, 17, 20, 10, "north", "south"),
                      (0, -37, 20, 10, "north", "south")),
        }
        for direction, (bottom, top) in expected.items():
            with self.subTest(direction=direction):
                stair_spec = stair(direction, count=3, width=20, tread=10,
                                   rise=100, ceiling_z=-1000)
                landings = (LandingSpec("bottom", "stairs", "bottom", 7),
                            LandingSpec("top", "stairs", "top", 7))
                bottom_room = AreaSpec("bottom-room", *bottom[:4], 0, -1000)
                top_room = AreaSpec("top-room", *top[:4], -200, -1000)
                bottom_room_side, bottom_landing_side = bottom[4:]
                top_landing_side, top_room_side = top[4:]
                connections = (
                    ConnectionSpec("bottom-room-link",
                                   PortalEndpoint("bottom-room", bottom_room_side),
                                   PortalEndpoint("bottom", bottom_landing_side)),
                    ConnectionSpec("top-room-link",
                                   PortalEndpoint("top", top_landing_side),
                                   PortalEndpoint("top-room", top_room_side)),
                )
                result = compile_scene(LevelScene(stairs=(stair_spec,), landings=landings,
                                                  areas=(top_room, bottom_room),
                                                  connections=connections))
                native = result.native_map
                self.assertEqual(native.start_sector,
                                 result.object_map["stair/stairs/step/0000/sector"][1])
                for connection_id in ("bottom-room-link", "top-room-link"):
                    for endpoint in ("a", "b"):
                        wall_index = result.object_map[f"connection/{connection_id}/{endpoint}"][1]
                        self.assertTrue(reciprocal(native, wall_index),
                                        f"{direction}: {connection_id}/{endpoint}")
                for sector in native.sectors:
                    walls = native.walls[sector.wallptr:sector.wallptr + sector.wallnum]
                    self.assertGreaterEqual(len(walls), 4)
                    for offset, wall in enumerate(walls):
                        self.assertEqual(wall.point2,
                                         sector.wallptr + (offset + 1) % sector.wallnum)
                self.assertEqual(len([wall for wall in native.walls if wall.nextwall >= 0]), 12)

    def test_side_texture_phase_is_exact_for_all_cardinal_directions(self):
        for side in ("north", "east", "south", "west"):
            with self.subTest(side=side):
                wall = WallMaterial(tile=88, xrepeat=4)
                appearance = StepAppearance(**{side: wall})
                source = AreaSpec("a", 0, 0, 100, 100, 0, -1000, appearance)
                if side == "north":
                    peer = AreaSpec("b", 25, -50, 50, 50, 0, -1000)
                    other_side = "south"
                elif side == "east":
                    peer = AreaSpec("b", 100, 25, 50, 50, 0, -1000)
                    other_side = "west"
                elif side == "south":
                    peer = AreaSpec("b", 25, 100, 50, 50, 0, -1000)
                    other_side = "north"
                else:
                    peer = AreaSpec("b", -50, 25, 50, 50, 0, -1000)
                    other_side = "east"
                connection = ConnectionSpec("door", PortalEndpoint("a", side),
                                            PortalEndpoint("b", other_side))
                result = compile_scene(LevelScene(areas=(peer, source), connections=(connection,)))
                segments = [result.object_map[f"area/a/wall/{side}/segment/{i:04d}"][1]
                            for i in range(3)]
                self.assertEqual([result.native_map.walls[index].xrepeat for index in segments], [1, 2, 1])
                self.assertEqual([result.native_map.walls[index].xpanning for index in segments], [0, 8, 24])
                self.assertTrue(all(reciprocal(result.native_map, index) for index in
                                    (result.object_map["connection/door/a"][1],)))

    def test_landings_derive_bounds_and_automatically_connect_in_all_directions(self):
        for direction in ("east", "west", "north", "south"):
            with self.subTest(direction=direction):
                stair_spec = stair(direction)
                scene = LevelScene(stairs=(stair_spec,), landings=(
                    LandingSpec("bottom", stair_spec.id, "bottom", 7),
                    LandingSpec("top", stair_spec.id, "top", 9),
                ))
                result = compile_scene(scene)
                native = result.native_map
                self.assertEqual(len(native.sectors), 5)
                for name, endpoint in (("bottom", "bottom"), ("top", "top")):
                    key = f"landing/{name}/stair/{stair_spec.id}/{endpoint}/a"
                    self.assertTrue(reciprocal(native, result.object_map[key][1]))
                self.assertEqual(sum(1 for wall in native.walls if wall.nextwall >= 0), 8)

    def test_connection_order_and_scene_tuple_order_are_deterministic(self):
        one = stair("east", id="s1", origin=(0, 0))
        two = stair("west", id="s2", origin=(100, 100))
        room_a = AreaSpec("a", -100, 0, 100, 20, 0, -1000)
        room_b = AreaSpec("b", 30, 0, 70, 20, -200, -1000)
        scene_a = LevelScene((one, two), areas=(room_b, room_a))
        scene_b = LevelScene((two, one), areas=(room_a, room_b))
        compiled_a, compiled_b = compile_scene(scene_a), compile_scene(scene_b)
        self.assertEqual(encode_map(compiled_a.native_map), encode_map(compiled_b.native_map))
        self.assertEqual(compiled_a.object_map, compiled_b.object_map)

    def test_explicit_start_can_target_area_and_requires_containment(self):
        room = AreaSpec("room", -100, 0, 300, 100, 0, -8192)
        start = PlayerStart(0, 50, -2000, angle=512, area_id="room")
        result = compile_scene(LevelScene(areas=(room,), player_start=start))
        self.assertEqual((result.native_map.start_x, result.native_map.start_y,
                          result.native_map.start_z, result.native_map.start_angle,
                          result.native_map.start_sector), (0, 50, -2000, 512, 0))
        with self.assertRaises(SceneCompileError):
            compile_scene(LevelScene(areas=(room,), player_start=PlayerStart(999, 50, -2000, area_id="room")))

    def test_rejects_bad_boundaries_floors_openings_and_overlaps(self):
        a = AreaSpec("a", 0, 0, 100, 100, 0, -1000)
        disconnected = AreaSpec("b", 100, 101, 100, 100, 0, -1000)
        with self.assertRaisesRegex(SceneCompileError, "positive-length"):
            compile_scene(LevelScene(areas=(a, disconnected), connections=(ConnectionSpec(
                "gap", PortalEndpoint("a", "east"), PortalEndpoint("b", "west")),)))

        wrong_floor = AreaSpec("b", 100, 0, 100, 100, 1, -1000)
        with self.assertRaisesRegex(SceneCompileError, "floor heights"):
            compile_scene(LevelScene(areas=(a, wrong_floor), connections=(ConnectionSpec(
                "floor", PortalEndpoint("a", "east"), PortalEndpoint("b", "west")),)))

        overlapped = AreaSpec("b", 50, 0, 100, 100, -10, -1000)
        with self.assertRaisesRegex(SceneCompileError, "interiors overlap"):
            compile_scene(LevelScene(areas=(a, overlapped)))

        # The compiler rejects a malformed in-memory source whose connection
        # has no opening (normal AreaSpec construction rejects this earlier).
        blocked = AreaSpec("b", 100, 0, 100, 100, 0, -1)
        object.__setattr__(blocked, "ceiling_z", 0)
        with self.assertRaisesRegex(SceneCompileError, "vertical opening"):
            compile_scene(LevelScene(areas=(a, blocked), connections=(ConnectionSpec(
                "blocked", PortalEndpoint("a", "east"), PortalEndpoint("b", "west")),)))

    def test_rejects_duplicate_intervals_self_connections_and_duplicate_landings(self):
        a = AreaSpec("a", 0, 0, 100, 100, 0, -1000)
        b = AreaSpec("b", 100, 0, 100, 100, 0, -1000)
        connections = (
            ConnectionSpec("one", PortalEndpoint("a", "east"), PortalEndpoint("b", "west")),
            ConnectionSpec("two", PortalEndpoint("a", "east"), PortalEndpoint("b", "west")),
        )
        with self.assertRaisesRegex(SceneCompileError, "overlaps"):
            compile_scene(LevelScene(areas=(a, b), connections=connections))
        with self.assertRaisesRegex(SceneCompileError, "itself"):
            compile_scene(LevelScene(areas=(a,), connections=(ConnectionSpec(
                "self", PortalEndpoint("a", "east"), PortalEndpoint("a", "west")),)))

        stair_spec = stair()
        duplicate = LevelScene(stairs=(stair_spec,), landings=(
            LandingSpec("l1", "stairs", "bottom", 5),
            LandingSpec("l2", "stairs", "bottom", 10),
        ))
        with self.assertRaisesRegex(SceneCompileError, "more than one bottom landing"):
            compile_scene(duplicate)

    def test_split_rejects_unrepresentable_texture_phase_and_xflip(self):
        a = AreaSpec("a", 0, 0, 10, 10, 0, -1000,
                     StepAppearance(east=WallMaterial(xrepeat=1)))
        b = AreaSpec("b", 10, 1, 8, 8, 0, -1000)
        link = ConnectionSpec("door", PortalEndpoint("a", "east"), PortalEndpoint("b", "west"))
        with self.assertRaisesRegex(SceneCompileError, "not exactly representable"):
            compile_scene(LevelScene(areas=(a, b), connections=(link,)))

        flipped = replace(a, appearance=StepAppearance(east=WallMaterial(xrepeat=10, flags=8)))
        b2 = AreaSpec("b", 10, 2, 6, 6, 0, -1000)
        link2 = ConnectionSpec("door", PortalEndpoint("a", "east"), PortalEndpoint("b", "west"))
        with self.assertRaisesRegex(SceneCompileError, "x-flipped"):
            compile_scene(LevelScene(areas=(flipped, b2), connections=(link2,)))

    def test_early_base_wall_capacity_rejection_and_no_empty_map(self):
        huge_a, huge_b = stair(id="huge-a", count=5000), stair(id="huge-b", count=5000)
        with self.assertRaisesRegex(SceneCompileError, "base scene wall count"):
            compile_scene(LevelScene(stairs=(huge_a, huge_b)))
        with self.assertRaisesRegex(SceneCompileError, "at least one native sector"):
            compile_scene(LevelScene())


if __name__ == "__main__":
    unittest.main()
