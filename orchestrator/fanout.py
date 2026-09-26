"""Durable intent and per-target receipts for one multi-platform delivery."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Any

from orchestrator.errors import TaskError
from orchestrator.manifest import DECLARATION_FIELDS, FACT_FIELDS, FIELD_POLICY_FIELDS, MANIFEST_FIELDS
from orchestrator.store import _canonical_json

if TYPE_CHECKING:
    from orchestrator.service import TaskService


_BATCH_FIELDS = (MANIFEST_FIELDS - {"platform", "account"}) | {"targets"}
_OVERRIDE_FIELDS = MANIFEST_FIELDS - {"schema_version", "delivery_key", "platform", "account"}


def _input_error(message: str, field: str) -> TaskError:
    return TaskError("INVALID_INPUT", message, field=field)


def _check_content_shapes(content: dict[str, Any], prefix: str) -> None:
    """Reject nested unknown fields and values that could carry credentials."""
    for name in ("facts", "declarations", "field_policy"):
        if name not in content:
            continue
        value = content[name]
        allowed = {"facts": FACT_FIELDS, "declarations": DECLARATION_FIELDS,
                   "field_policy": FIELD_POLICY_FIELDS}[name]
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
    if "publish_at" in payload:
        raise TaskError("UNSUPPORTED", "Platform publication scheduling is not supported", field="publish_at")
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
        if "publish_at" in overrides:
            raise TaskError("UNSUPPORTED", "Platform publication scheduling is not supported", field="publish_at")
        extra = sorted(overrides.keys() - _OVERRIDE_FIELDS)
        if extra:
            raise _input_error("Override field is not supported", prefix + "overrides." + extra[0])
        _check_content_shapes(overrides, prefix + "overrides.")
    try:
        canonical = _canonical_json(payload)
    except (TypeError, ValueError) as exc:
        raise _input_error("Batch contains a non-JSON value", "manifest") from exc
    return canonical, sha256(canonical.encode("utf-8")).hexdigest()


def _child_manifest(payload: dict[str, Any], index: int) -> dict[str, Any]:
    target = payload["targets"][index]
    child = {key: value for key, value in payload.items() if key != "targets"}
    child.update(target.get("overrides", {}))
    child["platform"] = target["platform"]
    child["account"] = target["account"]
    return child


def _fill_target(service: TaskService, batch_id: str, index: int) -> None:
    target = service.store.fanout_target(batch_id, index)
    if target["job_id"] is not None or target["error"] is not None:
        return
    expected, snapshot = target["expected_digest"], target["manifest"]
    if not expected or not isinstance(snapshot, dict):
        raise TaskError("STORAGE_ERROR", "Batch target has no durable normalized snapshot")
    try:
        if service.store.link_existing_fanout_child(batch_id, index):
            return
        job = service.store.enqueue(snapshot)
        if job["request_digest"] != expected:
            raise TaskError("STORAGE_ERROR", "Batch target snapshot digest changed")
        if not service.store.link_existing_fanout_child(batch_id, index):
            raise TaskError("STORAGE_ERROR", "Batch child could not be linked")
    except TaskError as exc:
        if exc.code == "STORAGE_ERROR":
            raise
        service.store.record_fanout_error(batch_id, index, exc.as_dict())


def enqueue_batch(service: TaskService, payload: dict[str, Any]) -> dict[str, Any]:
    canonical, digest = _validate_batch(payload)
    batch_id = service.store.find_fanout_batch(payload["delivery_key"], canonical, digest)
    if batch_id is None:
        prepared: list[dict[str, Any]] = []
        for index, target in enumerate(payload["targets"]):
            try:
                snapshot, error = service.normalize_input(_child_manifest(payload, index)), None
            except TaskError as exc:
                if exc.code == "STORAGE_ERROR":
                    raise
                snapshot, error = None, exc.as_dict()
            prepared.append({"platform": target["platform"], "account": target["account"],
                             "manifest": snapshot, "error": error})
        batch_id = service.store.register_fanout_batch(payload["delivery_key"], canonical, digest, prepared)
    for index in range(len(payload["targets"])):
        _fill_target(service, batch_id, index)
    return batch_status(service, batch_id)


def batch_status(service: TaskService, batch_id: str) -> dict[str, Any]:
    if not isinstance(batch_id, str) or not batch_id:
        raise _input_error("Batch ID is required", "batch_id")
    batch = service.store.fanout_status_rows(batch_id)
    targets = []
    for row in batch["targets"]:
        result: dict[str, Any] = {
            "platform": row["platform"], "account": row["account"], "job_id": row["job_id"],
            "status": "PENDING", "job_status": None, "submission": None,
            "platform_result": None, "content_id": None, "verified_at": None,
            "error": None, "evidence": [], "warnings": [],
            "account_status": None, "pause_reason": None,
        }
        if row["error"] is not None:
            result["status"] = "ERROR"
            result["error"] = row["error"]
        elif row["job"] is not None:
            job = row["job"]
            result.update({
                "status": job["status"], "job_status": job["status"],
                "submission": job["submission"], "platform_result": job["platform_result"],
                "content_id": job["content_id"], "verified_at": job["verified_at"],
                "error": job["error"], "evidence": job["evidence"], "warnings": job["warnings"],
                "account_status": row["account_status"], "pause_reason": row["pause_reason"],
            })
            if (row["account_status"] is not None and row["account_status"] != "READY" and
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
