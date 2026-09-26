"""Explicit local account binding for task execution."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import NoReturn

from orchestrator.errors import TaskError
from orchestrator.ownership import AccountLease
from orchestrator.session_storage import atomic_private_write
from orchestrator.store import Store, _utc_now


PLATFORMS = frozenset({"douyin", "xiaohongshu", "tencent"})
_ACCOUNT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant: {value}")


def validate_account(platform: str, account: str) -> None:
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise TaskError("UNSUPPORTED", "Platform is not supported by the task interface", field="platform")
    if not isinstance(account, str) or not _ACCOUNT_NAME.fullmatch(account) or account in {".", ".."}:
        raise TaskError("INVALID_INPUT", "Account must be a safe local name", field="account")


def managed_session_path(runtime_dir: Path, platform: str, account: str) -> Path:
    key = hashlib.sha256(f"{platform}\0{account}".encode("utf-8")).hexdigest()
    return runtime_dir / "sessions" / f"{key}.json"


def _storage_state_bytes(source: Path | str) -> bytes:
    try:
        with Path(source).open("rb") as stream:
            data = stream.read(5_000_001)
    except (OSError, ValueError) as exc:
        raise TaskError("INVALID_INPUT", "Cannot read the session source", field="session_source") from exc
    if len(data) > 5_000_000:
        raise TaskError("INVALID_INPUT", "Session source is too large", field="session_source")
    try:
        state = json.loads(data, parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError) as exc:
        raise TaskError("INVALID_INPUT", "Session source must be storage-state JSON", field="session_source") from exc
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
        raise TaskError("INVALID_INPUT", "Session source must be storage-state JSON", field="session_source")
    for cookie in state["cookies"]:
        if not isinstance(cookie, dict) or any(
            not isinstance(cookie.get(key), str)
            for key in ("name", "value", "domain", "path", "sameSite")
        ) or any(not cookie[key] for key in ("name", "domain", "path", "sameSite")) \
                or type(cookie.get("httpOnly")) is not bool or type(cookie.get("secure")) is not bool:
            raise TaskError("INVALID_INPUT", "Session source contains an invalid cookie", field="session_source")
        expires = cookie.get("expires")
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise TaskError("INVALID_INPUT", "Session source contains an invalid cookie", field="session_source")
    for origin in state["origins"]:
        if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str) or not origin["origin"]:
            raise TaskError("INVALID_INPUT", "Session source contains an invalid origin", field="session_source")
        entries = origin.get("localStorage")
        if not isinstance(entries, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("name"), str)
            or not isinstance(item.get("value"), str) for item in entries
        ):
            raise TaskError("INVALID_INPUT", "Session source contains invalid local storage", field="session_source")
    return data


def _install_session(path: Path, data: bytes) -> None:
    try:
        atomic_private_write(path, data, validate=_storage_state_bytes)
    except (OSError, ValueError, TaskError):
        raise TaskError("STORAGE_ERROR", "Cannot install the managed account session") from None


def bind_account(
    store: Store,
    runtime_dir: Path,
    platform: str,
    account: str,
    expected_id: str,
    session_source: Path | str,
) -> dict:
    validate_account(platform, account)
    if not isinstance(expected_id, str) or not expected_id.strip() or len(expected_id) > 256 or any(ord(c) < 32 for c in expected_id):
        raise TaskError("INVALID_INPUT", "Expected account ID is required", field="expected_id")
    data = _storage_state_bytes(session_source)
    path = managed_session_path(runtime_dir, platform, account)
    with AccountLease(runtime_dir, platform, account):
        with store.transaction() as connection:
            existing = connection.execute(
                "SELECT expected_id FROM accounts WHERE platform = ? AND account = ?",
                (platform, account),
            ).fetchone()
            if existing is not None and existing["expected_id"] not in (None, expected_id):
                raise TaskError("CONFLICT", "Account is already bound to a different ID", field="expected_id")
            _install_session(path, data)
            connection.execute(
                """INSERT INTO accounts (platform, account, expected_id, session_path, status, pause_reason, updated_at)
                   VALUES (?, ?, ?, ?, 'READY', NULL, ?)
                   ON CONFLICT(platform, account) DO UPDATE SET
                   expected_id = excluded.expected_id, session_path = excluded.session_path,
                   status = 'READY', pause_reason = NULL, updated_at = excluded.updated_at""",
                (platform, account, expected_id, str(path), _utc_now()),
            )
    return {"platform": platform, "account": account, "expected_id": expected_id,
            "session_path": str(path), "status": "READY"}


def get_binding(store: Store, runtime_dir: Path, platform: str, account: str) -> dict:
    validate_account(platform, account)
    with store.transaction() as connection:
        row = connection.execute(
            "SELECT expected_id, session_path, status FROM accounts WHERE platform = ? AND account = ?",
            (platform, account),
        ).fetchone()
    if row is None or not row["expected_id"] or not row["session_path"]:
        raise TaskError("INVALID_INPUT", "Account must be bound before enqueue", field="account")
    expected_path = managed_session_path(runtime_dir, platform, account)
    if Path(row["session_path"]) != expected_path or not expected_path.is_file():
        raise TaskError("INVALID_INPUT", "Managed account session is unavailable", field="account")
    return dict(row)
