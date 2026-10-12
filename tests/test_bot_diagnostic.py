import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from bloodforge.compiler import compile_scene
from bloodforge.native import encode_map
from bloodforge.scene import LevelScene, StairSpec
from bloodforge.bot_diagnostic import _read_ndjson, _route_evidence, diagnose_bot_route


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
    def __init__(self, command, *, cwd, stdout, stderr, rows, timeout=False,
                 return_code=0, trace_bytes=0, **kwargs):
        self.command = command
        self.cwd = Path(cwd)
        self.returncode = None if timeout else return_code
        self.killed = False
        self.stdout = stdout
        self.stderr = stderr
        stdout.write(b"fake bot stdout\n")
        stderr.write(b"")
        (self.cwd / "nblood.log").write_text("NBlood test-build\n", encoding="utf-8")
        telemetry = Path(command[command.index("-bot_telemetry") + 1])
        trajectory = Path(command[command.index("-bot_trajectory") + 1])
        telemetry.write_text(json.dumps({"type": "summary", "result": "NO_KNOWN_ACTION"}) + "\n",
                             encoding="utf-8")
        trajectory.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        if trace_bytes:
            trajectory.write_bytes(b"x" * trace_bytes)

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class BotDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.root.mkdir()
        self.map_path = self.root / "artifacts" / "candidate.MAP"
        self.map_path.parent.mkdir()
        make_map(self.map_path)
        self.output = self.root / "traversal"
        self.assets = base / "blood-assets"
        self.assets.mkdir()
        (self.assets / "BLOOD.INI").write_text("reference", encoding="utf-8")
        engine_dir = base / "engine"
        engine_dir.mkdir()
        self.executable = engine_dir / "nblood.exe"
        self.executable.write_bytes(b"fake executable")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def rows(final_z=-512):
        return [
            {"tick": 10, "sector": 0, "z": 0, "on_ground": 1},
            {"tick": 20, "sector": 1, "z": -256, "on_ground": 1},
            {"tick": 30, "sector": 2, "z": final_z, "on_ground": 1},
        ]

    def run_fake(self, rows, *, timeout=False, return_code=0, trace_bytes=0,
                 trace_limit=None):
        clock = FakeClock()
        launched = {}

        def launch(command, **kwargs):
            process = FakeProcess(command, **kwargs, rows=rows, timeout=timeout,
                                  return_code=return_code, trace_bytes=trace_bytes)
            launched["process"] = process
            return process

        limit_patch = (patch("bloodforge.bot_diagnostic._MAX_TRACE_BYTES", trace_limit)
                       if trace_limit is not None else nullcontext())
        with patch("bloodforge.bot_diagnostic.os.name", "nt"), \
             patch("bloodforge.bot_diagnostic.subprocess.STARTUPINFO", return_value=type("Info", (), {"dwFlags": 0, "wShowWindow": 0})()), \
             patch("bloodforge.bot_diagnostic.subprocess.STARTF_USESHOWWINDOW", 1), \
             patch("bloodforge.bot_diagnostic.subprocess.SW_HIDE", 0), \
             patch("bloodforge.bot_diagnostic.subprocess.CREATE_NO_WINDOW", 0x08000000), \
             patch("bloodforge.bot_diagnostic.subprocess.Popen", side_effect=launch), \
             patch("bloodforge.bot_diagnostic.time.monotonic", side_effect=clock.monotonic), \
             patch("bloodforge.bot_diagnostic.time.sleep", side_effect=clock.sleep), \
             limit_patch:
            result = diagnose_bot_route(self.map_path, (0, 1, 2), self.output,
                                        self.executable, self.assets, data_root=self.root,
                                        game_seconds=5, max_wall_seconds=1)
        return result, launched.get("process")

    def test_observed_means_grounded_route_and_riser_deltas(self):
        result, process = self.run_fake(self.rows())
        self.assertEqual(result["status"], "diagnostic_observed")
        self.assertEqual(result["route_evidence"]["sectors_seen"], [0, 1, 2])
        self.assertEqual([item["actor_delta_z"] for item in result["route_evidence"]["transitions"]],
                         [-256, -256])
        self.assertTrue(result["claims"]["route_observed"])
        self.assertFalse(result["claims"]["traversal_verified"])
        self.assertTrue(result["experimental"])
        self.assertFalse(result["reliability_established"])
        self.assertTrue(result["claims"]["map_loaded"])
        self.assertTrue(result["claims"]["engine_initialized"])
        self.assertFalse(result["claims"]["controlled_route"])
        self.assertFalse(result["claims"]["player_spawn_verified"])
        self.assertFalse(result["claims"]["playability_verified"])
        self.assertNotIn("-bot_visible", process.command)
        self.assertIn("-bot_trajectory", process.command)
        self.assertTrue(result["assets"]["unchanged"])
        report = json.loads(Path(result["outputs"]["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "diagnostic_observed")
        self.assertTrue(Path(result["trajectory_file"]).is_file())

    def test_missing_final_riser_height_is_incomplete(self):
        result, _ = self.run_fake(self.rows(final_z=-256))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["claims"]["route_observed"])
        self.assertFalse(result["route_evidence"]["transitions"][-1]["rise_observed"])

    def test_timeout_never_claims_traversal_even_with_complete_rows(self):
        result, process = self.run_fake(self.rows(), timeout=True)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(process.killed)
        self.assertFalse(result["claims"]["map_loaded"])
        self.assertFalse(result["claims"]["engine_initialized"])
        self.assertFalse(result["claims"]["route_observed"])

    def test_trace_output_limit_kills_process_with_distinct_status(self):
        result, process = self.run_fake(self.rows(), timeout=True, trace_bytes=65,
                                        trace_limit=64)
        self.assertEqual(result["status"], "resource_limit")
        self.assertTrue(result["terminated_by_wrapper"])
        self.assertEqual(result["termination_reason"], "trace_output_limit")
        self.assertTrue(process.killed)

    def test_crash_and_nonportal_route_are_rejected(self):
        result, _ = self.run_fake(self.rows(), return_code=2)
        self.assertEqual(result["status"], "crashed")
        with patch("bloodforge.bot_diagnostic.subprocess.Popen") as launch:
            result = diagnose_bot_route(self.map_path, (0, 2), self.output,
                                        self.executable, self.assets, data_root=self.root)
        self.assertEqual(result["status"], "invalid_input")
        launch.assert_not_called()

    def test_ndjson_parser_caps_attempted_lines_and_reports_resource_limit(self):
        path = self.root / "samples.ndjson"
        path.write_text('not-json\nnot-json\nnot-json\n', encoding="utf-8")
        rows, errors, limited = _read_ndjson(path, max_lines=2)
        self.assertEqual(rows, [])
        self.assertTrue(limited)
        self.assertIn("trace_line_or_byte_limit_exceeded", errors)

    def test_ndjson_parser_caps_total_bytes_and_line_length(self):
        path = self.root / "samples.ndjson"
        path.write_text('123456789012345\n', encoding="utf-8")
        _, errors, limited = _read_ndjson(path, max_bytes=10, max_line_bytes=20)
        self.assertTrue(limited)
        self.assertIn("trace_byte_limit_exceeded:10", errors)
        _, errors, limited = _read_ndjson(path, max_bytes=100, max_line_bytes=8)
        self.assertTrue(limited)
        self.assertIn("trace_line_limit_exceeded:8", errors)

    def test_tiny_nonzero_rise_rejects_zero_actor_delta(self):
        stair = StairSpec("tiny", 3, 1024, 512, 1, 100, 200, 8192, 0,
                          "east", 10, 11, 12)
        level = compile_scene(LevelScene((stair,))).native_map
        rows = [
            {"tick": 1, "sector": 0, "z": 0, "on_ground": 1},
            {"tick": 2, "sector": 1, "z": 0, "on_ground": 1},
            {"tick": 3, "sector": 2, "z": 0, "on_ground": 1},
        ]
        evidence = _route_evidence(level, rows, (0, 1, 2))
        self.assertEqual(evidence["transitions"][0]["floor_delta_z"], -1)
        self.assertEqual(evidence["transitions"][0]["tolerance_z"], 0)
        self.assertFalse(evidence["all_floor_changes_observed"])


if __name__ == "__main__":
    unittest.main()
