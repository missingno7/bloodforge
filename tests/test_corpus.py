import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from bloodforge.config import Config
from bloodforge.corpus import corpus_search, corpus_summary, discover, ingest
from bloodforge.native import NativeMap, Sector, encode_map
from bloodforge.store import database


def _map_bytes(marker: int = 0) -> bytes:
    return encode_map(NativeMap(
        sectors=[Sector(wallnum=0, floorz=8192 + marker)],
        start_x=marker,
    ))


class CorpusTests(unittest.TestCase):
    def setUp(self):
        # The managed runner restricts the host TEMP directory, so keep the
        # automatically cleaned fixture tree inside the writable workspace.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.base = Path(self.temp.name)
        self.root = self.base / "corpus"
        self.data = self.base / "data"
        self.root.mkdir()
        self.config = Config(self.root, self.data)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, data: bytes | None = None) -> Path:
        path = self.root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_map_bytes() if data is None else data)
        return path

    def test_discovery_classifies_from_path_and_tiered_is_view(self):
        self.write("campaign/E1M1.MAP")
        self.write("campaign/multiplayer/BB1.MAP")
        self.write("community/arena.map")
        self.write("conversions/DNE1.MAP")
        self.write("curated/multiplayer/DM1.MAP")
        self.write("mechanism/Vanilla/SPRITE-ITEMS.map")
        self.write("tiered/S/arena.map")

        rows = {row.relative_path: row for row in discover(self.config)}
        self.assertEqual(rows["campaign/E1M1.MAP"].population, "blood-campaign")
        self.assertEqual(rows["campaign/E1M1.MAP"].mode, "sp")
        self.assertEqual(rows["campaign/multiplayer/BB1.MAP"].population, "blood-bloodbath")
        self.assertEqual(rows["campaign/multiplayer/BB1.MAP"].mode, "multiplayer")
        self.assertEqual(rows["community/arena.map"].population, "community")
        self.assertEqual(rows["community/arena.map"].mode, "unknown")
        self.assertEqual(rows["conversions/DNE1.MAP"].population, "own-conversion")
        self.assertEqual(rows["curated/multiplayer/DM1.MAP"].mode, "multiplayer")
        self.assertEqual(rows["mechanism/Vanilla/SPRITE-ITEMS.map"].population, "mechanism-tutorial")
        self.assertTrue(rows["tiered/S/arena.map"].is_view)
        self.assertEqual(rows["tiered/S/arena.map"].tier, "S")

    def test_ingest_deduplicates_content_but_preserves_paths_and_tier_views(self):
        shared = _map_bytes(77)
        self.write("community/arena.map", shared)
        self.write("community/arena-alias.map", shared)
        self.write("tiered/S/arena.map", shared)
        self.write("campaign/E1M1.MAP", _map_bytes(1))

        result = ingest(self.config)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["discovered"], 4)
        self.assertEqual(result["parsed"], 2)
        self.assertEqual(result["skipped"], 2)

        with database(self.config.knowledge_db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contents").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sources WHERE active=1").fetchone()[0], 4)
            community = db.execute(
                "SELECT relative_path,tier,is_view FROM sources WHERE population='community' ORDER BY relative_path"
            ).fetchall()
            self.assertEqual([(row[0], row[1], row[2]) for row in community], [
                ("community/arena-alias.map", "S", 0),
                ("community/arena.map", "S", 0),
                ("tiered/S/arena.map", "S", 1),
            ])
            summary = corpus_summary(db)
            self.assertEqual(summary["active_sources"], 4)
            self.assertEqual(summary["duplicate_source_hash_groups"], 1)
            self.assertEqual(summary["repeated_source_paths"], 1)
            self.assertEqual(summary["native_record_rows"]["sectors"], 2)
            self.assertGreater(summary["observations"], 0)
            self.assertEqual(result["known_failed_content"], 0)
            found = corpus_search(db, "arena", population="community")
            self.assertEqual(len(found), 3)
            self.assertTrue(any(row["is_view"] for row in found))

        second = ingest(self.config)
        self.assertEqual(second["parsed"], 0)
        self.assertEqual(second["failed"], 0)

    def test_bad_map_isolated_and_later_maps_still_ingest(self):
        self.write("community/00-corrupt.map", b"not a Blood map")
        self.write("community/01-valid.map", _map_bytes(4))

        result = ingest(self.config)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["parsed"], 1)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["known_failed_content"], 1)
        with database(self.config.knowledge_db) as db:
            statuses = dict(db.execute("SELECT parse_status,COUNT(*) FROM contents GROUP BY parse_status"))
            self.assertEqual(statuses, {"failed": 1, "ok": 1})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ingestion_errors WHERE run_id=?",
                                        (result["run_id"],)).fetchone()[0], 1)
        second = ingest(self.config)
        self.assertEqual(second["failed"], 0)
        self.assertEqual(second["parsed"], 0)
        self.assertEqual(second["known_failed_content"], 1)

    def test_path_replacement_keeps_source_history_and_old_content_identity(self):
        path = self.write("community/revised.map", _map_bytes(5))
        first = ingest(self.config)
        with database(self.config.knowledge_db) as db:
            old_hash = db.execute("SELECT sha256 FROM sources WHERE relative_path='community/revised.map'").fetchone()[0]

        path.write_bytes(_map_bytes(6))
        second = ingest(self.config)
        self.assertEqual(first["parsed"], 1)
        self.assertEqual(second["parsed"], 1)
        with database(self.config.knowledge_db) as db:
            new_hash = db.execute("SELECT sha256 FROM sources WHERE relative_path='community/revised.map'").fetchone()[0]
            self.assertNotEqual(old_hash, new_hash)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM source_history").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contents").fetchone()[0], 2)

    def test_rescan_preserves_manual_author_and_refreshes_fts(self):
        self.write("community/credited.map", _map_bytes(15))
        ingest(self.config)
        with database(self.config.knowledge_db) as db:
            with db:
                db.execute("UPDATE sources SET author=? WHERE relative_path=?",
                           ("Manual Mapper", "community/credited.map"))

        result = ingest(self.config)
        self.assertEqual(result["parsed"], 0)
        with database(self.config.knowledge_db) as db:
            row = db.execute("SELECT author FROM sources WHERE relative_path=?",
                             ("community/credited.map",)).fetchone()
            self.assertEqual(row[0], "Manual Mapper")
            hits = corpus_search(db, "Manual", population="community")
            self.assertEqual([hit["author"] for hit in hits], ["Manual Mapper"])
            self.assertEqual(result["known_failed_content"], 0)

    def test_failed_extraction_is_atomic_and_retry_failed_retries_it(self):
        self.write("community/retry.map", _map_bytes(12))
        with patch("bloodforge.extraction.extract", side_effect=RuntimeError("temporary extraction issue")):
            first = ingest(self.config)
        self.assertEqual(first["failed"], 1)
        with database(self.config.knowledge_db) as db:
            self.assertEqual(db.execute("SELECT parse_status FROM contents").fetchone()[0], "extraction_failed")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM native_sectors").fetchone()[0], 0)

        skipped = ingest(self.config)
        self.assertEqual(skipped["skipped"], 1)
        retried = ingest(self.config, retry_failed=True)
        self.assertEqual(retried["parsed"], 1)
        with database(self.config.knowledge_db) as db:
            self.assertEqual(db.execute("SELECT parse_status FROM contents").fetchone()[0], "ok")

    def test_limit_is_partial_and_only_full_scan_deactivates_removed_paths(self):
        self.write("campaign/01.MAP", _map_bytes(1))
        removed = self.write("campaign/02.MAP", _map_bytes(2))
        ingest(self.config)
        removed.unlink()

        partial = ingest(self.config, limit=1)
        self.assertEqual(partial["status"], "partial")
        with database(self.config.knowledge_db) as db:
            active = db.execute("SELECT active FROM sources WHERE relative_path='campaign/02.MAP'").fetchone()[0]
            self.assertEqual(active, 1)

        complete = ingest(self.config)
        self.assertEqual(complete["status"], "complete")
        with database(self.config.knowledge_db) as db:
            active = db.execute("SELECT active FROM sources WHERE relative_path='campaign/02.MAP'").fetchone()[0]
            self.assertEqual(active, 0)
            self.assertEqual(corpus_search(db, "02"), [])


if __name__ == "__main__":
    unittest.main()
