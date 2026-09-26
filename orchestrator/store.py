"""Durable job intake and query storage for the orchestrator."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic, sleep
from typing import Iterator
from uuid import uuid4

from .errors import TaskError


SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class Store:
    """A SQLite-backed store with one connection per operation."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TaskError("STORAGE_ERROR", "Cannot prepare the job database directory.") from exc
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            self._enable_wal(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        # Changing journal mode needs a lock that SQLite's busy timeout does not
        # reliably wait for when several processes create the database together.
        deadline = monotonic() + 5
        while True:
            try:
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                if mode.lower() == "wal":
                    return
                mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if mode.lower() != "wal":
                    raise sqlite3.OperationalError("Could not enable WAL mode")
                return
            except sqlite3.OperationalError as exc:
                if not any(word in str(exc).lower() for word in ("locked", "busy")) or monotonic() >= deadline:
                    raise
                sleep(0.02)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield an immediate write transaction and always close its connection."""
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise TaskError("STORAGE_ERROR", "Job database operation failed.") from exc
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise TaskError("STORAGE_ERROR", f"Job database schema version {version} is newer than supported version {SCHEMA_VERSION}.")
            if version == SCHEMA_VERSION:
                return

            connection.execute(
                """CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    account TEXT NOT NULL,
                    delivery_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    attempt_id TEXT,
                    status TEXT NOT NULL,
                    submission TEXT NOT NULL,
                    platform_result TEXT,
                    content_id TEXT,
                    verified_at TEXT,
                    actual_metadata_json TEXT,
                    warnings_json TEXT NOT NULL,
                    error_json TEXT,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (platform, account, delivery_key),
                    FOREIGN KEY (job_id, attempt_id) REFERENCES attempts (job_id, attempt_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs (job_id),
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    owner_token TEXT,
                    UNIQUE (job_id, attempt_id)
                )"""
            )
            connection.execute("CREATE INDEX attempts_by_job ON attempts (job_id, started_at)")
            connection.execute(
                """CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs (job_id),
                    attempt_id TEXT,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    FOREIGN KEY (job_id, attempt_id) REFERENCES attempts (job_id, attempt_id)
                )"""
            )
            connection.execute("CREATE INDEX events_by_job ON events (job_id, id)")
            connection.execute(
                """CREATE TABLE accounts (
                    platform TEXT NOT NULL,
                    account TEXT NOT NULL,
                    expected_id TEXT,
                    session_path TEXT,
                    status TEXT NOT NULL,
                    pause_reason TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (platform, account)
                )"""
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _decode(value: str | None) -> object | None:
        if value is None:
            return None
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise TaskError("STORAGE_ERROR", "Job database contains invalid JSON.") from exc

    @classmethod
    def _job_from_row(cls, row: sqlite3.Row) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "job_id": row["job_id"],
            "attempt_id": row["attempt_id"],
            "platform": row["platform"],
            "account": row["account"],
            "delivery_key": row["delivery_key"],
            "request_digest": row["request_digest"],
            "status": row["status"],
            "submission": row["submission"],
            "platform_result": row["platform_result"],
            "content_id": row["content_id"],
            "verified_at": row["verified_at"],
            "actual_metadata": cls._decode(row["actual_metadata_json"]),
            "warnings": cls._decode(row["warnings_json"]),
            "error": cls._decode(row["error_json"]),
            "evidence": cls._decode(row["evidence_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "manifest": cls._decode(row["manifest_json"]),
        }

    @staticmethod
    def _job_row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise TaskError("NOT_FOUND", f"Job {job_id} was not found.")
        return row

    def enqueue(self, manifest: dict) -> dict:
        if not isinstance(manifest, dict):
            raise TaskError("INVALID_MANIFEST", "Manifest must be a JSON object.")
        for field in ("platform", "account", "delivery_key"):
            if not isinstance(manifest.get(field), str) or not manifest[field]:
                raise TaskError("INVALID_MANIFEST", f"Manifest requires a nonempty {field}.", field=field)
        try:
            manifest_json = _canonical_json(manifest)
        except (TypeError, ValueError) as exc:
            raise TaskError("INVALID_MANIFEST", "Manifest must contain only finite JSON values.") from exc
        digest = sha256(manifest_json.encode("utf-8")).hexdigest()
        platform = manifest["platform"]
        account = manifest["account"]
        delivery_key = manifest["delivery_key"]

        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE platform = ? AND account = ? AND delivery_key = ?",
                (platform, account, delivery_key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] == digest and existing["manifest_json"] == manifest_json:
                    return self._job_from_row(existing)
                raise TaskError("CONFLICT", "Delivery key is already used for a different manifest.", field="delivery_key")

            job_id = uuid4().hex
            now = _utc_now()
            connection.execute(
                """INSERT INTO jobs (
                    job_id, platform, account, delivery_key, request_digest, manifest_json,
                    attempt_id, status, submission, platform_result, content_id, verified_at,
                    actual_metadata_json, warnings_json, error_json, evidence_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'QUEUED', 'NOT_SUBMITTED', NULL, NULL, NULL,
                          NULL, '[]', NULL, '[]', ?, ?)""",
                (job_id, platform, account, delivery_key, digest, manifest_json, now, now),
            )
            connection.execute(
                "INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) "
                "VALUES (?, NULL, 'QUEUED', 'QUEUED', ?, '{}')",
                (job_id, now),
            )
            return self._job_from_row(self._job_row(connection, job_id))

    def get(self, job_id: str) -> dict:
        with self.transaction() as connection:
            return self._job_from_row(self._job_row(connection, job_id))

    def list_jobs(self) -> list[dict]:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC, job_id DESC").fetchall()
            return [self._job_from_row(row) for row in rows]

    def events(self, job_id: str) -> list[dict]:
        with self.transaction() as connection:
            self._job_row(connection, job_id)
            rows = connection.execute("SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
            return [
                {
                    "id": row["id"],
                    "job_id": row["job_id"],
                    "attempt_id": row["attempt_id"],
                    "status": row["status"],
                    "stage": row["stage"],
                    "created_at": row["created_at"],
                    "details": self._decode(row["details_json"]),
                }
                for row in rows
            ]

    def attempts(self, job_id: str) -> list[dict]:
        with self.transaction() as connection:
            self._job_row(connection, job_id)
            rows = connection.execute(
                "SELECT * FROM attempts WHERE job_id = ? ORDER BY started_at, attempt_id", (job_id,)
            ).fetchall()
            return [dict(row) for row in rows]
