"""Conservative matching of read-only platform work evidence."""

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


PLATFORM_RESULTS = frozenset({"ACCEPTED", "IN_REVIEW", "PUBLISHED", "REJECTED", "DRAFT"})


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def match_work(job: Mapping[str, Any], attempt: Mapping[str, Any], candidates: Sequence[dict]) -> dict | None:
    """An ID helps locate a work; it never establishes its publication state."""
    manifest = job["manifest"]
    submitted_at = _timestamp(attempt.get("submission_started_at"))
    if submitted_at is None or not manifest.get("expected_account_id"):
        return None
    expected_media = [item["sha256"] for item in manifest["media"]]
    matches = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        created_at = _timestamp(candidate.get("created_at"))
        if created_at is None or not submitted_at <= created_at <= submitted_at + timedelta(hours=1):
            continue
        if (candidate.get("platform") != job["platform"]
                or candidate.get("account_id") != manifest["expected_account_id"]
                or candidate.get("content_type") != manifest["content_type"]
                or candidate.get("platform_result") not in PLATFORM_RESULTS
                or candidate.get("source") not in {"work_detail", "work_list"}
                or not isinstance(candidate.get("content_id"), str) or not candidate["content_id"].strip()
                or candidate.get("metadata") != job["actual_metadata"]):
            continue
        receipt_id = attempt.get("receipt_id")
        if receipt_id and candidate["content_id"] != receipt_id:
            continue
        media = candidate.get("media_sha256")
        if media is not None and media != expected_media:
            continue
        if not receipt_id and (not expected_media or media != expected_media):
            continue
        matches.append(candidate)
    return matches[0] if len(matches) == 1 else None
