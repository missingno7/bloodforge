from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from bloodforge.cli import main


class CliTests(unittest.TestCase):
    def test_scene_to_compiled_artifact_through_public_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            corpus = base / "corpus"
            corpus.mkdir()
            data = base / "data"
            prefix = ["--corpus-root", str(corpus), "--data-root", str(data)]

            def command(*args):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main([*prefix, *args])
                self.assertEqual(code, 0, err.getvalue())
                return json.loads(out.getvalue())

            command("init")
            example = Path(__file__).resolve().parents[1] / "examples" / "stair.scene.json"
            command("scene", "create", "cli-test", "--file", str(example))
            compiled = command("level", "compile", "--project", "cli-test")
            self.assertTrue(Path(compiled["artifact_path"]).is_file())
            checked = command("level", "validate", compiled["artifact_path"])
            self.assertEqual(checked["summary"]["error_count"], 0)
            self.assertEqual(command("scene", "inspect", "cli-test")["revision"], 0)
            again = command("level", "compile", "--project", "cli-test")
            self.assertEqual(compiled["artifact_hash"], again["artifact_hash"])

    def test_missing_map_returns_structured_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["--corpus-root", temp, "--data-root", str(Path(temp) / "other"),
                             "level", "validate", str(Path(temp) / "absent.map")])
            # Invalid output/corpus nesting fails closed before any writes.
            self.assertEqual(code, 2)
            self.assertTrue(json.loads(err.getvalue())["message"])
