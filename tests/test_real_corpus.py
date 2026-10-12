"""Optional read-only integration tests; no commercial fixtures are bundled."""
import os
from pathlib import Path
import unittest

from bloodforge.native import encode_map, read_map
from bloodforge.patterns import discover_stairs


class RealCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default = Path(__file__).resolve().parents[2] / "llmapper" / "maps" / "blood"
        cls.root = Path(os.environ.get("BLOODFORGE_CORPUS_ROOT", str(default)))
        if not (cls.root / "campaign" / "E2M5.MAP").is_file():
            raise unittest.SkipTest("local original Blood corpus unavailable; set BLOODFORGE_CORPUS_ROOT")

    def test_original_campaign_roundtrips_byte_exactly(self):
        paths = sorted(path for path in (self.root / "campaign").iterdir()
                       if path.is_file() and path.suffix.lower() == ".map")
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(map=path.name):
                self.assertEqual(encode_map(read_map(path)), path.read_bytes())

    def test_e2m5_stair_evidence_has_native_references(self):
        source = read_map(self.root / "campaign" / "E2M5.MAP")
        candidates = discover_stairs(source)
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(candidate.status, "candidate")
            self.assertEqual(len(candidate.source_sector_indices), candidate.parameters["count"])
            for left, right, wall in zip(candidate.source_sector_indices, candidate.source_sector_indices[1:],
                                         candidate.portal_wall_ids):
                self.assertEqual(source.walls[wall].nextsector, right)
                self.assertLess(source.sectors[right].floorz, source.sectors[left].floorz)
