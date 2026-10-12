import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bloodforge.compiler import compile_scene
from bloodforge.native import encode_map
from bloodforge.runtime import test_load
from bloodforge.scene import LevelScene, StairSpec


def make_map(path):
    stair = StairSpec("test", 3, 1024, 512, 256, 100, 200, 8192, 0,
                      "east", 10, 11, 12)
    path.write_bytes(encode_map(compile_scene(LevelScene((stair,))).native_map))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 0.2
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeProcess:
    def __init__(self, command, *, cwd, stdout, stderr, finish_early=False,
                 markers=True, assets_to_mutate=None, **kwargs):
        self.command = command
        self.cwd = Path(cwd)
        self.stdout = stdout
        self.stderr = stderr
        self.finish_early = finish_early
        self.markers = markers
        self.assets_to_mutate = assets_to_mutate
        self.returncode = None
        self.killed = False
        self.pid = 12345
        stdout.write(b"fake stdout\n")
        stderr.write(b"fake stderr\n")
        if markers:
            (self.cwd / "nblood.log").write_text(
                "NBlood test-build\nBLOODMAP_ORACLE_BOOTSTRAPPED\n"
                "Waiting for network players!\nModern types erased: 0\n",
                encoding="utf-8",
            )
        if assets_to_mutate is not None:
            assets_to_mutate.write_bytes(b"changed")

    def poll(self):
        if self.finish_early:
            self.returncode = 2
        return self.returncode

    def kill(self):
        self.killed = True
        if self.returncode is None:
            self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = -9
        return self.returncode


class RuntimeSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.root.mkdir()
        self.map_path = self.root / "artifacts" / "candidate.MAP"
        self.map_path.parent.mkdir()
        make_map(self.map_path)
        self.output = self.root / "runtime"
        self.assets = Path(self.temp.name) / "blood-assets"
        self.assets.mkdir()
        (self.assets / "BLOOD.INI").write_text("reference", encoding="utf-8")
        engine_dir = Path(self.temp.name) / "engine-dir"
        engine_dir.mkdir()
        self.executable = engine_dir / "nblood.exe"
        self.executable.write_bytes(b"fake executable")

    def tearDown(self):
        self.temp.cleanup()

    def run_fake(self, *, finish_early=False, markers=True, mutate_assets=False):
        clock = FakeClock()
        process_instance = {}

        def launch(command, **kwargs):
            process = FakeProcess(command, **kwargs, finish_early=finish_early,
                                  markers=markers,
                                  assets_to_mutate=(self.assets / "BLOOD.INI") if mutate_assets else None)
            process_instance["value"] = process
            return process

        with patch("bloodforge.runtime.os.name", "nt"), \
             patch("bloodforge.runtime.subprocess.STARTUPINFO", return_value=type("Info", (), {"dwFlags": 0, "wShowWindow": 0})()), \
             patch("bloodforge.runtime.subprocess.STARTF_USESHOWWINDOW", 1), \
             patch("bloodforge.runtime.subprocess.SW_HIDE", 0), \
             patch("bloodforge.runtime.subprocess.CREATE_NO_WINDOW", 0x08000000), \
             patch("bloodforge.runtime.subprocess.Popen", side_effect=launch), \
             patch("bloodforge.runtime.time.monotonic", side_effect=clock.monotonic), \
             patch("bloodforge.runtime.time.sleep", side_effect=clock.sleep):
            result = test_load(self.map_path, self.output, self.executable,
                               self.assets, data_root=self.root, seconds=1)
        return result, process_instance["value"], clock

    def test_initialized_smoke_is_hidden_isolated_and_audited(self):
        result, process, _ = self.run_fake()
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["claims"], {"map_loaded": True, "engine_initialized": True,
                                             "player_spawn_verified": False,
                                             "stair_traversal_verified": False,
                                             "playability_verified": False})
        self.assertTrue(result["terminated_by_wrapper"])
        self.assertEqual(result["initialized_markers"],
                         {"autoexec": True, "game_loop": True, "map_initialization": True})
        self.assertEqual(result["engine"]["version"], "test-build")
        self.assertTrue(result["assets"]["unchanged"])
        self.assertTrue(result["map"]["source_unchanged"])
        self.assertTrue(result["engine"]["unchanged"])
        self.assertEqual(result["map"]["sha256"], result["map"]["copied_sha256"])
        self.assertTrue(Path(result["outputs"]["config"]).is_absolute())
        self.assertTrue(Path(result["outputs"]["report"]).is_file())
        self.assertEqual(json.loads(Path(result["outputs"]["report"]).read_text())["status"], "initialized")
        self.assertIn("-usecwd", process.command)
        staged_assets = Path(process.command[process.command.index("-game_dir") + 1])
        self.assertTrue(staged_assets.is_relative_to(self.root))
        self.assertTrue((staged_assets / "BLOOD.INI").is_file())
        self.assertEqual(process.command[process.command.index("-cfg") + 1], result["outputs"]["config"])
        self.assertTrue(process.killed)
        self.assertTrue(Path(result["run_dir"]).is_relative_to(self.root))

    def test_timeout_and_early_crash_are_distinct(self):
        timeout, _, _ = self.run_fake(markers=False)
        self.assertEqual(timeout["status"], "timeout")
        crashed, process, _ = self.run_fake(finish_early=True, markers=False)
        self.assertEqual(crashed["status"], "crashed")
        self.assertEqual(crashed["return_code"], 2)
        self.assertFalse(process.killed)

    def test_changed_reference_asset_is_reported(self):
        result, _, _ = self.run_fake(mutate_assets=True)
        self.assertEqual(result["status"], "asset_modified")
        self.assertFalse(result["assets"]["unchanged"])
        self.assertEqual(result["assets"]["changed_paths"], ["BLOOD.INI"])

    def test_invalid_map_does_not_launch_engine_and_paths_are_confined(self):
        invalid = self.root / "bad.MAP"
        invalid.write_bytes(b"bad")
        with patch("bloodforge.runtime.subprocess.Popen") as launch:
            result = test_load(invalid, self.output, self.executable,
                               self.assets, data_root=self.root)
        self.assertEqual(result["status"], "invalid_input")
        launch.assert_not_called()
        with self.assertRaises(ValueError):
            test_load(self.map_path, Path(self.temp.name) / "outside", self.executable,
                      self.assets, data_root=self.root)

    def test_missing_engine_or_assets_is_not_reported_as_success(self):
        result = test_load(self.map_path, self.output, self.executable.parent / "absent.exe",
                           self.assets, data_root=self.root)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("does not exist", result["reason"])


if __name__ == "__main__":
    unittest.main()
