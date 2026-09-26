"""Durable intent and per-target receipts for one multi-platform delivery."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from orchestrator.errors import TaskError
from orchestrator.manifest import MANIFEST_FIELDS
from orchestrator.store import _canonical_json, _utc_now

if TYPE_CHECKING:
    from orchestrator.service import TaskService


_BATCH_FIELDS = (MANIFEST_FIELDS - {"platform", "account"}) | {"targets"}
_OVERRIDE_FIELDS = MANIFEST_FIELDS - {"schema_version", "delivery_key", "platform", "account"}
_FACT_FIELDS = {"ownership", "ai_usage", "repost_source"}
_DECLARATION_FIELDS = _FACT_FIELDS | {"originality_rights", "repost_authorized"}
_POLICY_FIELDS = {"required", "optional", "allow_omit"}


def _input_error(message: str, field: str) -> TaskError:
    return TaskError("INVALID_INPUT", message, field=field)


def _check_content_shapes(content: dict[str, Any], prefix: str) -> None:
    """Reject nested unknown fields and values that could carry credentials."""
    for name in ("facts", "declarations", "field_policy"):
        if name not in content:
            continue
        value = content[name]
        allowed = {"facts": _FACT_FIELDS, "declarations": _DECLARATION_FIELDS,
                   "field_policy": _POLICY_FIELDS}[name]
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise _input_error("Content object has an invalid shape", f"{prefix}{name}")
        extra = sorted(value.keys() - allowed)
        if extra:
            raise _input_error("Content field is not supported", f"{prefix}{name}.{extra[0]}")
        for key, item in value.items():
            if name == "field_policy":
                valid = isinstance(item, list) and all(isinstance(part, str) for part in item)
            else:
                valid = item is None or type(item) in (str, bool)
            if not valid:
                raise _input_error("Content field has an invalid shape", f"{prefix}{name}.{key}")
    for name in ("tags", "media"):
        if name in content and (not isinstance(content[name], list) or
                                any(not isinstance(item, str) for item in content[name])):
            raise _input_error("Content list has an invalid shape", f"{prefix}{name}")
    for name in ("content_type", "title", "body", "collection"):
        if name in content and content[name] is not None and not isinstance(content[name], str):
            raise _input_error("Content field has an invalid shape", f"{prefix}{name}")
    if "cover" in content and content["cover"] is not None and not isinstance(content["cover"], str):
        raise _input_error("Content field has an invalid shape", f"{prefix}cover")
    if "run_at" in content:
        schedule = content["run_at"]
        if not isinstance(schedule, dict) or any(not isinstance(key, str) for key in schedule):
            raise _input_error("Local start time must be an object", f"{prefix}run_at")
        extra = sorted(schedule.keys() - {"local", "timezone", "fold"})
        if extra:
            raise _input_error("Schedule field is not supported", f"{prefix}run_at.{extra[0]}")
        for name in ("local", "timezone"):
            if name in schedule and not isinstance(schedule[name], str):
                raise _input_error("Schedule field has an invalid shape", f"{prefix}run_at.{name}")
        if "fold" in schedule and type(schedule["fold"]) is not int:
            raise _input_error("Schedule field has an invalid shape", f"{prefix}run_at.fold")


def _validate_batch(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise _input_error("Batch must be a JSON object with string keys", "manifest")
    extra = sorted(payload.keys() - _BATCH_FIELDS)
    if extra:
        raise _input_error("Batch field is not supported", extra[0])
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise TaskError("UNSUPPORTED", "Only batch schema_version 1 is supported", field="schema_version")
    key = payload.get("delivery_key")
    if not isinstance(key, str) or not key.strip():
        raise _input_error("A non-empty delivery key is required", "delivery_key")
    if not isinstance(payload.get("content_type"), str) or not payload["content_type"].strip():
        raise _input_error("A non-empty content type is required", "content_type")
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise _input_error("At least one target is required", "targets")
    _check_content_shapes(payload, "")
    seen: set[tuple[str, str]] = set()
    for index, target in enumerate(targets):
        prefix = f"targets[{index}]."
        if not isinstance(target, dict) or any(not isinstance(name, str) for name in target):
            raise _input_error("Target must be a JSON object", f"targets[{index}]")
        extra = sorted(target.keys() - {"platform", "account", "overrides"})
        if extra:
            raise _input_error("Target field is not supported", prefix + extra[0])
        for field in ("platform", "account"):
            if not isinstance(target.get(field), str) or not target[field].strip():
                raise _input_error("Target requires a non-empty string", prefix + field)
        identity = (target["platform"], target["account"])
        if identity in seen:
            raise _input_error("Target platform and account are duplicated", f"targets[{index}]")
        seen.add(identity)
        overrides = target.get("overrides", {})
        if not isinstance(overrides, dict) or any(not isinstance(name, str) for name in overrides):
            raise _input_error("Overrides must be a JSON object", prefix + "overrides")
        extra = sorted(overrides.keys() - _OVERRIDE_FIELDS)
        if extra:
            raise _input_error("Override field is not supported", prefix + "overrides." + extra[0])
        _check_content_shapes(overrides, prefix + "overrides.")
    try:
        canonical = _canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise _input_error("Batch contains a non-JSON value", "manifest") from exc
    return canonical, sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_tables(connection: Any) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS fanout_batches (
            batch_id TEXT PRIMARY KEY,
            delivery_key TEXT NOT NULL UNIQUE,
            request_digest TEXT NOT NULL,
            request_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS fanout_targets (
            batch_id TEXT NOT NULL REFERENCES fanout_batches (batch_id),
            target_index INTEGER NOT NULL,
            platform TEXT NOT NULL,
            account TEXT NOT NULL,
            expected_digest TEXT,
            normalized_json TEXT,
            job_id TEXT REFERENCES jobs (job_id),
            error_json TEXT,
            PRIMARY KEY (batch_id, target_index),
            UNIQUE (batch_id, platform, account)
        )"""
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(fanout_targets)")}
    if "normalized_json" not in columns:
        connection.execute("ALTER TABLE fanout_targets ADD COLUMN normalized_json TEXT")


def _existing_batch(service: TaskService, key: str, canonical: str, digest: str) -> str | None:
    with service.store.transaction() as connection:
        _ensure_tables(connection)
        existing = connection.execute(
            "SELECT batch_id, request_digest, request_json FROM fanout_batches WHERE delivery_key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] != digest or existing["request_json"] != canonical:
                raise TaskError("CONFLICT", "Batch delivery key is already used for different content", field="delivery_key")
            return existing["batch_id"]
        return None


def _register(service: TaskService, payload: dict[str, Any], canonical: str, digest: str,
              prepared: list[tuple[str | None, str | None, str | None]]) -> str:
    with service.store.transaction() as connection:
        _ensure_tables(connection)
        existing = connection.execute(
            "SELECT batch_id, request_digest, request_json FROM fanout_batches WHERE delivery_key = ?",
            (payload["delivery_key"],),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] != digest or existing["request_json"] != canonical:
                raise TaskError("CONFLICT", "Batch delivery key is already used for different content", field="delivery_key")
            return existing["batch_id"]
        batch_id = uuid4().hex
        connection.execute(
            "INSERT INTO fanout_batches (batch_id, delivery_key, request_digest, request_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch_id, payload["delivery_key"], digest, canonical, _utc_now()),
        )
        for index, target in enumerate(payload["targets"]):
            normalized_json, expected_digest, error_json = prepared[index]
            connection.execute(
                "INSERT INTO fanout_targets (batch_id, target_index, platform, account, "
                "expected_digest, normalized_json, error_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (batch_id, index, target["platform"], target["account"],
                 expected_digest, normalized_json, error_json),
            )
        return batch_id


def _child_manifest(payload: dict[str, Any], index: int) -> dict[str, Any]:
    target = payload["targets"][index]
    child = {key: value for key, value in payload.items() if key != "targets"}
    child.update(target.get("overrides", {}))
    child["platform"] = target["platform"]
    child["account"] = target["account"]
    return child


def _target_row(service: TaskService, batch_id: str, index: int) -> Any:
    with service.store.transaction() as connection:
        return connection.execute(
            "SELECT * FROM fanout_targets WHERE batch_id = ? AND target_index = ?", (batch_id, index)
        ).fetchone()


def _record_error(service: TaskService, batch_id: str, index: int, error: TaskError) -> None:
    if error.code == "STORAGE_ERROR":
        raise error
    with service.store.transaction() as connection:
        connection.execute(
            "UPDATE fanout_targets SET error_json = ? WHERE batch_id = ? AND target_index = ? "
            "AND job_id IS NULL AND error_json IS NULL",
            (_canonical_json(error.as_dict()), batch_id, index),
        )


def _find_and_link_existing(service: TaskService, batch_id: str, index: int,
                            child: dict[str, Any], expected: str) -> bool:
    with service.store.transaction() as connection:
        row = connection.execute(
            "SELECT job_id, request_digest FROM jobs WHERE platform = ? AND account = ? AND delivery_key = ?",
            (child["platform"], child["account"], child["delivery_key"]),
        ).fetchone()
        if row is None:
            return False
        if row["request_digest"] != expected:
            raise TaskError("CONFLICT", "Target delivery key is already used for different content", field="delivery_key")
        connection.execute(
            "UPDATE fanout_targets SET job_id = ? WHERE batch_id = ? AND target_index = ? "
            "AND job_id IS NULL AND error_json IS NULL",
            (row["job_id"], batch_id, index),
        )
        return True


def _fill_target(service: TaskService, batch_id: str, index: int, child: dict[str, Any]) -> None:
    row = _target_row(service, batch_id, index)
    if row["job_id"] is not None or row["error_json"] is not None:
        return
    expected = row["expected_digest"]
    if not expected or not row["normalized_json"]:
        raise TaskError("STORAGE_ERROR", "Batch target has no durable normalized snapshot")
    try:
        if _find_and_link_existing(service, batch_id, index, child, expected):
            return
        job = service.store.enqueue(json.loads(row["normalized_json"]))
        if job["request_digest"] != expected:
            raise TaskError("STORAGE_ERROR", "Batch target snapshot digest changed")
        with service.store.transaction() as connection:
            connection.execute(
                "UPDATE fanout_targets SET job_id = ? WHERE batch_id = ? AND target_index = ? "
                "AND job_id IS NULL AND error_json IS NULL",
                (job["job_id"], batch_id, index),
            )
    except TaskError as exc:
        _record_error(service, batch_id, index, exc)


def enqueue_batch(service: TaskService, payload: dict[str, Any]) -> dict[str, Any]:
    canonical, digest = _validate_batch(payload)
    batch_id = _existing_batch(service, payload["delivery_key"], canonical, digest)
    if batch_id is None:
        prepared: list[tuple[str | None, str | None, str | None]] = []
        for index in range(len(payload["targets"])):
            try:
                normalized_json = _canonical_json(service.normalize_input(_child_manifest(payload, index)))
                prepared.append((normalized_json, sha256(normalized_json.encode("utf-8")).hexdigest(), None))
            except TaskError as exc:
                if exc.code == "STORAGE_ERROR":
                    raise
                prepared.append((None, None, _canonical_json(exc.as_dict())))
        batch_id = _register(service, payload, canonical, digest, prepared)
    for index in range(len(payload["targets"])):
        _fill_target(service, batch_id, index, _child_manifest(payload, index))
    return batch_status(service, batch_id)


def batch_status(service: TaskService, batch_id: str) -> dict[str, Any]:
    if not isinstance(batch_id, str) or not batch_id:
        raise _input_error("Batch ID is required", "batch_id")
    with service.store.transaction() as connection:
        _ensure_tables(connection)
        batch = connection.execute("SELECT * FROM fanout_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise TaskError("NOT_FOUND", f"Batch {batch_id} was not found")
        rows = connection.execute(
            "SELECT * FROM fanout_targets WHERE batch_id = ? ORDER BY target_index", (batch_id,)
        ).fetchall()
        targets = []
        for row in rows:
            result: dict[str, Any] = {
                "platform": row["platform"], "account": row["account"], "job_id": row["job_id"],
                "status": "PENDING", "job_status": None, "submission": None,
                "platform_result": None, "content_id": None, "verified_at": None,
                "error": None, "evidence": [], "warnings": [],
                "account_status": None, "pause_reason": None,
            }
            if row["error_json"] is not None:
                result["status"] = "ERROR"
                result["error"] = json.loads(row["error_json"])
            elif row["job_id"] is not None:
                job_row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
                if job_row is None:
                    raise TaskError("STORAGE_ERROR", "Batch target references a missing job")
                job = service.store._job_from_row(job_row)
                account = connection.execute(
                    "SELECT status, pause_reason FROM accounts WHERE platform = ? AND account = ?",
                    (row["platform"], row["account"]),
                ).fetchone()
                result.update({
                    "status": job["status"], "job_status": job["status"],
                    "submission": job["submission"], "platform_result": job["platform_result"],
                    "content_id": job["content_id"], "verified_at": job["verified_at"],
                    "error": job["error"], "evidence": job["evidence"], "warnings": job["warnings"],
                    "account_status": account["status"] if account is not None else None,
                    "pause_reason": json.loads(account["pause_reason"]) if account is not None and account["pause_reason"] else None,
                })
                if (account is not None and account["status"] != "READY" and
                        job["submission"] == "NOT_SUBMITTED" and
                        job["status"] in {"QUEUED", "FAILED_PRE_SUBMIT", "PREPARING", "READY"}):
                    result["status"] = "PAUSED"
            targets.append(result)
    statuses = [target["status"] for target in targets]
    failures = [
        target["status"] in {"ERROR", "FAILED_PRE_SUBMIT", "UNKNOWN_COMMIT", "BLOCKED", "CANCELLED"}
        or (target["status"] == "COMPLETED" and target["platform_result"] in {"REJECTED", "DRAFT"})
        for target in targets
    ]
    if all(status == "COMPLETED" for status in statuses) and not any(failures):
        aggregate = "COMPLETED"
    elif all(failures):
        aggregate = "FAILED"
    elif any(failures):
        aggregate = "PARTIAL_FAILURE"
    else:
        aggregate = "IN_PROGRESS"
    return {
        "schema_version": 1, "batch_id": batch["batch_id"],
        "delivery_key": batch["delivery_key"], "request_digest": batch["request_digest"],
        "status": aggregate, "targets": targets,
    }
