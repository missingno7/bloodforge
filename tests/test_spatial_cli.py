from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from bloodforge.cli import main
from bloodforge.compiler import compile_scene
from bloodforge.native import NativeMap, Sector, Wall, encode_map, read_map
from bloodforge.scene import AreaSpec, ConnectionSpec, LevelScene, PortalEndpoint


def synthetic_connected_map() -> NativeMap:
    left = AreaSpec("left", 0, 0, 100, 100, 0, -12000)
    right = AreaSpec("right", 100, 0, 100, 100, 0, -12000)
    scene = LevelScene(areas=(left, right), connections=(ConnectionSpec(
        "door", PortalEndpoint("left", "east"), PortalEndpoint("right", "west")),))
    return compile_scene(scene).native_map


class SpatialCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        self.data = self.root / "data"
        self.map_dir = self.root / "maps"
        self.map_dir.mkdir()
        self.source = self.map_dir / "source.map"
        self.source_bytes = encode_map(synthetic_connected_map())
        self.source.write_bytes(self.source_bytes)
        self.prefix = ["--corpus-root", str(self.corpus), "--data-root", str(self.data)]
        self._run("init", expected=0)

    def _run(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([*self.prefix, *map(str, args)])
        stream = out if code == 0 else err
        try:
            payload = json.loads(stream.getvalue())
        except json.JSONDecodeError as exc:
            self.fail(f"CLI returned non-JSON output (code {code}): {out.getvalue()!r} {err.getvalue()!r}: {exc}")
        self.assertEqual(code, expected, payload)
        return payload

    def test_inspect_transitions_and_diagram_are_source_bound_and_persisted(self):
        diagram = self.root / "reports" / "space.svg"
        inspected = self._run("space", "inspect", self.source, "--diagram", diagram)
        source_sha = hashlib.sha256(self.source_bytes).hexdigest()
        self.assertEqual(inspected["source"]["sha256"], source_sha)
        self.assertEqual(inspected["source"]["path"], str(self.source.resolve()))
        self.assertTrue(inspected["report_path"])
        self.assertTrue(Path(inspected["report_path"]).is_file())
        self.assertEqual(inspected["questions"]["what_negative_space_is_measured"]["mapped_sector_area_xy"], 20000)
        self.assertTrue(diagram.is_file())
        svg = diagram.read_text(encoding="utf-8")
        self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', svg)
        self.assertIn("Native sector footprints", svg)

        transitions = self._run("space", "transitions", self.source, "--sectors", "0,1")
        self.assertEqual(transitions["source"]["sha256"], source_sha)
        self.assertEqual(len(transitions["openings"]), 1)
        self.assertEqual(transitions["openings"][0]["sectors"], [0, 1])
        self.assertEqual(transitions["openings"][0]["navigation"], "unverified")

    def test_viewpoints_writes_validated_static_camera_json(self):
        route = self.root / "route.json"
        output = self.root / "views" / "cameras.json"
        route.write_text(json.dumps([{"x": 50, "y": 50, "z": -6000,
                                      "angle": 0, "sector": 0}]), encoding="utf-8")
        result = self._run("space", "viewpoints", self.source, "--route", route,
                           "--samples", "2", "--output", output)
        self.assertEqual(result["map_sha256"], hashlib.sha256(self.source_bytes).hexdigest())
        self.assertIn("movement unverified", result["claim"])
        self.assertTrue(output.is_file())
        cameras = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(cameras), 1)
        self.assertEqual(cameras[0], {"id": "route_0000", "x": 50, "y": 50, "z": -6000,
                                      "angle": 0, "horiz": 100, "sector": 0, "screenshot": True})

    def test_local_inspect_and_transitions_expose_global_failure(self):
        damaged = read_map(self.source)
        start = len(damaged.walls)
        damaged.sectors.append(Sector(wallptr=start, wallnum=2))
        damaged.walls.extend([Wall(x=1000, y=1000, point2=start+1),
                              Wall(x=1001, y=1001, point2=start)])
        self.source.write_bytes(encode_map(damaged))
        self._run("space", "inspect", self.source, expected=2)
        for command in ("inspect", "transitions"):
            report = self._run("space", command, self.source, "--sectors", "0,1")
            self.assertEqual(report["scope"]["selected_sectors"], [0, 1])
            self.assertFalse(report["scope_validation"]["whole_map_structural_gate_passed"])
            self.assertEqual(report["scope_validation"]["whole_map_error_count"], 2)
            full = json.loads(Path(report["report_path"]).read_text())
            self.assertEqual(full["scope_validation"]["whole_map_dimensions"]["geometry"]["error_count"], 2)
            self.assertIsNone(full["whole_level"]["reciprocal_components"])

    def test_intervention_hash_precondition_persists_counterfactual_and_compare(self):
        original = read_map(self.source)
        wall = next(index for index, item in enumerate(original.walls) if item.nextwall >= 0)
        spec_path = self.root / "intervention.json"
        spec_path.write_text(json.dumps({
            "expected_source_sha256": hashlib.sha256(self.source_bytes).hexdigest(),
            "edit": {"kind": "close_portal", "wall": wall},
        }), encoding="utf-8")
        report = self._run("space", "intervene", self.source, "--spec", spec_path)
        self.assertEqual(report["source_sha256"], hashlib.sha256(self.source_bytes).hexdigest())
        self.assertEqual(report["audit"]["kind"], "close_portal")
        self.assertTrue(report["audit"]["invariants"]["record_counts_preserved"])
        self.assertFalse(report["audit"]["invariants"]["portal_graph_preserved"])
        artifact = Path(report["artifact_path"])
        self.assertTrue(artifact.is_file())
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), report["artifact_hash"])
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertTrue(report["report_path"])
        self.assertTrue(Path(report["report_path"]).is_file())

        compared = self._run("space", "compare", self.source, artifact,
                             "--sectors", "0,1")
        self.assertEqual(compared["source_hashes"],
                         [hashlib.sha256(self.source_bytes).hexdigest(), report["artifact_hash"]])
        self.assertEqual(compared["portal_pairs_removed"], [[min(wall, original.walls[wall].nextwall),
                                                              max(wall, original.walls[wall].nextwall)]])
        self.assertTrue(compared["native_counts_unchanged"])
        self.assertFalse(compared["runtime_verified"])

        stale = json.loads(spec_path.read_text(encoding="utf-8"))
        stale["expected_source_sha256"] = "0" * 64
        spec_path.write_text(json.dumps(stale), encoding="utf-8")
        error = self._run("space", "intervene", self.source, "--spec", spec_path, expected=2)
        self.assertIn("SHA-256 precondition failed", error["message"])

    def test_compare_rejects_same_count_map_with_remapped_geometry(self):
        changed = read_map(self.source)
        changed.walls[0].x += 1
        remapped = self.map_dir / "remapped.map"
        remapped.write_bytes(encode_map(changed))
        result = self._run("space", "compare", self.source, remapped,
                           "--sectors", "0,1", expected=2)
        self.assertIn("unchanged indexed wall XY", result["message"])


if __name__ == "__main__":
    unittest.main()
