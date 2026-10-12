import json
from pathlib import Path
import tempfile
import unittest

from bloodforge.config import Config
from bloodforge.evidence import bundle_checkpoint


class EvidenceTests(unittest.TestCase):
    def test_unrelated_map_cannot_receive_runtime_or_visual_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            values = [{"results": []}, {"lineage": {"artifact_hash": "generated-a"}}, {},
                      {"input_integrity": {"before": {"map": {"sha256": "generated-a"}}}},
                      {"map": {"sha256": "different-generated-map"}}]
            paths = []
            for i, value in enumerate(values):
                path = base / f"input-{i}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            config = Config(base / "corpus", base / "data")
            with self.assertRaisesRegex(ValueError, "same generated MAP"):
                bundle_checkpoint(config, *paths)
            self.assertFalse(config.knowledge_db.exists())
            self.assertFalse(config.workspace_db.exists())
