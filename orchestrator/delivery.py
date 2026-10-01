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
        live_douyin = candidate.get("evidence_scope") == "douyin_video_v1"
        live_xhs = candidate.get("evidence_scope") == "xiaohongshu_video_v1"
        live_channels = candidate.get("evidence_scope") == "channels_video_v1"
        # This observed endpoint reports integer epoch seconds. Allow only its
        # subsecond truncation, not an arbitrary clock-skew matching window.
        earliest = submitted_at.replace(microsecond=0) if live_douyin or live_xhs or live_channels else submitted_at
        if created_at is None or not earliest <= created_at <= submitted_at + timedelta(hours=1):
            continue
        if (candidate.get("platform") != job["platform"]
                or candidate.get("account_id") != manifest["expected_account_id"]
                or candidate.get("content_type") != manifest["content_type"]
                or candidate.get("platform_result") not in PLATFORM_RESULTS
                or candidate.get("source") not in {"work_detail", "work_list"}
                or not isinstance(candidate.get("content_id"), str) or not candidate["content_id"].strip()):
            continue
        receipt_id = attempt.get("receipt_id")
        if receipt_id and candidate["content_id"] != receipt_id:
            continue
        if live_douyin or live_xhs or live_channels:
            actual = job["actual_metadata"]
            upload = [item for item in job.get("evidence", []) if item.get("kind") == "uploaded_video"
                      and item.get("attempt_id") == attempt["attempt_id"]]
            if (job["platform"] != ("douyin" if live_douyin else "xiaohongshu" if live_xhs else "tencent") or manifest["content_type"] != "video"
                    or not isinstance(actual, dict) or actual.get("tags") != []
                    or actual.get("cover") is not None or actual.get("collection") is not None
                    or actual.get("declarations") != {}
                    or not isinstance(candidate.get("remote_video_id"), str) or not candidate["remote_video_id"]
                    or len(upload) != 1 or len(expected_media) != 1
                    or upload[0].get("sha256") != expected_media[0]
                    or candidate.get("metadata") != {key: actual[key] for key in ("title", "body", "tags")}):
                continue
            if live_douyin:
                if not receipt_id or upload[0].get("video_id") != candidate["remote_video_id"]:
                    continue
            else:
                # XHS converts the temporary upload key into a canonical file
                # ID. Its independent detail exposes the original video MD5;
                # never relabel that field as SHA256 or copy local hashes into it.
                source_md5 = upload[0].get("md5")
                if (not isinstance(source_md5, str) or len(source_md5) != 32
                        or any(char not in "0123456789abcdef" for char in source_md5)
                        or candidate.get("remote_media_md5") != source_md5
                        or (not receipt_id and candidate.get("listing_complete") is not True)):
                    continue
                if live_channels:
                    from orchestrator.platforms.channels_delivery import ChannelsVideoPort

                    if (candidate.get('source') != 'work_detail'
                            or candidate.get('account_evidence') not in {'authenticated_creator_list', 'work_detail'}):
                        continue
                    replies = [item for item in job.get('evidence', []) if item.get('kind') == 'submission_response'
                               and item.get('attempt_id') == attempt['attempt_id']]
                    if not receipt_id and (len(replies) != 1 or not ChannelsVideoPort.positive_acknowledgement(replies[0])):
                        continue
            matches.append(candidate)
            continue
        if candidate.get("metadata") != job["actual_metadata"]:
            continue
        media = candidate.get("media_sha256")
        if media is not None and media != expected_media:
            continue
        if not receipt_id and (not expected_media or media != expected_media):
            continue
        matches.append(candidate)
    return matches[0] if len(matches) == 1 else None
