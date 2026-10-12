import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from bloodforge.observe import CameraPose, ObserverError, observe_map


class ObserverAdapterTests(unittest.TestCase):
    def setUp(self):
        data_parent = Path(__file__).resolve().parents[1] / "data"
        data_parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=data_parent, prefix="observer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "bloodforge" / "data"
        self.data.mkdir(parents=True)
        self.current_data = self.data
        self.map_path = self.data / "candidate.MAP"
        self.map_path.write_bytes(b"candidate map bytes")
        self.expected_map = self.map_path
        self.expected_ids = ["entry", "stairs"]
        self.invalid_stairs = False
        self.binary = self.root / "llmapper" / "xmapedit" / "bin" / "xmapedit-observe.exe"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"observer binary bytes")
        self.assets = self.root / "llmapper" / "reference" / "blood"
        self.assets.mkdir(parents=True)
        (self.assets / "BLOOD.RFF").write_bytes(b"palette archive")
        (self.assets / "TILES000.ART").write_bytes(b"tile archive")
        (self.assets / "TILES001.ART").write_bytes(b"second tile archive")
        self.data_root_patch = patch("bloodforge.observe._default_data_root", return_value=self.data)
        self.data_root_patch.start()
        self.addCleanup(self.data_root_patch.stop)

    def _successful_observer(self, args, *, cwd, capture_output, text, timeout, check, **kwargs):
        self.assertTrue(Path(cwd).is_relative_to(self.current_data))
        request_path = Path(args[1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(Path(request["map"]), self.expected_map)
        self.assertEqual(Path(request["resource_dir"]), self.assets)
        self.assertEqual(Path(request["output"]), Path(cwd) / "render")
        self.assertEqual([view["id"] for view in request["views"]], self.expected_ids)

        render_dir = Path(request["output"])
        frames = render_dir / "frames"
        frames.mkdir(parents=True)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480)
        (frames / "entry.png").write_bytes(png)
        manifest = {
            "$schema": "llmapper.xmapedit-visual-observation",
            "schema_version": 1,
            "map": {"path": request["map"], "version": 1792, "sectors": 4,
                    "walls": 16, "sprites": 0},
            "renderer": {"engine": "xmapedit classic Build software renderer",
                         "width": 640, "height": 480, "palette_authentic": True},
            "views": [
                {"id": "entry", "status": "ok",
                 "camera": {"x": 100, "y": 100, "z": 2048, "angle": 0,
                            "horiz": 100, "sector": 0},
                 "frame": {"pixels": 307200, "painted": 172000, "sky_pixels": 0},
                 "surfaces": [{"kind": "wall", "sector": 0, "wall": 1,
                               "picnum": 110, "pixels": 5100}],
                 "occluded": [], "unorderable": [], "screenshot": "frames/entry.png"},
                {"id": "stairs", "status": "invalid_pose" if self.invalid_stairs else "ok",
                 "reason": "outside sector" if self.invalid_stairs else None,
                 "camera": {"x": 1, "y": 1, "z": 1, "angle": 0, "horiz": 100,
                            "sector": -1}, "surfaces": [], "screenshot": None},
            ],
            "limitations": ["one static world state"],
        }
        (render_dir / "observation.json").write_text(json.dumps(manifest), encoding="utf-8")
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, "observer finished", "wrote observation.json")

    def test_records_rendered_views_surfaces_png_and_input_integrity(self):
        poses = [
            CameraPose("entry", 100, 100, 2048, sector=0),
            {"id": "stairs", "x": 1, "y": 1, "z": 1, "screenshot": False},
        ]
        with patch("bloodforge.observe.subprocess.run", side_effect=self._successful_observer):
            result = observe_map(
                self.map_path, self.data / "observe-run", self.binary, self.assets, poses,
            )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["claims"]["process_ok"])
        self.assertTrue(result["claims"]["manifest_ok"])
        self.assertTrue(result["claims"]["input_files_unchanged"])
        self.assertFalse(result["claims"]["gameplay_or_door_motion_proven"])
        self.assertEqual([view["status"] for view in result["views"]], ["ok", "ok"])
        self.assertEqual(result["views"][0]["surfaces"][0]["picnum"], 110)
        self.assertEqual(result["screenshots"][0]["width"], 640)
        self.assertEqual(result["screenshots"][0]["height"], 480)
        self.assertTrue((self.data / "observe-run" / "summary.json").is_file())
        self.assertTrue((self.data / "observe-run" / "render" / "observation.json").is_file())
        self.assertEqual(result["process"]["cwd"], str(self.data / "observe-run"))

    def test_fails_closed_when_output_is_outside_data_but_allows_reference_map(self):
        with self.assertRaisesRegex(ObserverError, "output_dir must be a child"):
            observe_map(
                self.map_path, self.root / "outside", self.binary, self.assets,
                CameraPose("entry", 1, 2, 3),
            )
        reference = self.root / "llmapper" / "maps"
        reference.mkdir(parents=True)
        original_map = reference / "original.MAP"
        original_map.write_bytes(b"original map bytes")
        self.expected_map = original_map
        self.expected_ids = ["entry", "stairs"]
        with patch("bloodforge.observe.subprocess.run", side_effect=self._successful_observer):
            result = observe_map(
                original_map, self.data / "safe", self.binary, self.assets,
                [CameraPose("entry", 100, 100, 2048, sector=0),
                 CameraPose("stairs", 150, 100, 2048, sector=0, screenshot=False)],
            )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["input_integrity"]["map_unchanged"])

    def test_data_root_can_be_explicit_and_outputs_cannot_overlap_asset_or_binary_dirs(self):
        configured_data = self.root / "configured-bloodforge" / "data"
        configured_data.mkdir(parents=True)
        configured_map = configured_data / "candidate.MAP"
        configured_map.write_bytes(b"candidate")
        self.current_data = configured_data
        with patch("bloodforge.observe.subprocess.run", side_effect=self._successful_observer):
            # The configured root is independent of the package's default data path.
            self.expected_map = configured_map
            self.expected_ids = ["entry", "stairs"]
            result = observe_map(
                configured_map, configured_data / "run", self.binary, self.assets,
                [CameraPose("entry", 100, 100, 2048, sector=0),
                 CameraPose("stairs", 150, 100, 2048, sector=0, screenshot=False)],
                data_root=configured_data,
            )
        self.assertEqual(result["status"], "completed")

        local_assets = configured_data / "assets"
        local_assets.mkdir()
        (local_assets / "BLOOD.RFF").write_bytes(b"rff")
        (local_assets / "TILES000.ART").write_bytes(b"art")
        with self.assertRaisesRegex(ObserverError, "overlap the read-only asset"):
            observe_map(configured_map, local_assets / "render", self.binary,
                        local_assets, CameraPose("entry", 1, 2, 3), data_root=configured_data)

    def test_invalid_pose_prevents_a_completed_claim(self):
        self.invalid_stairs = True
        with patch("bloodforge.observe.subprocess.run", side_effect=self._successful_observer):
            result = observe_map(
                self.map_path, self.data / "invalid-pose", self.binary, self.assets,
                [CameraPose("entry", 100, 100, 2048, sector=0),
                 CameraPose("stairs", 1, 1, 1, screenshot=False)],
            )
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["claims"]["process_ok"])
        self.assertFalse(result["claims"]["all_requested_views_rendered"])
        self.assertEqual(result["views"][1]["reason"], "outside sector")

    def test_rejects_unsafe_camera_ids_and_stale_output(self):
        with self.assertRaisesRegex(ObserverError, "camera id"):
            CameraPose("../outside", 1, 2, 3)
        out = self.data / "used"
        out.mkdir()
        (out / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ObserverError, "must be empty"):
            observe_map(self.map_path, out, self.binary, self.assets, CameraPose("entry", 1, 2, 3))
        self.assertEqual((out / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_preserves_failure_claims_when_process_exits_nonzero(self):
        from subprocess import CompletedProcess
        with patch("bloodforge.observe.subprocess.run",
                   return_value=CompletedProcess([], 2, "", "bad request")):
            result = observe_map(
                self.map_path, self.data / "failed-run", self.binary, self.assets,
                CameraPose("entry", 1, 2, 3),
            )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["claims"]["process_ok"])
        self.assertFalse(result["claims"]["manifest_ok"])
        self.assertTrue(result["claims"]["input_files_unchanged"])
        self.assertEqual(result["process"]["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
