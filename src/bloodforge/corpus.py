"""Discover, classify, and incrementally ingest Blood MAP files."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable

from .config import Config
from .native import encode_map, parse_map
from . import store


Progress = Callable[[dict[str, Any]], None]


class LosslessRoundTripError(ValueError):
    """The native representation cannot reproduce the original bytes."""


@dataclass(frozen=True)
class MapSource:
    """One physical map path and the path-derived metadata for it."""

    path: Path
    relative_path: str
    population: str
    mode: str
    tier: str | None
    is_view: bool
    byte_size: int
    mtime_ns: int
    sha256: str | None = None


def _root(value: Config | str | Path) -> Path:
    result = value.corpus_root if isinstance(value, Config) else Path(value)
    result = result.expanduser().resolve()
    if not result.is_dir():
        raise FileNotFoundError(f"corpus root is not a directory: {result}")
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _metadata(root: Path, path: Path, sha: str | None) -> MapSource:
    relative = path.relative_to(root).as_posix()
    parts = relative.split("/")
    first = parts[0].casefold()
    is_view = first == "tiered"
    tier: str | None = None

    if first == "campaign":
        if len(parts) > 1 and parts[1].casefold() == "multiplayer":
            population, mode = "blood-bloodbath", "multiplayer"
        else:
            population, mode = "blood-campaign", "sp"
    elif first == "community":
        population, mode = "community", "unknown"
    elif first == "conversions":
        population, mode = "own-conversion", "unknown"
    elif first == "curated":
        population = "community-curated"
        mode = "multiplayer" if len(parts) > 1 and parts[1].casefold() == "multiplayer" else "unknown"
    elif first == "mechanism":
        population, mode = "mechanism-tutorial", "unknown"
    elif first in {"generated", "reconstructed"}:
        population, mode = "generated", "unknown"
    elif is_view:
        population, mode = "community", "unknown"
        tier = parts[1] if len(parts) > 2 else None
    else:
        # Preserve unexpected MAPs as discoverable records instead of silently
        # assigning them to a training population.
        population, mode = "unclassified", "unknown"

    stat = path.stat()
    return MapSource(path, relative, population, mode, tier, is_view,
                     stat.st_size, stat.st_mtime_ns, sha)


def discover(config: Config | str | Path) -> list[MapSource]:
    """Return all recursive ``.map`` paths in stable path order.

    Tiered files are physical derived views. Ingestion hashes them and uses
    their content identity to assign tiers, independent of legacy absolute
    paths in the tier manifest.
    """
    root = _root(config)
    paths = sorted((path for path in root.rglob("*")
                    if path.is_file() and path.suffix.casefold() == ".map"),
                   key=lambda path: (path.relative_to(root).as_posix().casefold(),
                                     path.relative_to(root).as_posix()))

    return [_metadata(root, path, None) for path in paths]


def _call_extract(level: object) -> dict[str, Any]:
    # Deferred so metadata-only use remains available while extraction evolves.
    from .extraction import extract

    result = extract(level)
    if not isinstance(result, dict):
        raise TypeError("extract(level) must return a dictionary")
    if "observations" not in result:
        raise ValueError("extraction result is missing observations")
    return result


def _record_error(db: sqlite3.Connection, run_id: int, relative: str, error: str) -> None:
    db.execute("INSERT OR REPLACE INTO ingestion_errors(run_id,relative_path,error) VALUES(?,?,?)",
               (run_id, relative, error[:4000]))


def _upsert_source(db: sqlite3.Connection, root_id: int, source: MapSource,
                   sha: str, metadata: dict[str, Any]) -> None:
    metadata_json = store.canonical_json(metadata)
    author_is_supplied = "author" in metadata and metadata["author"] is not None
    requested_author = metadata.get("author") if author_is_supplied else None
    existing = db.execute(
        "SELECT id,sha256,population,tier,mode,author,is_view,mtime_ns,metadata_json "
        "FROM sources WHERE root_id=? AND relative_path=?",
        (root_id, source.relative_path),
    ).fetchone()
    values = (sha, source.population, source.tier, source.mode, requested_author,
              int(source.is_view), source.mtime_ns, metadata_json)
    author = requested_author

    if existing is None:
        cursor = db.execute(
            "INSERT INTO sources(root_id,relative_path,sha256,population,tier,mode,author,is_view,active,mtime_ns,metadata_json) "
            "VALUES(?,?,?,?,?,?,?, ?,1,?,?)",
            (root_id, source.relative_path, *values),
        )
        source_id = int(cursor.lastrowid)
    else:
        source_id = int(existing[0])
        previous_metadata = str(existing[8])
        author = requested_author if author_is_supplied else existing[5]
        values = (sha, source.population, source.tier, source.mode, author,
                  int(source.is_view), source.mtime_ns, metadata_json)
        changed = (existing[1] != sha or existing[2] != source.population or
                   existing[3] != source.tier or existing[4] != source.mode or
                   existing[5] != author or bool(existing[6]) != source.is_view or
                   previous_metadata != metadata_json)
        if changed:
            db.execute(
                "INSERT OR IGNORE INTO source_history(source_id,sha256,metadata_json) VALUES(?,?,?)",
                (source_id, existing[1], previous_metadata),
            )
        db.execute(
            "UPDATE sources SET sha256=?,population=?,tier=?,mode=?,author=?,is_view=?,active=1,mtime_ns=?,metadata_json=? WHERE id=?",
            (*values, source_id),
        )

    db.execute("DELETE FROM source_search WHERE rowid=?", (source_id,))
    db.execute("INSERT INTO source_search(rowid,relative_path,population,tier,author) VALUES(?,?,?,?,?)",
               (source_id, source.relative_path, source.population, source.tier or "", author or ""))


def _ensure_content(db: sqlite3.Connection, sha: str, size: int) -> sqlite3.Row:
    db.execute("INSERT OR IGNORE INTO contents(sha256,byte_size) VALUES(?,?)", (sha, size))
    row = db.execute("SELECT * FROM contents WHERE sha256=?", (sha,)).fetchone()
    if row is None:
        raise RuntimeError("content row disappeared")
    if int(row["byte_size"]) != size:
        raise RuntimeError(f"SHA-256 collision with inconsistent size for {sha}")
    return row


def _extract_content(db: sqlite3.Connection, sha: str, raw: bytes, level: object) -> None:
    if encode_map(level) != raw:
        raise LosslessRoundTripError(
            "native parse/encode round trip changed source bytes; refusing lossy normalization")

    extracted = _call_extract(level)
    observations = extracted["observations"]
    if not isinstance(observations, list):
        raise TypeError("extraction observations must be a list")

    # This function is called inside the map transaction. A failed write leaves
    # the previous complete native extraction intact.
    store.save_native(db, sha, level)
    db.execute("DELETE FROM observations WHERE sha256=? AND extractor=?",
               (sha, store.EXTRACTION_VERSION))
    db.executemany(
        "INSERT INTO observations(sha256,extractor,kind,object_id,value_json) VALUES(?,?,?,?,?)",
        ((sha, store.EXTRACTION_VERSION, str(item["kind"]), str(item["object_id"]),
          store.canonical_json(item["value"])) for item in observations),
    )
    counts = (len(getattr(level, "sectors")), len(getattr(level, "walls")), len(getattr(level, "sprites")))
    db.execute(
        "UPDATE contents SET parse_status='ok',error=NULL,extraction_version=?,native_version=?,"
        "sector_count=?,wall_count=?,sprite_count=?,geometry_signature=?,similarity_signature=?,"
        "start_x=?,start_y=?,start_z=?,start_angle=?,start_sector=? WHERE sha256=?",
        (store.EXTRACTION_VERSION, int(getattr(level, "version")), *counts,
         extracted.get("geometry_signature"), extracted.get("similarity_signature"),
         int(getattr(level, "start_x")), int(getattr(level, "start_y")), int(getattr(level, "start_z")),
         int(getattr(level, "start_angle")), int(getattr(level, "start_sector")), sha),
    )


def ingest(config: Config, progress: Progress | None = None, limit: int | None = None,
           retry_failed: bool = False) -> dict[str, Any]:
    """Incrementally ingest the physical map tree into the knowledge database.

    Content hashes are recalculated for all files on each run. Parsed native
    data is reused when its hash and extractor version are unchanged. ``limit``
    is useful for bounded/checkpointed runs; it never deactivates undiscovered
    source rows.
    """
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise ValueError("limit must be a non-negative integer or None")
    root = _root(config)
    discovered = discover(root)
    work = discovered if limit is None else discovered[:limit]
    # An explicit limit is an intentional bounded run. Even if it currently
    # covers every discovered path, do not let a demo/checkpoint invocation
    # deactivate rows based on the restricted discovery set.
    is_full_scan = limit is None
    tier_lookup: dict[str, set[str]] = {}

    config.data_root.mkdir(parents=True, exist_ok=True)
    db = store.connect(config.knowledge_db)
    counts = {"processed": 0, "parsed": 0, "skipped": 0, "failed": 0}
    run_id: int | None = None
    root_id: int | None = None
    try:
        with db:
            db.execute("INSERT OR IGNORE INTO roots(path) VALUES(?)", (str(root),))
            root_id = int(db.execute("SELECT id FROM roots WHERE path=?", (str(root),)).fetchone()[0])
            cursor = db.execute(
                "INSERT INTO ingestion_runs(root_id,extraction_version,status,discovered,processed,parsed,skipped,failed) "
                "VALUES(?,?, 'running', ?,0,0,0,0)",
                (root_id, store.EXTRACTION_VERSION, len(discovered)),
            )
            run_id = int(cursor.lastrowid)

        # Hash every tier view before source processing so community tiers can
        # be joined by actual bytes even though their paths sort earlier.
        for source in discovered:
            if source.is_view and source.tier:
                try:
                    view_sha = _sha256(source.path.read_bytes())
                    tier_lookup.setdefault(view_sha, set()).add(source.tier)
                except OSError:
                    # The normal per-map pass below records the isolated error.
                    continue

        for source in work:
            result_kind = "skipped"
            error: str | None = None
            sha: str | None = source.sha256
            try:
                # Tier views were already hashed during discovery. Hash every
                # source path now, using the exact bytes subsequently parsed.
                raw = source.path.read_bytes()
                sha = _sha256(raw)
                stat = source.path.stat()
                source = MapSource(source.path, source.relative_path, source.population,
                                   source.mode, source.tier, source.is_view, len(raw),
                                   stat.st_mtime_ns, sha)
                if source.population == "community" and not source.is_view:
                    classes = tier_lookup.get(sha, set())
                    source = MapSource(source.path, source.relative_path, source.population,
                                       source.mode, next(iter(classes)) if len(classes) == 1 else None,
                                       source.is_view, len(raw), stat.st_mtime_ns, sha)
                metadata = {
                    "relative_path": source.relative_path,
                    "provenance_directory": source.relative_path.split("/", 1)[0],
                    "population": source.population,
                    "mode": source.mode,
                    "tier": source.tier,
                    "is_view": source.is_view,
                    "sha256": sha,
                    "byte_size": len(raw),
                }

                with db:
                    assert root_id is not None and run_id is not None
                    row = _ensure_content(db, sha, len(raw))
                    _upsert_source(db, root_id, source, sha, metadata)
                    if source.is_view:
                        result_kind = "skipped"
                    elif (row["parse_status"] == "ok" and
                          row["extraction_version"] == store.EXTRACTION_VERSION and
                          row["native_version"] == 0x0700):
                        result_kind = "skipped"
                    elif row["parse_status"] in {"failed", "lossless_failed", "extraction_failed"} and not retry_failed:
                        result_kind = "skipped"
                    else:
                        try:
                            level = parse_map(raw)
                        except Exception as exc:
                            message = f"{type(exc).__name__}: {exc}"
                            db.execute("UPDATE contents SET parse_status='failed',error=? WHERE sha256=?",
                                       (message[:4000], sha))
                            _record_error(db, run_id, source.relative_path, message)
                            error = message
                            result_kind = "failed"
                        else:
                            db.execute("SAVEPOINT parse_content")
                            try:
                                _extract_content(db, sha, raw, level)
                            except Exception as exc:
                                db.execute("ROLLBACK TO parse_content")
                                db.execute("RELEASE parse_content")
                                message = f"{type(exc).__name__}: {exc}"
                                status = ("lossless_failed" if isinstance(exc, LosslessRoundTripError)
                                          else "extraction_failed")
                                db.execute("UPDATE contents SET parse_status=?,error=? WHERE sha256=?",
                                           (status, message[:4000], sha))
                                _record_error(db, run_id, source.relative_path, message)
                                error = message
                                result_kind = "failed"
                            else:
                                db.execute("RELEASE parse_content")
                                result_kind = "parsed"
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                error = message
                result_kind = "failed"
                with db:
                    assert run_id is not None
                    _record_error(db, run_id, source.relative_path, message)

            counts["processed"] += 1
            counts[result_kind] += 1
            with db:
                assert run_id is not None
                db.execute(
                    "UPDATE ingestion_runs SET processed=?,parsed=?,skipped=?,failed=?,last_path=? WHERE id=?",
                    (counts["processed"], counts["parsed"], counts["skipped"], counts["failed"],
                     source.relative_path, run_id),
                )
            if progress is not None:
                progress({"run_id": run_id, "discovered": len(discovered),
                          **counts, "last_path": source.relative_path, "error": error})

        if is_full_scan:
            active_paths = [source.relative_path for source in discovered]
            with db:
                assert root_id is not None
                if active_paths:
                    placeholders = ",".join("?" for _ in active_paths)
                    stale = db.execute(
                        f"SELECT id FROM sources WHERE root_id=? AND relative_path NOT IN ({placeholders}) AND active=1",
                        (root_id, *active_paths),
                    ).fetchall()
                else:
                    stale = db.execute("SELECT id FROM sources WHERE root_id=? AND active=1", (root_id,)).fetchall()
                for row in stale:
                    db.execute("UPDATE sources SET active=0 WHERE id=?", (row[0],))
                    db.execute("DELETE FROM source_search WHERE rowid=?", (row[0],))

        status = "complete" if is_full_scan else "partial"
        with db:
            assert run_id is not None
            db.execute("UPDATE ingestion_runs SET status=?,ended_at=CURRENT_TIMESTAMP WHERE id=?",
                       (status, run_id))
        return {"run_id": run_id, "status": status, "discovered": len(discovered),
                **counts, "known_failed_content": int(db.execute(
                    "SELECT COUNT(*) FROM contents WHERE parse_status IN ('failed','lossless_failed','extraction_failed')"
                ).fetchone()[0]), "root": str(root), "knowledge_db": str(config.knowledge_db)}
    except BaseException:
        if run_id is not None:
            with db:
                db.execute("UPDATE ingestion_runs SET status='interrupted',ended_at=CURRENT_TIMESTAMP WHERE id=?",
                           (run_id,))
        raise
    finally:
        db.close()


def corpus_summary(db: sqlite3.Connection) -> dict[str, Any]:
    """Return compact counts for the active source corpus and parser cache."""
    source = db.execute(
        "SELECT population,tier,is_view,COUNT(*) AS n FROM sources WHERE active=1 "
        "GROUP BY population,tier,is_view ORDER BY population,tier,is_view"
    ).fetchall()
    content = db.execute("SELECT parse_status,COUNT(*) AS n FROM contents GROUP BY parse_status ORDER BY parse_status").fetchall()
    duplicate_source_paths = db.execute(
        "SELECT COUNT(*) FROM (SELECT sha256 FROM sources WHERE active=1 AND is_view=0 "
        "GROUP BY sha256 HAVING COUNT(*)>1)"
    ).fetchone()[0]
    repeated_source_paths = db.execute(
        "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) AS n FROM sources "
        "WHERE active=1 AND is_view=0 GROUP BY sha256 HAVING COUNT(*)>1)"
    ).fetchone()[0]
    native_rows = {
        table.removeprefix("native_"): int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("native_sectors", "native_walls", "native_sprites")
    }
    known_failed_content = int(db.execute(
        "SELECT COUNT(*) FROM contents WHERE parse_status IN ('failed','lossless_failed','extraction_failed')"
    ).fetchone()[0])
    return {
        "active_sources": int(db.execute("SELECT COUNT(*) FROM sources WHERE active=1").fetchone()[0]),
        "active_source_groups": [dict(row) for row in source],
        "distinct_active_source_hashes": int(db.execute(
            "SELECT COUNT(DISTINCT sha256) FROM sources WHERE active=1 AND is_view=0"
        ).fetchone()[0]),
        "duplicate_source_hash_groups": int(duplicate_source_paths),
        "repeated_source_paths": int(repeated_source_paths),
        "contents": int(db.execute("SELECT COUNT(*) FROM contents").fetchone()[0]),
        "content_status": [dict(row) for row in content],
        "known_failed_content": known_failed_content,
        "native_record_rows": native_rows,
        "observations": int(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]),
        "latest_run": (dict(row) if (row := db.execute(
            "SELECT id,status,discovered,processed,parsed,skipped,failed,last_path,started_at,ended_at "
            "FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()) else None),
    }


def corpus_search(db: sqlite3.Connection, query: str, population: str | None = None,
                  limit: int = 20) -> list[dict[str, Any]]:
    """Search indexed source paths/populations/tiers with a bounded result set."""
    if not query.strip():
        return []
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    sql = (
        "SELECT s.relative_path,s.population,s.tier,s.author,s.sha256,s.is_view "
        "FROM source_search q JOIN sources s ON s.id=q.rowid "
        "WHERE source_search MATCH ? AND s.active=1"
    )
    params: list[Any] = [query]
    if population is not None:
        sql += " AND s.population=?"
        params.append(population)
    sql += " ORDER BY bm25(source_search),s.relative_path LIMIT ?"
    params.append(limit)
    return [dict(row) for row in db.execute(sql, params).fetchall()]
