"""Versioned SQLite stores and a content-addressed artifact directory."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from .native import NativeMap, Sector, Sprite, Wall

SCHEMA_VERSION = 1
EXTRACTION_VERSION = "native-geometry-1"


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


_KNOWLEDGE_SQL = """
CREATE TABLE roots(id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE);
CREATE TABLE contents(
 sha256 TEXT PRIMARY KEY, byte_size INTEGER NOT NULL,
 parse_status TEXT NOT NULL DEFAULT 'pending', error TEXT,
 extraction_version TEXT, native_version INTEGER,
 sector_count INTEGER, wall_count INTEGER, sprite_count INTEGER,
 geometry_signature TEXT, similarity_signature TEXT,
 start_x INTEGER, start_y INTEGER, start_z INTEGER, start_angle INTEGER, start_sector INTEGER
);
CREATE INDEX geometry_group ON contents(geometry_signature);
CREATE INDEX similarity_group ON contents(similarity_signature);
CREATE TABLE sources(
 id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES roots(id),
 relative_path TEXT NOT NULL, sha256 TEXT NOT NULL REFERENCES contents(sha256),
 population TEXT NOT NULL, tier TEXT, mode TEXT NOT NULL, author TEXT,
 is_view INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
 mtime_ns INTEGER NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 UNIQUE(root_id,relative_path)
);
CREATE INDEX source_hash ON sources(sha256);
CREATE INDEX source_population ON sources(population,tier,active);
CREATE TABLE source_history(
 source_id INTEGER NOT NULL REFERENCES sources(id), sha256 TEXT NOT NULL REFERENCES contents(sha256),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 PRIMARY KEY(source_id,sha256,metadata_json)
);
CREATE VIRTUAL TABLE source_search USING fts5(relative_path,population,tier,author);
CREATE TABLE ingestion_runs(
 id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES roots(id),
 extraction_version TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 ended_at TEXT, status TEXT NOT NULL, discovered INTEGER NOT NULL,
 processed INTEGER NOT NULL DEFAULT 0, parsed INTEGER NOT NULL DEFAULT 0,
 skipped INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
 last_path TEXT
);
CREATE TABLE ingestion_errors(
 run_id INTEGER NOT NULL REFERENCES ingestion_runs(id), relative_path TEXT NOT NULL,
 error TEXT NOT NULL, PRIMARY KEY(run_id,relative_path)
);
CREATE TABLE observations(
 sha256 TEXT NOT NULL REFERENCES contents(sha256), extractor TEXT NOT NULL,
 kind TEXT NOT NULL, object_id TEXT NOT NULL,
 value_json TEXT NOT NULL CHECK(json_valid(value_json)),
 PRIMARY KEY(sha256,extractor,kind,object_id)
);
CREATE INDEX observation_kind ON observations(kind,sha256);
CREATE TABLE patterns(
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL REFERENCES contents(sha256),
 family TEXT NOT NULL, signature TEXT NOT NULL, extractor TEXT NOT NULL,
 parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('hypothesis','candidate','reproduced','verified','rejected','superseded'))
);
CREATE INDEX pattern_family ON patterns(family,signature);
CREATE TABLE mining_runs(
 id INTEGER PRIMARY KEY, extractor TEXT NOT NULL, population TEXT NOT NULL,
 input_digest TEXT NOT NULL, summary_json TEXT NOT NULL CHECK(json_valid(summary_json)),
 UNIQUE(extractor,population,input_digest)
);
CREATE TABLE recipes(
 id TEXT PRIMARY KEY, family TEXT NOT NULL, version INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('hypothesis','candidate','reproduced','verified','rejected','superseded')),
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
 training_json TEXT NOT NULL CHECK(json_valid(training_json)),
 limitations_json TEXT NOT NULL CHECK(json_valid(limitations_json))
);
CREATE TABLE benchmarks(
 id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL REFERENCES recipes(id),
 heldout_pattern_id TEXT NOT NULL REFERENCES patterns(id),
 result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 artifact_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_WORKSPACE_SQL = """
CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE revisions(
 project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
 parent_revision INTEGER, scene_json TEXT NOT NULL CHECK(json_valid(scene_json)),
 operation_json TEXT NOT NULL CHECK(json_valid(operation_json)),
 PRIMARY KEY(project_id,revision)
);
CREATE TABLE outputs(
 sha256 TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), revision INTEGER,
 artifact_path TEXT NOT NULL, kind TEXT NOT NULL,
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json))
);
"""


def connect(path: Path, *, workspace: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=60000")
    application_id = 0x42465753 if workspace else 0x42464B4E
    current_id = db.execute("PRAGMA application_id").fetchone()[0]
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION or (current_id and current_id != application_id):
        db.close()
        raise ValueError(f"unsupported or wrong database at {path} (version {version})")
    if version == 0:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
            db.close()
            raise ValueError(f"refusing to initialize an unrecognized non-empty database: {path}")
        sql = _WORKSPACE_SQL if workspace else _KNOWLEDGE_SQL
        if not workspace:
            # Each native scalar is typed, each extension is a BLOB; no duplicate
            # whole-map JSON is stored. Dataclass fields are the v1 wire schema.
            for name, cls in (("sectors", Sector), ("walls", Wall), ("sprites", Sprite)):
                columns = ",".join(f'"{f.name}" {"BLOB" if f.name == "extra_data" else "INTEGER"} NOT NULL'
                                   for f in fields(cls))
                sql += f"CREATE TABLE native_{name}(sha256 TEXT NOT NULL REFERENCES contents(sha256),object_id INTEGER NOT NULL,{columns},PRIMARY KEY(sha256,object_id)) WITHOUT ROWID;"
        try:
            db.executescript("BEGIN IMMEDIATE;" + sql +
                             f"PRAGMA application_id={application_id};PRAGMA user_version={SCHEMA_VERSION};COMMIT;")
        except BaseException:
            db.rollback()
            db.close()
            raise
    return db


@contextmanager
def database(path: Path, *, workspace: bool = False) -> Iterator[sqlite3.Connection]:
    db = connect(path, workspace=workspace)
    try:
        yield db
    finally:
        db.close()


def save_native(db: sqlite3.Connection, sha256: str, level: object) -> None:
    for name, cls in (("sectors", Sector), ("walls", Wall), ("sprites", Sprite)):
        db.execute(f"DELETE FROM native_{name} WHERE sha256=?", (sha256,))
        names = [f.name for f in fields(cls)]
        placeholders = ",".join("?" for _ in range(len(names) + 2))
        db.executemany(f"INSERT INTO native_{name} VALUES({placeholders})",
                       ((sha256, index, *(getattr(record, key) for key in names))
                        for index, record in enumerate(getattr(level, name))))


def load_native(db: sqlite3.Connection, sha256: str) -> NativeMap:
    """Rehydrate the indexed facts for analysis (not archival byte rewriting)."""
    row = db.execute("SELECT * FROM contents WHERE sha256=? AND parse_status='ok'", (sha256,)).fetchone()
    if row is None:
        raise ValueError(f"no successfully extracted native facts for {sha256}")
    records = {}
    for name, cls in (("sectors", Sector), ("walls", Wall), ("sprites", Sprite)):
        names = [f.name for f in fields(cls)]
        records[name] = [cls(**{key: record[key] for key in names}) for record in
                         db.execute(f"SELECT * FROM native_{name} WHERE sha256=? ORDER BY object_id", (sha256,))]
    return NativeMap(**records, **{key: row[key] for key in
                     ("start_x", "start_y", "start_z", "start_angle", "start_sector")})


def artifact_put(data_root: Path, payload: bytes, suffix: str) -> tuple[str, Path]:
    if not suffix.startswith(".") or not suffix[1:].isalnum():
        raise ValueError("artifact suffix must be a simple extension")
    sha = hashlib.sha256(payload).hexdigest()
    destination = data_root / "artifacts" / sha[:2] / (sha + suffix.lower())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
            raise ValueError(f"corrupt content-addressed artifact: {destination}")
    else:
        # Exclusive creation avoids overwriting another writer's artifact.
        try:
            with destination.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                raise ValueError(f"artifact collision: {destination}")
    return sha, destination
