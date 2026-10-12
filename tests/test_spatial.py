"""Independent contract tests for source-backed spatial summaries."""
from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path
import tempfile
import unittest

from bloodforge.config import Config
from bloodforge.native import NativeMap, Sector, Wall, encode_map, write_map
from bloodforge.spatial import (analyze_space, compare_spaces, inspect_space,
                                require_index_correspondence)
from bloodforge.store import database


def _append_sector(level: NativeMap, points: list[tuple[int, int]], *,
                   floorz: int = 8192, ceilingz: int = 0,
                   floorstat: int = 0, ceilingstat: int = 0,
                   floorpicnum: int = 1, ceilingpicnum: int = 2) -> int:
    index = len(level.sectors)
    start = len(level.walls)
    for point in points:
        level.walls.append(Wall(x=point[0], y=point[1]))
    for local in range(len(points)):
        level.walls[start + local].point2 = start + (local + 1) % len(points)
    level.sectors.append(Sector(wallptr=start, wallnum=len(points), floorz=floorz,
                                 ceilingz=ceilingz, floorstat=floorstat,
                                 ceilingstat=ceilingstat, floorpicnum=floorpicnum,
                                 ceilingpicnum=ceilingpicnum))
    if len(level.sectors) == 1:
        level.start_x = points[0][0] + 10
        level.start_y = points[0][1] + 10
        level.start_z = (floorz + ceilingz) // 2
        level.start_sector = 0
    return index


def _rect(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _portal(level: NativeMap, a: int, wa: int, b: int, wb: int,
            *, flags_a: int = 0, flags_b: int = 0) -> None:
    level.walls[wa].nextwall, level.walls[wa].nextsector = wb, b
    level.walls[wb].nextwall, level.walls[wb].nextsector = wa, a
    level.walls[wa].cstat = flags_a
    level.walls[wb].cstat = flags_b


def _three_strip_map() -> NativeMap:
    """Three 100x100 sectors with coplanar A/B and same-finish B/C."""
    level = NativeMap()
    _append_sector(level, _rect(0, 0, 100, 100), floorpicnum=10)
    _append_sector(level, _rect(100, 0, 200, 100), floorpicnum=20)
    _append_sector(level, _rect(200, 0, 300, 100), floorz=9000,
                   ceilingz=1000, floorpicnum=20)
    _portal(level, 0, 1, 1, 7)
    _portal(level, 1, 5, 2, 11)
    return level


def _narrow_portal_map() -> NativeMap:
    """Adjacent rectangles with a 20-unit portal segment on a 100-unit side."""
    level = NativeMap()
    # Left sector's east edge is split at y=40 and y=60; middle segment is wall 2.
    _append_sector(level, [(0, 0), (100, 0), (100, 40), (100, 60),
                           (100, 100), (0, 100)])
    # Right sector has the matching, reversed three-part west boundary.
    _append_sector(level, [(100, 0), (200, 0), (200, 100), (100, 100),
                           (100, 60), (100, 40)])
    _portal(level, 0, 2, 1, 10)
    return level


def _blocked_expansion_map() -> NativeMap:
    """Small room adjacent to a five-times-larger room through a blocked portal."""
    level = NativeMap()
    _append_sector(level, _rect(0, 0, 100, 100))
    # The long west edge is split at the small room's north/south extent.
    _append_sector(level, [(100, 0), (200, 0), (200, 500), (100, 500), (100, 100)])
    # In the second sector wall index 8 is the reversed shared segment.
    _portal(level, 0, 1, 1, 8, flags_a=1, flags_b=1)
    return level


class SpatialAnalysisTests(unittest.TestCase):
    def analyze(self, level: NativeMap, sectors: list[int] | None = None) -> dict:
        return analyze_space(level, "a" * 64, sectors)

    def test_coplanar_and_finish_views_are_complementary_hypotheses(self):
        report = self.analyze(_three_strip_map())
        coplanar = {tuple(row["sectors"]) for row in report["regions"] if row["view"] == "coplanar"}
        finish = {tuple(row["sectors"]) for row in report["regions"] if row["view"] == "finish"}
        self.assertIn((0, 1), coplanar)
        self.assertIn((2,), coplanar)
        self.assertIn((0,), finish)
        self.assertIn((1, 2), finish)
        self.assertNotEqual(coplanar, finish)
        self.assertTrue(all(r["evidence_kind"] == "continuity-hypothesis" for r in report["regions"]))
        self.assertFalse(report["claims"]["design_intent_established"])

    def test_narrow_opening_is_reported_without_merging_or_claiming_navigation(self):
        report = self.analyze(_narrow_portal_map())
        opening, = report["openings"]
        self.assertEqual(opening["width_xy"], 20)
        self.assertLess(opening["opening_width_to_smaller_area_scale"], 0.5)
        self.assertEqual(opening["navigation"], "unverified")
        self.assertEqual(opening["visibility"], "not_observed")
        self.assertEqual(len([r for r in report["regions"] if r["view"] == "coplanar" and len(r["sectors"]) > 1]), 0)
        self.assertEqual(len(report["transitions"]), 1)
        self.assertEqual(report["transitions"][0]["interpretation"],
                         "area contrast across threshold; compression/reveal requires route-view evidence")

    def test_blocked_boundary_does_not_gain_candidate_expansion_claim(self):
        report = self.analyze(_blocked_expansion_map())
        transition, = report["transitions"]
        self.assertEqual(transition["region_area_ratio"], 5)
        self.assertEqual(transition["navigation"], "unverified")
        self.assertIsNone(transition["candidate_expansion_from_sector"])

    def test_hole_and_negative_space_are_geometry_only(self):
        level = NativeMap()
        _append_sector(level, _rect(0, 0, 100, 100))
        # Disjoint from outer loop and nested: even-odd fill records this as a hole.
        start = len(level.walls)
        hole = _rect(20, 20, 80, 80)
        for point in hole:
            level.walls.append(Wall(x=point[0], y=point[1]))
        for local in range(len(hole)):
            level.walls[start + local].point2 = start + (local + 1) % len(hole)
        level.sectors[0].wallnum += len(hole)
        report = self.analyze(level)
        sector, = report["sectors"]
        self.assertEqual(sector["hole_count"], 1)
        self.assertEqual(sector["hole_area"], 3600)
        self.assertEqual(sector["xy_area"], 6400)
        self.assertEqual(sector["intentional_emptiness"], "not_established")
        negative = report["regions"][0]["negative_space"]
        self.assertEqual(negative["navigable_empty_volume"], "not_established")
        self.assertEqual(negative["unmodeled_outside"], "not_classified_as_solid")

    def test_sloped_portal_opening_remains_unknown(self):
        level = _three_strip_map()
        level.sectors[1].floorstat |= 2
        level.sectors[1].floorheinum = 4096
        report = self.analyze(level, [0, 1])
        shared = next(row for row in report["openings"] if row["sectors"] == [0, 1])
        self.assertIsNone(shared["flat_opening_z"])
        self.assertIsNone(shared["floor_delta_z"])
        self.assertEqual(shared["static_point_connection"], "unknown_slope")
        self.assertEqual(shared["navigation"], "unverified")
        self.assertEqual(shared["visibility"], "not_observed")

    def test_zero_heinum_slope_flag_cannot_emit_negative_flat_volume(self):
        level = NativeMap()
        # Validator currently exempts stat&2 planes from scalar ordering, while
        # the spatial layer treats a zero-heinum plane as its flat base Z.
        _append_sector(level, _rect(0, 0, 100, 100), floorz=0,
                       ceilingz=8192, floorstat=2)
        with self.assertRaisesRegex(ValueError, "inverted ceiling/floor Z bounds"):
            self.analyze(level)

    def test_comparison_requires_same_scope_policy_and_index_preserving_maps(self):
        before_map = _three_strip_map()
        after_map = copy.deepcopy(before_map)
        after_map.walls[1].cstat |= 1
        require_index_correspondence(before_map, after_map)
        before = self.analyze(before_map, [0, 1])
        after = analyze_space(after_map, "b" * 64, [0, 1])
        comparison = compare_spaces(before, after)
        self.assertEqual(comparison["source_hashes"], ["a" * 64, "b" * 64])
        self.assertEqual(comparison["correspondence"],
                         "same native indices; only use with an audited index-preserving intervention")
        self.assertTrue(comparison["native_counts_unchanged"])
        self.assertFalse(comparison["runtime_verified"])
        self.assertTrue(comparison["opening_changes"])
        self.assertFalse(comparison["continuity_memberships_unchanged"])

        wrong_scope = self.analyze(after_map, [0, 2])
        with self.assertRaisesRegex(ValueError, "same native sector selection"):
            compare_spaces(before, wrong_scope)
        wrong_policy = analyze_space(after_map, "b" * 64, [0, 1],
                                     policy={"broad_opening_ratio": 0.6})
        with self.assertRaisesRegex(ValueError, "same analysis policy"):
            compare_spaces(before, wrong_policy)

        moved = copy.deepcopy(after_map)
        moved.walls[0].x += 1
        with self.assertRaisesRegex(ValueError, "unchanged indexed wall XY"):
            require_index_correspondence(before_map, moved)

    def test_movement_and_masked_or_one_way_flags_are_separate(self):
        level = _three_strip_map()
        _portal(level, 0, 1, 1, 7, flags_a=1, flags_b=16)
        report = self.analyze(level, [0, 1])
        shared = next(row for row in report["openings"] if row["sectors"] == [0, 1])
        self.assertTrue(shared["movement_blocking_flag"])
        self.assertTrue(shared["masked_or_one_way_flag"])
        self.assertEqual(shared["native_wall_flags"], [1, 16])
        self.assertEqual(shared["static_point_connection"], "blocked_by_native_flag")
        self.assertFalse(any(len(r["sectors"]) > 1 for r in report["regions"] if r["view"] == "coplanar"))

    def test_neighbor_loop_geometry_failure_is_localized(self):
        level = NativeMap()
        _append_sector(level, _rect(0, 0, 100, 100))
        _append_sector(level, _rect(100, 0, 200, 100))
        _portal(level, 0, 1, 1, 7)
        # Structurally closed loop that touches the neighbor's outer loop; the
        # generic validator accepts it, but strict polygon construction cannot.
        start = len(level.walls)
        touching = _rect(200, 20, 210, 30)
        for point in touching:
            level.walls.append(Wall(x=point[0], y=point[1]))
        for local in range(len(touching)):
            level.walls[start + local].point2 = start + (local + 1) % len(touching)
        level.sectors[1].wallnum += len(touching)
        report = self.analyze(level, [0])
        self.assertEqual(report["scope"]["selected_sectors"], [0])
        self.assertEqual([r["id"] for r in report["sectors"]], [0])
        self.assertEqual(len(report["geometry_errors"]), 1)
        self.assertEqual(report["geometry_errors"][0]["sector"], 1)
        self.assertEqual(report["geometry_errors"][0]["scope"], "neighbor")

    def test_inspection_observation_key_is_source_scoped_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            map_path = root / "source.map"
            level = _three_strip_map()
            write_map(level, map_path)
            raw = map_path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            config = Config(corpus_root=root, data_root=root / "data")
            config.data_root.mkdir()
            with database(config.knowledge_db) as db, db:
                db.execute("INSERT INTO contents(sha256,byte_size,parse_status) VALUES(?,?, 'ok')",
                           (sha, len(raw)))
            first = inspect_space(config, map_path, [0, 1])
            second = inspect_space(config, map_path, [0, 1])
            self.assertEqual(first["source"]["sha256"], sha)
            self.assertEqual(first["report_hash"], second["report_hash"])
            self.assertEqual(first["report_path"], second["report_path"])
            other_path = root / "other.map"
            other_level = _three_strip_map()
            other_level.sectors[0].floorpicnum = 11
            write_map(other_level, other_path)
            other_raw = other_path.read_bytes()
            other_sha = hashlib.sha256(other_raw).hexdigest()
            with database(config.knowledge_db) as db, db:
                db.execute("INSERT INTO contents(sha256,byte_size,parse_status) VALUES(?,?, 'ok')",
                           (other_sha, len(other_raw)))
            other = inspect_space(config, other_path, [0, 1])
            self.assertNotEqual(other["source"]["sha256"], first["source"]["sha256"])
            with database(config.knowledge_db) as db:
                rows = db.execute("SELECT sha256,extractor,kind,object_id,value_json FROM observations").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["sha256"] for row in rows}, {sha, other_sha})
            for row in rows:
                self.assertEqual(row["kind"], "spatial-analysis")
                self.assertEqual(json.loads(row["value_json"])["source"]["sha256"], row["sha256"])


if __name__ == "__main__":
    unittest.main()
