"""Interpret local start times as one durable UTC instant."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from orchestrator.errors import TaskError


_LOCAL_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?\Z")


def normalize_run_at(value: Any) -> dict[str, Any]:
    """Resolve an explicit local time and IANA zone, rejecting DST gaps and guesses."""
    if not isinstance(value, dict) or set(value) - {"local", "timezone", "fold"}:
        raise TaskError("INVALID_INPUT", "run_at requires local time and timezone", field="run_at")
    local_text = value.get("local")
    if not isinstance(local_text, str) or not _LOCAL_TIME.fullmatch(local_text):
        raise TaskError("INVALID_INPUT", "Local start time must include date and minute without an offset", field="run_at.local")
    try:
        local = datetime.fromisoformat(local_text)
    except ValueError as exc:
        raise TaskError("INVALID_INPUT", "Local start time is invalid", field="run_at.local") from exc
    zone_key = value.get("timezone")
    if not isinstance(zone_key, str) or not zone_key or zone_key.strip() != zone_key:
        raise TaskError("INVALID_INPUT", "An IANA timezone is required", field="run_at.timezone")
    try:
        zone = ZoneInfo(zone_key)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise TaskError("INVALID_INPUT", "IANA timezone is unknown", field="run_at.timezone") from exc

    possible: dict[int, datetime] = {}
    for fold in (0, 1):
        instant = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        back = instant.astimezone(zone)
        if back.replace(tzinfo=None) == local and back.fold == fold:
            possible[fold] = instant
    if not possible:
        raise TaskError("INVALID_INPUT", "Local start time does not exist in this timezone", field="run_at.local")
    ambiguous = len(set(possible.values())) == 2
    requested_fold = value.get("fold")
    if ambiguous and "fold" not in value:
        raise TaskError("INVALID_INPUT", "Ambiguous local start time requires fold 0 or 1", field="run_at.fold")
    if "fold" in value and (type(requested_fold) is not int or requested_fold not in possible):
        raise TaskError("INVALID_INPUT", "fold must select a valid occurrence", field="run_at.fold")
    selected_fold = requested_fold if "fold" in value else 0
    if selected_fold not in possible:
        raise TaskError("INVALID_INPUT", "fold must select a valid occurrence", field="run_at.fold")
    return {
        "local": local_text,
        "timezone": zone_key,
        "utc": possible[selected_fold].isoformat(),
        "fold": selected_fold,
    }


def is_due(manifest: Mapping[str, Any], now: datetime) -> bool:
    """Return whether a queued task's persisted UTC start time has arrived."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Scheduling clock must return an aware datetime")
    schedule = manifest.get("run_at")
    if schedule is None:
        return True
    return now.astimezone(timezone.utc) >= datetime.fromisoformat(schedule["utc"]).astimezone(timezone.utc)
