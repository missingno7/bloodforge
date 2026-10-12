from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

try:
    from PIL import Image
except ImportError:
    Image = None

from bloodforge.visual_fidelity import VisualFidelityError, compare_observations


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(Image is None, "optional Pillow visual dependency is not installed")
class PairedVisualComparisonTests(unittest.TestCase):
    def setUp(self):
        data_root = Path(__file__).resolve().parents[1] / "data"
        data_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=data_root, prefix="visual-fidelity-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "llmapper" / "reference" / "blood"
        self.assets.mkdir(parents=True)
        (self.assets / "BLOOD.RFF").write_bytes(b"blood archive")
        (self.assets / "TILES000.ART").write_bytes(b"tile archive")
        self.observer = self.root / "llmapper" / "xmapedit" / "bin" / "xmapedit-observe.exe"
        self.observer.parent.mkdir(parents=True)
        self.observer.write_bytes(b"native observer")
        self.source = self._make_run("source", (10, 0, 0), (0, 0, 5))
        self.candidate = self._make_run("candidate", (20, 0, 0), (0, 0, 5))

    def _make_run(self, name, first_pixel, second_pixel):
        run = self.root / name
        render = run / "render"
        frames = render / "frames"
        frames.mkdir(parents=True)
        map_path = self.root / f"{name}.MAP"
        map_path.write_bytes(f"map-{name}".encode())
        image_path = frames / "view.png"
        Image.new("RGB", (2, 1)).putdata([first_pixel, second_pixel])
        image = Image.new("RGB", (2, 1))
        image.putdata([first_pixel, second_pixel])
        image.save(image_path)

        request = {
            "$schema": "llmapper.xmapedit-observation-request",
            "schema_version": 1,
            "map": str(map_path).replace("\\", "/"),
            "resource_dir": str(self.assets).replace("\\", "/"),
            "output": str(render).replace("\\", "/"),
            "width": 2,
            "height": 1,
            "screenshots": True,
            "brightness": 0,
            "views": [{"id": "view", "x": 10, "y": 20, "z": 30,
                       "angle": 512, "horiz": 100, "sector": 4,
                       "screenshot": True}],
        }
        request_path = run / "request.json"
        request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
        renderer = {"engine": "xmapedit classic Build software renderer",
                    "width": 2, "height": 1, "brightness": 0,
                    "palette_authentic": True}
        manifest = {
            "$schema": "llmapper.xmapedit-visual-observation",
            "schema_version": 1,
            "map": {"path": request["map"], "version": 1792,
                    "sectors": 5, "walls": 20, "sprites": 0},
            "renderer": renderer,
            "views": [{"id": "view", "status": "ok", "reason": None,
                       "camera": {"x": 10, "y": 20, "z": 30, "angle": 512,
                                  "horiz": 100, "sector": 4},
                       "frame": {"pixels": 2, "painted": 2, "sky_pixels": 0},
                       "surfaces": [], "occluded": [], "unorderable": [],
                       "screenshot": "frames/view.png"}],
            "limitations": ["one static world state"],
        }
        manifest_path = render / "observation.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        map_identity = {"path": str(map_path), "size": map_path.stat().st_size,
                        "sha256": _digest(map_path)}
        observer_identity = {"path": str(self.observer), "size": self.observer.stat().st_size,
                             "sha256": _digest(self.observer)}
        assets = [
            {"path": str(path), "size": path.stat().st_size, "sha256": _digest(path)}
            for path in sorted(self.assets.iterdir()) if path.is_file()
        ]
        input_before = {"map": map_identity, "executable": observer_identity,
                        "assets": assets}
        screenshot_sha = _digest(image_path)
        files = [request_path, manifest_path, image_path]
        output_records = [
            {"relative_path": path.relative_to(run).as_posix(),
             "size": path.stat().st_size, "sha256": _digest(path)}
            for path in files
        ]
        summary = {
            "$schema": "bloodforge.native-observation-summary",
            "schema_version": 1,
            "status": "completed",
            "claims": {"process_ok": True, "manifest_ok": True,
                       "input_files_unchanged": True, "outputs_contained": True,
                       "requested_views_match": True,
                       "all_requested_views_rendered": True,
                       "requested_screenshots_valid": True,
                       "visual_quality_judged": False,
                       "gameplay_or_door_motion_proven": False},
            "process": {"executable": str(self.observer), "cwd": str(run),
                        "exit_code": 0, "timeout_seconds": 60.0, "error": None},
            "map": str(map_path),
            "observer_manifest": str(manifest_path),
            "manifest_error": None,
            "renderer": renderer,
            "requested_view_ids": ["view"],
            "returned_view_ids": ["view"],
            "views": manifest["views"],
            "screenshots": [{"id": "view", "path": str(image_path),
                             "present": True, "valid_png": True,
                             "width": 2, "height": 1,
                             "size": image_path.stat().st_size,
                             "sha256": screenshot_sha}],
            "input_integrity": {"map_unchanged": True,
                                "executable_unchanged": True,
                                "assets_unchanged": True,
                                "before": input_before,
                                "after": input_before},
            "output_integrity": {"all_files_within_output_root": True,
                                 "files_before_summary_sidecar": output_records},
            "output_root": str(run),
        }
        summary_path = run / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return {"root": run, "request": request_path, "manifest": manifest_path,
                "image": image_path, "summary": summary_path}

    def _load_summary(self, run):
        return json.loads(run["summary"].read_text(encoding="utf-8"))

    def _save_summary(self, run, data):
        run["summary"].write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _refresh_output_hash(self, run, relative_path):
        summary = self._load_summary(run)
        record = next(item for item in summary["output_integrity"]["files_before_summary_sidecar"]
                      if item["relative_path"] == relative_path)
        path = run["root"] / relative_path
        record.update(size=path.stat().st_size, sha256=_digest(path))
        self._save_summary(run, summary)

    def test_reports_same_pose_per_pixel_differences_without_quality_claim(self):
        result = compare_observations(self.source["summary"], self.candidate["summary"])
        self.assertEqual(result["claim"], "paired-static-view pixel comparison only")
        self.assertTrue(result["claims"]["same_observer_sha256"])
        self.assertFalse(result["claims"]["automatic_verification"])
        self.assertFalse(result["claims"]["overall_quality_judged"])
        view = result["views"][0]
        self.assertEqual(view["image_dimensions"], {"width": 2, "height": 1})
        self.assertEqual(view["pixel_count"], 2)
        self.assertEqual(view["equal_pixel_count"], 1)
        self.assertEqual(view["changed_pixel_count"], 1)
        self.assertEqual(view["mae_per_channel"], {"red": 5.0, "green": 0.0, "blue": 0.0})

    def test_rejects_changed_camera_pose(self):
        request = json.loads(self.candidate["request"].read_text(encoding="utf-8"))
        request["views"][0]["angle"] = 1024
        self.candidate["request"].write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
        self._refresh_output_hash(self.candidate, "request.json")
        with self.assertRaisesRegex(VisualFidelityError, "returned pose differs"):
            compare_observations(self.source["summary"], self.candidate["summary"])

    def test_rejects_different_observer_executable(self):
        summary = self._load_summary(self.candidate)
        summary["input_integrity"]["before"]["executable"]["sha256"] = "f" * 64
        summary["input_integrity"]["after"]["executable"]["sha256"] = "f" * 64
        self._save_summary(self.candidate, summary)
        with self.assertRaisesRegex(VisualFidelityError, "current input SHA-256 differs"):
            compare_observations(self.source["summary"], self.candidate["summary"])

    def test_rejects_failed_input_integrity_claim(self):
        summary = self._load_summary(self.candidate)
        summary["claims"]["input_files_unchanged"] = False
        summary["input_integrity"]["map_unchanged"] = False
        self._save_summary(self.candidate, summary)
        with self.assertRaisesRegex(VisualFidelityError, "success/integrity claims"):
            compare_observations(self.source["summary"], self.candidate["summary"])

    def test_rejects_png_tampering_after_observation(self):
        Image.new("RGB", (2, 1), (255, 0, 0)).save(self.candidate["image"])
        with self.assertRaisesRegex(VisualFidelityError, "no longer matches its recorded output identity"):
            compare_observations(self.source["summary"], self.candidate["summary"])

    def test_rejects_current_map_tampering(self):
        map_path = Path(self._load_summary(self.candidate)["map"])
        map_path.write_bytes(b"x" * map_path.stat().st_size)  # Same length, so this exercises SHA-256.
        with self.assertRaisesRegex(VisualFidelityError, "current input SHA-256 differs"):
            compare_observations(self.source["summary"], self.candidate["summary"])

    def test_rejects_current_observer_tampering(self):
        self.observer.write_bytes(b"tampered observer")
        with self.assertRaisesRegex(VisualFidelityError, "current input size differs|current input SHA-256 differs"):
            compare_observations(self.source["summary"], self.candidate["summary"])

    def test_rejects_current_resource_tampering(self):
        resource = self.assets / "BLOOD.RFF"
        resource.write_bytes(b"x" * resource.stat().st_size)  # Same length as the recorded resource.
        with self.assertRaisesRegex(VisualFidelityError, "current input SHA-256 differs"):
            compare_observations(self.source["summary"], self.candidate["summary"])

    def test_rejects_missing_current_resource(self):
        (self.assets / "TILES000.ART").unlink()
        with self.assertRaisesRegex(VisualFidelityError, "current input file is missing or unreadable"):
            compare_observations(self.source["summary"], self.candidate["summary"])


if __name__ == "__main__":
    unittest.main()
