"""Small, explicit public diagnostic vocabulary for untrusted adapter output.

Manifest and actual metadata are user content, not diagnostics. Callers apply
these functions only to errors, warnings, runtime facts, and evidence notes.
Unknown text is discarded rather than guessed safe by a redaction regex.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping
from uuid import uuid4

from orchestrator.session_storage import atomic_private_write, ensure_private_directory


_PUBLIC_MESSAGES = {
    "ACCOUNT_BUSY": "Account is busy",
    "ATTEMPT_LIMIT": "Attempt limit reached",
    "AUTH_REQUIRED": "Account authentication is required",
    "CANCELLED": "Task execution was cancelled",
    "CLEANUP_FAILED": "Resource cleanup could not be confirmed",
    "CONFLICT": "Requested content conflicts with an existing task",
    "DECLARATION_MISMATCH": "Prepared declaration does not match the request",
    "FIELD_OMITTED": "An allowed optional field was omitted",
    "IDENTITY_MISMATCH": "Account identity did not match the binding",
    "INTERNAL_ERROR": "Task operation failed",
    "INVALID_INPUT": "Input validation failed",
    "INVALID_MANIFEST": "Manifest validation failed",
    "INVALID_RESULT": "Platform result is invalid",
    "INVALID_STATE": "Task state does not allow this action",
    "LOCK_ERROR": "Account lock could not be acquired",
    "METADATA_MISMATCH": "Prepared work does not match the queued content",
    "NOT_DUE": "Scheduled local start time has not arrived",
    "NOT_FOUND": "Task was not found",
    "OWNERSHIP_LOST": "Task ownership was lost",
    "OWNERSHIP_UNAVAILABLE": "Task ownership could not be established",
    "PAGE_CLOSED": "Browser page or context was closed",
    "PREFLIGHT_FAILED": "Task preflight failed",
    "PREPARATION_FAILED": "Platform preparation failed",
    "RECONCILIATION_UNAVAILABLE": "Read-only work verification is unavailable",
    "RESOURCES_UNCONFIRMED": "Browser resource shutdown could not be confirmed",
    "STORAGE_ERROR": "Task storage operation failed",
    "SUBMISSION_UNCERTAIN": "The final action may have reached the platform",
    "TIMEOUT": "Execution stage exceeded its deadline",
    "TRANSIENT_PRE_SUBMIT": "A temporary failure occurred before submission",
    "UI_CHANGED": "Platform interface needs review",
    "UPLOAD_FAILED": "One or more media files failed processing",
    "UNSUPPORTED": "Requested capability is unsupported",
}

_SAFE_FIXED_MESSAGES = frozenset({
    "A required declaration is missing",
    "Account binding changed since enqueue",
    "Account verification or login is required",
    "Ambiguous local start time requires fold 0 or 1",
    "An exact topic candidate was not found",
    "An IANA timezone is required",
    "Browser session state could not be saved",
    "Cannot install the managed account session",
    "Content exceeds the observed page limit",
    "Cover changed since enqueue",
    "IANA timezone is unknown",
    "Local start time does not exist in this timezone",
    "Local start time is invalid",
    "Media changed since enqueue",
    "Media file is missing",
    "Media file is outside a trusted root",
    "Media is no longer ready",
    "One or more media files failed processing",
    "Optional field was omitted by the platform",
    "Platform evidence is not verified",
    "Required page control is missing or ambiguous",
    "Prepared work does not match the queued content",
    "Prepared work does not match the requested declaration",
    "Ready metadata changed before submission",
    "Session source is too large",
    "Session source must be storage-state JSON",
    "Stable account identity could not be read",
    "The final action may have reached the platform",
    "The active account does not match the bound account",
    "Task execution was cancelled",
    "Browser resource shutdown could not be confirmed",
    "Browser page or context was closed",
    "Execution stage exceeded its deadline",
    "Read-only work evidence is unavailable",
})
_CLEANUP_MESSAGE = re.compile(
    r"(?:page|context|browser|driver) shutdown (?:budget could not be read|"
    r"exceeded the cleanup deadline|exceeded its time limit|could not be confirmed)\Z"
)
_KNOWN_FIELDS = frozenset({
    "account", "action", "actual_metadata", "ai_usage", "body", "collection",
    "content_type", "cover", "declarations", "declarations.ai_usage",
    "declarations.originality_rights", "declarations.repost_authorized",
    "declarations.ownership", "declarations.repost_source",
    "delivery_key", "expected_id", "facts", "facts.ai_usage", "facts.ownership",
    "facts.repost_source", "field_policy", "field_policy.allow_omit",
    "field_policy.optional", "field_policy.required", "limits", "limits.total",
    "manifest", "max_attempts", "media", "platform", "preflight", "prepare",
    "processing", "publish_at", "reconcile", "run_at", "run_at.fold",
    "run_at.local", "run_at.timezone", "schema_version", "session_save",
    "session_source", "session_path", "stage", "submit", "tags", "title",
    "unverified", "upload", "verification_code", "cleanup", "navigation", "metadata",
})
_MEDIA_FIELD = re.compile(r"media\[[0-9]{1,3}\](?:\.(?:kind|sha256))?\Z")
_COVER_FIELD = re.compile(r"cover\.(?:kind|sha256)\Z")
_VERSION = re.compile(r"(?:[0-9]+(?:\.[0-9]+){1,5}|Chromium-local-[0-9]+|unavailable)\Z")
_STAGES = frozenset({
    "QUEUED", "prepare", "preflight", "navigation", "upload", "processing",
    "metadata", "submit", "reconcile", "session_save", "cleanup", "runtime",
    "platform", "diagnostic", "schedule", "recover", "identity", "verification",
})
_LIMIT_KEYS = frozenset({
    "total", "preflight", "prepare", "navigation", "upload", "processing",
    "metadata", "submit", "reconcile", "session_save", "cleanup",
})
_CHANNELS = frozenset({"chromium", "chrome", "chrome-beta", "chrome-dev", "msedge", "custom"})
_SCOPES = frozenset({"offline", "read_only", "live"})
_PLATFORMS = frozenset({"douyin", "xiaohongshu", "tencent"})
_CONTENT_TYPES = frozenset({"video", "note"})
_HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
_ARTIFACT = re.compile(r"[0-9a-f]{32}\.json\Z")
EVIDENCE_RETENTION_DAYS = 30


def diagnostic_error(value: Any) -> dict[str, str | None]:
    """Keep a stable business category and field, never arbitrary prose."""
    item = value if isinstance(value, Mapping) else {}
    raw_code = item.get("code")
    code = raw_code if isinstance(raw_code, str) and raw_code in _PUBLIC_MESSAGES else "INTERNAL_ERROR"
    raw_field = item.get("field")
    field = raw_field if isinstance(raw_field, str) and (
        raw_field in _KNOWN_FIELDS or _MEDIA_FIELD.fullmatch(raw_field) or _COVER_FIELD.fullmatch(raw_field)
    ) else None
    raw_message = item.get("message")
    message = (raw_message if isinstance(raw_message, str)
               and (raw_message in _SAFE_FIXED_MESSAGES or _CLEANUP_MESSAGE.fullmatch(raw_message))
               else _PUBLIC_MESSAGES[code])
    return {"code": code, "message": message, "field": field}


def diagnostic_warnings(value: Any) -> list[dict[str, str | None]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [diagnostic_error(item) for item in value[:32]]


def diagnostic_stage(stage: Any) -> str:
    return stage if isinstance(stage, str) and stage in _STAGES else "diagnostic"


def _version(value: Any) -> str | None:
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def diagnostic_event(stage: Any, details: Any) -> dict[str, Any]:
    """Allow only explicit runtime and outcome facts in public events."""
    if not isinstance(details, Mapping):
        return {}
    safe: dict[str, Any] = {}
    if details.get("driver") == "patchright":
        safe["driver"] = "patchright"
    for key in ("driver_version", "driver_pin", "browser_version", "capability_version"):
        version = _version(details.get(key))
        if version is not None:
            safe[key] = version
    scope = details.get("evidence_scope")
    if isinstance(scope, str) and scope in _SCOPES:
        safe["evidence_scope"] = scope
    platform = details.get("platform")
    if isinstance(platform, str) and platform in _PLATFORMS:
        safe["platform"] = platform
    content_type = details.get("content_type")
    if isinstance(content_type, str) and content_type in _CONTENT_TYPES:
        safe["content_type"] = content_type
    options = details.get("launch_options")
    if isinstance(options, Mapping):
        clean_options: dict[str, Any] = {}
        if type(options.get("headless")) is bool:
            clean_options["headless"] = options["headless"]
        channel = options.get("channel")
        if isinstance(channel, str) and channel in _CHANNELS:
            clean_options["channel"] = channel
        if clean_options:
            safe["launch_options"] = clean_options
    limits = details.get("execution_limits")
    if isinstance(limits, Mapping):
        clean_limits = {
            key: value for key, value in limits.items()
            if isinstance(key, str) and key in _LIMIT_KEYS and type(value) in (int, float)
            and math.isfinite(value) and 0 < value <= 86400
        }
        if clean_limits:
            safe["execution_limits"] = clean_limits
    if details.get("error") is not None:
        safe["error"] = diagnostic_error(details["error"])
    if details.get("warnings") is not None:
        safe["warnings"] = diagnostic_warnings(details["warnings"])
    result = details.get("platform_result")
    if isinstance(result, str) and result in {"ACCEPTED", "IN_REVIEW", "PUBLISHED", "REJECTED", "DRAFT"}:
        safe["platform_result"] = result
    evidence_ref = details.get("evidence_ref")
    if isinstance(evidence_ref, Mapping):
        path = evidence_ref.get("path")
        digest = evidence_ref.get("sha256")
        if (isinstance(path, str) and re.fullmatch(
            r"evidence/[0-9a-f]{32}/[0-9a-f]{32}/[0-9a-f]{32}\.json", path
        ) and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
            safe["evidence_ref"] = {"kind": "diagnostic", "path": path, "sha256": digest}
    return safe


def sanitize_public_result(value: Any) -> Any:
    """Sanitize only diagnostic fields; preserve authored content and metadata."""
    if isinstance(value, list):
        return [sanitize_public_result(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"manifest", "actual_metadata"}:
            result[key] = item
        elif key in {"error", "pause_reason"}:
            result[key] = diagnostic_error(item) if item is not None else None
        elif key == "warnings":
            result[key] = diagnostic_warnings(item)
        elif key == "details":
            result[key] = diagnostic_event(value.get("stage"), item)
        elif key == "stage":
            result[key] = diagnostic_stage(item)
        else:
            result[key] = sanitize_public_result(item)
    return result


def prune_private_evidence(runtime_dir: Path, *, now: float | None = None,
                           retention_days: int = EVIDENCE_RETENTION_DAYS) -> int:
    """Remove only expired diagnostic UUID files under the managed evidence tree."""
    if type(retention_days) is not int or retention_days < 1:
        raise ValueError("Evidence retention must be at least one day")
    root = Path(runtime_dir) / "evidence"
    if not root.exists():
        return 0
    ensure_private_directory(root)
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = 0
    for job_dir in root.iterdir():
        if not _HEX_ID.fullmatch(job_dir.name):
            continue
        if job_dir.is_symlink() or (hasattr(job_dir, "is_junction") and job_dir.is_junction()) or not job_dir.is_dir():
            continue
        for attempt_dir in job_dir.iterdir():
            if not _HEX_ID.fullmatch(attempt_dir.name):
                continue
            if (attempt_dir.is_symlink() or
                    (hasattr(attempt_dir, "is_junction") and attempt_dir.is_junction()) or
                    not attempt_dir.is_dir()):
                continue
            for artifact in attempt_dir.iterdir():
                if not _ARTIFACT.fullmatch(artifact.name) or artifact.is_symlink():
                    continue
                if artifact.is_file() and artifact.stat().st_mtime < cutoff:
                    artifact.unlink()
                    removed += 1
    return removed


def write_diagnostic_artifact(runtime_dir: Path, job_id: str, attempt_id: str,
                              stage: str, details: Mapping[str, Any]) -> dict[str, str]:
    """Persist only allowlisted diagnostics, never a screenshot or raw page state."""
    if not _HEX_ID.fullmatch(job_id) or not _HEX_ID.fullmatch(attempt_id):
        raise ValueError("Diagnostic evidence requires job and attempt identifiers")
    stage = diagnostic_stage(stage)
    safe_details = diagnostic_event(stage, details)
    root = Path(runtime_dir) / "evidence"
    job_dir, attempt_dir = root / job_id, root / job_id / attempt_id
    for directory in (root, job_dir, attempt_dir):
        ensure_private_directory(directory)
    artifact_id = uuid4().hex
    relative = Path("evidence") / job_id / attempt_id / f"{artifact_id}.json"
    payload = json.dumps({
        "schema_version": 1, "job_id": job_id, "attempt_id": attempt_id,
        "stage": stage,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "retention_days": EVIDENCE_RETENTION_DAYS,
        "details": safe_details,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    target = Path(runtime_dir) / relative
    atomic_private_write(target, payload, validate=lambda path: json.loads(path.read_bytes()))
    return {"kind": "diagnostic", "path": relative.as_posix(), "sha256": sha256(payload).hexdigest()}
