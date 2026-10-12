from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

try:
    from PIL import Image
except ImportError:
    Image = None

from bloodforge.compiler import compile_scene
from bloodforge.native import Sprite, encode_map
from bloodforge.scene import LevelScene, StairSpec
from bloodforge.spatial_views import SpatialViewError, compare_sequences, summarize_sequence


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(Image is None, "optional Pillow visual dependency is not installed")
class SpatialViewSequenceTests(unittest.TestCase):
    def setUp(self):
        data_root = Path(__file__).resolve().parents[1] / "data"
        data_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=data_root, prefix="spatial-view-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        assets = self.root / "ref" / "blood"
        assets.mkdir(parents=True)
        (assets / "BLOOD.RFF").write_bytes(b"rff bytes")
        (assets / "TILES000.ART").write_bytes(b"art bytes")
        self.assets = assets
        self.observer = self.root / "xmapedit" / "bin" / "xmapedit-observe.exe"
        self.observer.parent.mkdir(parents=True)
        self.observer.write_bytes(b"observer bytes")
        stair = StairSpec(
            id="steps", count=2, width=1024, tread=1024, rise=2048,
            origin_x=0, origin_y=0, floor_z=8192, ceiling_z=0,
            direction="east", floor_tile=1, ceiling_tile=2, wall_tile=3,
        )
        self.native = compile_scene(LevelScene(stairs=(stair,))).native_map
        self.native.sprites.append(Sprite(x=512, y=512, z=4096, sectnum=0, statnum=0))
        self.before_map = self.root / "before.MAP"
        self.before_map.write_bytes(encode_map(self.native))
        altered = copy.deepcopy(self.native)
        altered.walls[0].shade = 1
        self.after_map = self.root / "after.MAP"
        self.after_map.write_bytes(encode_map(altered))
        self.before = self._make_observation("before", self.before_map)
        self.after = self._make_observation("after", self.after_map)

    def _make_observation(self, name: str, map_path: Path):
        run = self.root / name
        render = run / "render"
        frames = render / "frames"
        frames.mkdir(parents=True)
        poses = [
            {"id": "entry", "x": 512, "y": 512, "z": 4096,
             "angle": 0, "horiz": 100, "sector": 0, "screenshot": True},
            {"id": "upper", "x": 1536, "y": 512, "z": 2048,
             "angle": 1024, "horiz": 100, "sector": 1, "screenshot": True},
        ]
        request = {
            "$schema": "llmapper.xmapedit-observation-request", "schema_version": 1,
            "map": str(map_path).replace("\\", "/"),
            "resource_dir": str(self.assets).replace("\\", "/"),
            "output": str(render).replace("\\", "/"),
            "width": 2, "height": 1, "screenshots": True, "brightness": 0,
            "views": poses,
        }
        request_path = run / "request.json"
        request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
        renderer = {"engine": "xmapedit classic Build software renderer",
                    "width": 2, "height": 1, "brightness": 0, "palette_authentic": True}
        views = []
        screenshots = []
        for view_index, pose in enumerate(poses):
            image_path = frames / f"{pose['id']}.png"
            image = Image.new("RGB", (2, 1), (view_index, 0, 0))
            image.save(image_path)
            surfaces = ([
                {"kind": "wall", "sector": 0, "wall": 0, "picnum": 3,
                 "shade": 0, "pal": 0, "pixels": 4, "columns": 2, "bbox": [0, 0, 1, 0]},
                {"kind": "floor", "sector": 1, "picnum": 1,
                 "shade": 0, "pal": 0, "pixels": 0, "columns": 0, "bbox": [0, 0, 0, 0]},
                {"kind": "sprite", "sector": 0, "sprite": 0, "picnum": 7,
                 "shade": 0, "pal": 0, "pixels": 1, "columns": 1, "bbox": [0, 0, 0, 0]},
            ] if view_index == 0 else [
                {"kind": "wall", "sector": 1, "wall": 4, "picnum": 3,
                 "shade": 0, "pal": 0, "pixels": 3, "columns": 1, "bbox": [1, 0, 1, 0]},
            ])
            camera = {key: pose[key] for key in ("x", "y", "z", "angle", "horiz", "sector")}
            views.append({"id": pose["id"], "status": "ok", "reason": None,
                          "camera": camera, "frame": {"pixels": 2, "painted": 2,
                                                          "sky_pixels": 0, "unwritten": 0},
                          "surfaces": surfaces,
                          "occluded": ([{"kind": "wall", "sector": 0, "wall": 1, "picnum": 3}]
                                       if view_index == 1 else []),
                          "unorderable": [], "screenshot": f"frames/{pose['id']}.png"})
            screenshots.append({"id": pose["id"], "path": str(image_path), "present": True,
                                "valid_png": True, "width": 2, "height": 1,
                                "size": image_path.stat().st_size, "sha256": _digest(image_path)})
        manifest = {"$schema": "llmapper.xmapedit-visual-observation", "schema_version": 1,
                    "map": {"path": str(map_path).replace("\\", "/"), "version": 1792,
                            "sectors": 2, "walls": 8, "sprites": 0},
                    "renderer": renderer, "views": views, "limitations": ["one static world state"]}
        manifest_path = render / "observation.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        map_identity = {"path": str(map_path), "size": map_path.stat().st_size,
                        "sha256": _digest(map_path)}
        executable_identity = {"path": str(self.observer), "size": self.observer.stat().st_size,
                               "sha256": _digest(self.observer)}
        asset_identities = [
            {"path": str(path), "size": path.stat().st_size, "sha256": _digest(path)}
            for path in sorted(self.assets.iterdir()) if path.is_file()
        ]
        records = []
        for path in [request_path, manifest_path, *sorted(frames.glob("*.png"))]:
            records.append({"relative_path": path.relative_to(run).as_posix(),
                            "size": path.stat().st_size, "sha256": _digest(path)})
        summary = {
            "$schema": "bloodforge.native-observation-summary", "schema_version": 1,
            "status": "completed",
            "claims": {"process_ok": True, "manifest_ok": True, "input_files_unchanged": True,
                       "outputs_contained": True, "requested_views_match": True,
                       "all_requested_views_rendered": True, "requested_screenshots_valid": True},
            "process": {"executable": str(self.observer), "cwd": str(run),
                        "exit_code": 0, "timeout_seconds": 60.0, "error": None},
            "map": str(map_path), "observer_manifest": str(manifest_path),
            "manifest_error": None, "renderer": renderer,
            "requested_view_ids": [pose["id"] for pose in poses],
            "returned_view_ids": [pose["id"] for pose in poses], "views": views,
            "screenshots": screenshots,
            "input_integrity": {"map_unchanged": True, "executable_unchanged": True,
                                "assets_unchanged": True,
                                "before": {"map": map_identity, "executable": executable_identity,
                                           "assets": asset_identities},
                                "after": {"map": map_identity, "executable": executable_identity,
                                          "assets": asset_identities}},
            "output_integrity": {"all_files_within_output_root": True,
                                 "files_before_summary_sidecar": records},
            "output_root": str(run),
        }
        summary_path = run / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary_path

    def _analysis(self, map_path: Path, *, names=("entry-space", "upper-space")):
        return {
            "schema": "bloodforge.spatial-understanding.v1",
            "source": {"sha256": _digest(map_path)},
            "scope": {"selected_sectors": [0, 1]},
            "native_counts": {"sectors": len(self.native.sectors),
                              "walls": len(self.native.walls), "sprites": len(self.native.sprites)},
            "sectors": [{"id": 0}, {"id": 1}],
            "regions": [
                {"id": names[0], "sectors": [0], "view": "coplanar",
                 "evidence_kind": "continuity-hypothesis"},
                {"id": names[1], "sectors": [1], "view": "finish",
                 "evidence_kind": "continuity-hypothesis"},
            ],
        }

    def test_summarizes_native_visibility_and_first_seen_sequence(self):
        result = summarize_sequence(self.before, self._analysis(self.before_map))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["views"][0]["visible_native_sectors"], [0])
        self.assertEqual(result["views"][0]["visible_regions"][0]["id"], "entry-space")
        self.assertEqual(result["views"][0]["visible_regions"][0]["contributing_native_refs"][0]["wall"], 0)
        self.assertEqual(result["views"][0]["visible_regions"][0]["summed_surface_pixel_samples"], 5)
        self.assertEqual(result["views"][1]["visible_native_sectors"], [1])
        self.assertEqual(result["transitions"][0]["gained_native_sectors"], [1])
        self.assertEqual(result["transitions"][0]["lost_native_sectors"], [0])
        self.assertEqual(result["first_seen"]["regions"]["upper-space"]["view_id"], "upper")
        self.assertEqual(result["first_seen"]["regions"]["upper-space"]["pose_key"], result["views"][1]["pose_key"])
        self.assertEqual(result["views"][1]["occluded_native_refs"][0]["wall"], 1)
        self.assertFalse(result["claims"]["traversability_inferred"])

    def test_rejects_analysis_for_another_source_hash(self):
        analysis = self._analysis(self.before_map)
        analysis["source"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(SpatialViewError, "does not match"):
            summarize_sequence(self.before, analysis)

    def test_rejects_duplicate_region_membership(self):
        analysis = self._analysis(self.before_map)
        analysis["regions"].append({"id": "duplicate", "sectors": [0], "view": "coplanar",
                                    "evidence_kind": "continuity-hypothesis"})
        with self.assertRaisesRegex(SpatialViewError, "repeats region membership"):
            summarize_sequence(self.before, analysis)

    def test_rejects_damaged_analysis_scope(self):
        analysis = self._analysis(self.before_map)
        analysis["scope"]["selected_sectors"] = [0]
        with self.assertRaisesRegex(SpatialViewError, "scope disagrees"):
            summarize_sequence(self.before, analysis)

    def test_rejects_surface_sector_that_does_not_own_wall(self):
        summary_path = self.before
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        manifest_path = Path(summary["observer_manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        wall_surface = next(surface for surface in manifest["views"][0]["surfaces"]
                            if "wall" in surface)
        wall_surface["sector"] = 1
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # Keep the sidecar's file identity current so the test reaches semantic
        # ownership validation rather than stopping at ordinary tamper checks.
        summary["output_integrity"]["files_before_summary_sidecar"] = [
            {"relative_path": record["relative_path"],
             "size": (summary_path.parent / record["relative_path"]).stat().st_size,
             "sha256": _digest(summary_path.parent / record["relative_path"])}
            if record["relative_path"] != "render/observation.json" else
            {"relative_path": record["relative_path"], "size": manifest_path.stat().st_size,
             "sha256": _digest(manifest_path)}
            for record in summary["output_integrity"]["files_before_summary_sidecar"]
        ]
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(SpatialViewError, "owned by MAP sector"):
            summarize_sequence(summary_path, self._analysis(self.before_map))

    def test_rejects_surface_sector_that_does_not_own_sprite(self):
        summary_path = self.before
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        manifest_path = Path(summary["observer_manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sprite_surface = next(surface for surface in manifest["views"][0]["surfaces"]
                              if "sprite" in surface)
        sprite_surface["sector"] = 1
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        records = summary["output_integrity"]["files_before_summary_sidecar"]
        summary["output_integrity"]["files_before_summary_sidecar"] = [
            {"relative_path": record["relative_path"],
             "size": manifest_path.stat().st_size, "sha256": _digest(manifest_path)}
            if record["relative_path"] == "render/observation.json" else
            {"relative_path": record["relative_path"],
             "size": (summary_path.parent / record["relative_path"]).stat().st_size,
             "sha256": _digest(summary_path.parent / record["relative_path"])}
            for record in records
        ]
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(SpatialViewError, "belongs to MAP sector"):
            summarize_sequence(summary_path, self._analysis(self.before_map))

    def test_rejects_current_map_tampering(self):
        self.before_map.write_bytes(b"tampered map")
        with self.assertRaisesRegex(SpatialViewError, "current input size|current input SHA-256"):
            summarize_sequence(self.before, self._analysis(self.before_map))

    def test_compares_same_poses_and_region_memberships_not_region_ids(self):
        before = summarize_sequence(self.before, self._analysis(self.before_map))
        after = summarize_sequence(self.after, self._analysis(self.after_map, names=("room-x", "room-y")))
        comparison = compare_sequences(before, after)
        self.assertEqual(comparison["region_join"]["matched_count"], 2)
        self.assertEqual(comparison["region_join"]["unmatched_before"], [])
        self.assertEqual(comparison["region_join"]["unmatched_after"], [])
        first_view = comparison["per_view"][0]
        self.assertEqual(first_view["sector_visibility_deltas"][0]["visibility_delta"], 0)
        self.assertEqual(first_view["region_visibility_deltas"][0]["before_id"], "entry-space")
        self.assertEqual(first_view["region_visibility_deltas"][0]["after_id"], "room-x")
        self.assertTrue(comparison["pose_invariants"]["exact_camera_poses_match"])
        self.assertEqual(comparison["pose_invariants"]["sector_index_stability_in_maps"],
                         "caller must establish from the controlled edit diff")

    def test_rejects_pose_change_between_counterfactual_sequences(self):
        before = summarize_sequence(self.before, self._analysis(self.before_map))
        after = summarize_sequence(self.after, self._analysis(self.after_map))
        after["views"][1]["camera"]["angle"] = 512
        with self.assertRaisesRegex(SpatialViewError, "camera pose differs"):
            compare_sequences(before, after)


if __name__ == "__main__":
    unittest.main()
