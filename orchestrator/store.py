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


SCHEMA_VERSION = 2
RESULT_SCHEMA_VERSION = 1


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
            if version == 1:
                connection.execute("ALTER TABLE attempts ADD COLUMN submission_started_at TEXT")
                connection.execute("ALTER TABLE attempts ADD COLUMN receipt_id TEXT")
                connection.execute("ALTER TABLE attempts ADD COLUMN submit_count INTEGER NOT NULL DEFAULT 0 CHECK (submit_count IN (0, 1))")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
                    submission_started_at TEXT,
                    receipt_id TEXT,
                    submit_count INTEGER NOT NULL DEFAULT 0 CHECK (submit_count IN (0, 1)),
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
            "schema_version": RESULT_SCHEMA_VERSION,
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

    def runnable_jobs(self, max_attempts: int) -> list[dict]:
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT j.* FROM jobs j LEFT JOIN accounts a
                   ON a.platform = j.platform AND a.account = j.account
                   WHERE j.status IN ('QUEUED', 'FAILED_PRE_SUBMIT', 'PREPARING', 'READY', 'SUBMITTING')
                     AND (a.status IS NULL OR a.status = 'READY')
                   ORDER BY j.created_at, j.job_id"""
            ).fetchall()
            return [self._job_from_row(row) for row in rows
                    if row["status"] == "SUBMITTING" or self._can_start(connection, row, max_attempts)]

    @staticmethod
    def _unfinished_preparation(connection: sqlite3.Connection, job: sqlite3.Row) -> bool:
        if job["status"] not in {"PREPARING", "READY"} or job["submission"] != "NOT_SUBMITTED":
            return False
        attempt = connection.execute("SELECT finished_at, submit_count FROM attempts WHERE attempt_id = ?",
                                     (job["attempt_id"],)).fetchone()
        return bool(attempt and attempt["finished_at"] is None and attempt["submit_count"] == 0)

    def _can_start(self, connection: sqlite3.Connection, job: sqlite3.Row, max_attempts: int) -> bool:
        if job["submission"] != "NOT_SUBMITTED":
            return False
        if job["status"] == "QUEUED":
            return True
        if self._unfinished_preparation(connection, job):
            return True
        error = self._decode(job["error_json"])
        count = connection.execute("SELECT COUNT(*) FROM attempts WHERE job_id = ?", (job["job_id"],)).fetchone()[0]
        return (job["status"] == "FAILED_PRE_SUBMIT" and isinstance(error, dict)
                and error.get("code") == "TRANSIENT_PRE_SUBMIT" and count < max_attempts)

    def can_start(self, job_id: str, max_attempts: int) -> bool:
        with self.transaction() as connection:
            return self._can_start(connection, self._job_row(connection, job_id), max_attempts)

    def claim(self, job_id: str, owner_token: str, max_attempts: int = 2) -> dict | None:
        with self.transaction() as connection:
            job = self._job_row(connection, job_id)
            account = connection.execute(
                "SELECT status FROM accounts WHERE platform = ? AND account = ?",
                (job["platform"], job["account"]),
            ).fetchone()
            if account and account["status"] != "READY":
                return None
            # AccountLease has already proved that the previous owner cannot
            # still write. A durable boundary is the remaining retry fence.
            if self._unfinished_preparation(connection, job):
                now = _utc_now()
                interrupted = TaskError("TRANSIENT_PRE_SUBMIT", "The previous executor stopped before submission").as_dict()
                connection.execute("UPDATE attempts SET status = 'FAILED_PRE_SUBMIT', finished_at = ? WHERE attempt_id = ?",
                                   (now, job["attempt_id"]))
                connection.execute("UPDATE jobs SET status = 'FAILED_PRE_SUBMIT', error_json = ?, updated_at = ? WHERE job_id = ?",
                                   (_canonical_json(interrupted), now, job_id))
                connection.execute("INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) VALUES (?, ?, 'FAILED_PRE_SUBMIT', 'recover', ?, ?)",
                                   (job_id, job["attempt_id"], now, _canonical_json({"error": interrupted})))
                job = self._job_row(connection, job_id)
            if not self._can_start(connection, job, max_attempts):
                return None
            attempt_id, now = uuid4().hex, _utc_now()
            connection.execute(
                "INSERT INTO attempts (attempt_id, job_id, status, started_at, owner_token) VALUES (?, ?, 'PREPARING', ?, ?)",
                (attempt_id, job_id, now, owner_token),
            )
            connection.execute(
                """UPDATE jobs SET status = 'PREPARING', attempt_id = ?, updated_at = ?,
                   error_json = NULL, warnings_json = '[]', actual_metadata_json = NULL WHERE job_id = ?""",
                (attempt_id, now, job_id),
            )
            connection.execute(
                "INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) VALUES (?, ?, 'PREPARING', 'prepare', ?, '{}')",
                (job_id, attempt_id, now),
            )
            return self._job_from_row(self._job_row(connection, job_id))

    @staticmethod
    def _require_owner(connection: sqlite3.Connection, job_id: str, owner_token: str) -> None:
        current = connection.execute(
            """SELECT a.owner_token, a.finished_at FROM jobs j JOIN attempts a
               ON a.attempt_id = j.attempt_id AND a.job_id = j.job_id WHERE j.job_id = ?""",
            (job_id,),
        ).fetchone()
        if current is None or current["owner_token"] != owner_token or current["finished_at"] is not None:
            raise TaskError("OWNERSHIP_LOST", "This executor no longer owns the active attempt")

    def assert_owner(self, job_id: str, owner_token: str) -> None:
        with self.transaction() as connection:
            self._require_owner(connection, job_id, owner_token)

    def finish_preparation(self, job_id: str, owner_token: str, *, status: str, metadata=None, error=None, warnings=None, pause_reason=None) -> dict:
        if status not in {"READY", "FAILED_PRE_SUBMIT", "BLOCKED"}:
            raise TaskError("INVALID_STATE", "Invalid preparation result")
        with self.transaction() as connection:
            self._require_owner(connection, job_id, owner_token)
            job = self._job_row(connection, job_id)
            if job["status"] not in {"PREPARING", "READY"} or job["submission"] != "NOT_SUBMITTED":
                raise TaskError("INVALID_STATE", "Task is not preparing")
            now = _utc_now()
            connection.execute(
                """UPDATE jobs SET status = ?, actual_metadata_json = ?, error_json = ?, warnings_json = ?, updated_at = ?
                   WHERE job_id = ?""",
                (status, _canonical_json(metadata) if metadata is not None else None,
                 _canonical_json(error) if error is not None else None, _canonical_json(warnings or []), now, job_id),
            )
            connection.execute(
                "UPDATE attempts SET status = ?, finished_at = ? WHERE attempt_id = ?",
                (status, now, job["attempt_id"]),
            )
            connection.execute(
                "INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) VALUES (?, ?, ?, 'prepare', ?, ?)",
                (job_id, job["attempt_id"], status, now, _canonical_json({"error": error})),
            )
            if pause_reason is not None:
                self._pause_account(connection, job["platform"], job["account"], pause_reason)
            return self._job_from_row(self._job_row(connection, job_id))

    def pause_account(self, platform: str, account: str, reason: dict) -> None:
        with self.transaction() as connection:
            self._pause_account(connection, platform, account, reason)

    @staticmethod
    def _pause_account(connection: sqlite3.Connection, platform: str, account: str, reason: dict) -> None:
        connection.execute(
            """INSERT INTO accounts (platform, account, status, pause_reason, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(platform, account) DO UPDATE SET status = excluded.status,
               pause_reason = excluded.pause_reason, updated_at = excluded.updated_at""",
            (platform, account, reason["code"], _canonical_json(reason), _utc_now()),
        )

    def accounts(self) -> list[dict]:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM accounts ORDER BY platform, account").fetchall()
            return [{**dict(row), "pause_reason": self._decode(row["pause_reason"])} for row in rows]

    def mark_ready(self, job_id: str, owner_token: str, metadata: dict) -> dict:
        with self.transaction() as connection:
            self._require_owner(connection, job_id, owner_token)
            job = self._job_row(connection, job_id)
            if job["status"] != "PREPARING" or job["submission"] != "NOT_SUBMITTED":
                raise TaskError("INVALID_STATE", "Task is not preparing for submission")
            now = _utc_now()
            connection.execute("UPDATE jobs SET status = 'READY', actual_metadata_json = ?, updated_at = ? WHERE job_id = ?",
                               (_canonical_json(metadata), now, job_id))
            connection.execute("UPDATE attempts SET status = 'READY' WHERE attempt_id = ?", (job["attempt_id"],))
            connection.execute("INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) VALUES (?, ?, 'READY', 'prepare', ?, '{}')",
                               (job_id, job["attempt_id"], now))
            return self._job_from_row(self._job_row(connection, job_id))

    def begin_submission(self, job_id: str, owner_token: str) -> dict:
        """Durably cross the boundary BEFORE any final platform action."""
        with self.transaction() as connection:
            self._require_owner(connection, job_id, owner_token)
            job = self._job_row(connection, job_id)
            if job["status"] != "READY" or job["submission"] != "NOT_SUBMITTED":
                raise TaskError("INVALID_STATE", "The final submission boundary cannot be crossed again")
            now = _utc_now()
            changed = connection.execute(
                """UPDATE attempts SET status = 'SUBMITTING', submit_count = 1, submission_started_at = ?
                   WHERE attempt_id = ? AND submit_count = 0""", (now, job["attempt_id"]),
            ).rowcount
            if changed != 1:
                raise TaskError("INVALID_STATE", "This attempt already crossed the submission boundary")
            connection.execute("UPDATE jobs SET status = 'SUBMITTING', submission = 'MAY_HAVE_SUBMITTED', updated_at = ? WHERE job_id = ?", (now, job_id))
            connection.execute("INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) VALUES (?, ?, 'SUBMITTING', 'submit', ?, '{}')",
                               (job_id, job["attempt_id"], now))
            return self._job_from_row(self._job_row(connection, job_id))

    def record_receipt(self, job_id: str, owner_token: str, content_id: str) -> None:
        with self.transaction() as connection:
            self._require_owner(connection, job_id, owner_token)
            job = self._job_row(connection, job_id)
            if job["status"] != "SUBMITTING":
                raise TaskError("INVALID_STATE", "No active submission accepts this receipt")
            connection.execute("UPDATE attempts SET receipt_id = ? WHERE attempt_id = ?", (content_id, job["attempt_id"]))

    def current_attempt(self, job_id: str) -> dict:
        with self.transaction() as connection:
            job = self._job_row(connection, job_id)
            row = connection.execute("SELECT * FROM attempts WHERE attempt_id = ?", (job["attempt_id"],)).fetchone()
            if row is None:
                raise TaskError("INVALID_STATE", "Task has no active attempt")
            return dict(row)

    def claim_reconciliation(self, job_id: str, owner_token: str) -> dict | None:
        """Resume the same attempt for reads only, after acquiring AccountLease."""
        with self.transaction() as connection:
            job = self._job_row(connection, job_id)
            account = connection.execute("SELECT status FROM accounts WHERE platform = ? AND account = ?",
                                         (job["platform"], job["account"])).fetchone()
            if (job["status"] not in {"SUBMITTING", "UNKNOWN_COMMIT"}
                    or job["submission"] != "MAY_HAVE_SUBMITTED"
                    or (account and account["status"] != "READY")):
                return None
            now = _utc_now()
            connection.execute("UPDATE attempts SET owner_token = ?, status = 'UNKNOWN_COMMIT', finished_at = NULL WHERE attempt_id = ?",
                               (owner_token, job["attempt_id"]))
            connection.execute("UPDATE jobs SET status = 'UNKNOWN_COMMIT', updated_at = ? WHERE job_id = ?", (now, job_id))
            connection.execute("INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) VALUES (?, ?, 'UNKNOWN_COMMIT', 'recover', ?, '{}')",
                               (job_id, job["attempt_id"], now))
            return self._job_from_row(self._job_row(connection, job_id))

    def finish_delivery(self, job_id: str, owner_token: str, *, work: dict | None, error=None, warnings=None, pause_reason=None) -> dict:
        with self.transaction() as connection:
            self._require_owner(connection, job_id, owner_token)
            job = self._job_row(connection, job_id)
            if job["submission"] != "MAY_HAVE_SUBMITTED":
                raise TaskError("INVALID_STATE", "Task has not crossed the submission boundary")
            now = _utc_now()
            status = "COMPLETED" if work is not None else "UNKNOWN_COMMIT"
            evidence = [] if work is None else [{
                "kind": work["source"], "content_id": work["content_id"],
                "account_id": work["account_id"], "created_at": work["created_at"], "observed_at": now,
            }]
            connection.execute(
                """UPDATE jobs SET status = ?, submission = ?, platform_result = ?, content_id = ?,
                   verified_at = ?, error_json = ?, warnings_json = ?, evidence_json = ?, updated_at = ? WHERE job_id = ?""",
                (status, "CONFIRMED" if work else "MAY_HAVE_SUBMITTED", work["platform_result"] if work else None,
                 work["content_id"] if work else None, now if work else None,
                 _canonical_json(error) if error and work is None else None,
                 _canonical_json(warnings or []), _canonical_json(evidence), now, job_id),
            )
            connection.execute("UPDATE attempts SET status = ?, finished_at = ? WHERE attempt_id = ?", (status, now, job["attempt_id"]))
            connection.execute("INSERT INTO events (job_id, attempt_id, status, stage, created_at, details_json) VALUES (?, ?, ?, 'reconcile', ?, ?)",
                               (job_id, job["attempt_id"], status, now, _canonical_json({"platform_result": work["platform_result"] if work else None})))
            if pause_reason is not None:
                self._pause_account(connection, job["platform"], job["account"], pause_reason)
            return self._job_from_row(self._job_row(connection, job_id))
