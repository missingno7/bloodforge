from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from bloodforge.store import artifact_put, database


class StoreTests(unittest.TestCase):
    def test_schema_identity_foreign_keys_and_store_separation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "knowledge.db"
            with database(path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO source_history VALUES(1,'missing','{}')")
            with self.assertRaisesRegex(ValueError, "wrong database"):
                with database(path, workspace=True):
                    pass

    def test_unknown_nonempty_database_is_not_mutated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "other.db"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE important(value TEXT)")
                db.execute("INSERT INTO important VALUES('keep')")
            with self.assertRaisesRegex(ValueError, "unrecognized"):
                with database(path):
                    pass
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT * FROM important").fetchall(), [('keep',)])

    def test_artifact_identity_and_corruption_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = artifact_put(root, b"generated synthetic content", ".map")
            second = artifact_put(root, b"generated synthetic content", ".map")
            self.assertEqual(first, second)
            first[1].write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "corrupt"):
                artifact_put(root, b"generated synthetic content", ".map")
