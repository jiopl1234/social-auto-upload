"""Offline inputs for the public task-service tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping

from orchestrator.service import TaskService


EXPECTED_DELIVERY_PATHS = (
    ("douyin", "video"), ("douyin", "note"), ("xiaohongshu", "video"),
    ("xiaohongshu", "note"), ("tencent", "video"),
)

VIDEO_BYTES = (
    b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    b"\x00\x00\x00\x08mdat"
)
IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
)


def make_service(tmp_path: Path) -> TaskService:
    media_root = tmp_path / "media"
    media_root.mkdir(exist_ok=True)
    service = TaskService(
        tmp_path / "jobs.sqlite3",
        runtime_dir=tmp_path / "runtime",
        media_roots=[media_root],
    )
    for platform in ("douyin", "xiaohongshu", "tencent"):
        bind_test_account(service, tmp_path, platform, "creator")
    return service


def bind_test_account(service: TaskService, tmp_path: Path, platform: str, account: str) -> None:
    source_root = tmp_path / "account-sources"
    source_root.mkdir(exist_ok=True)
    source = source_root / f"{platform}-{account}.json"
    source.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    service.bind_account(platform, account, f"{platform}:{account}", source)


def make_manifest(
    tmp_path: Path,
    platform: str = "douyin",
    account: str = "creator",
    key: str = "one",
    content_type: str = "video",
    **overrides: Any,
) -> dict[str, Any]:
    media_root = tmp_path / "media"
    media_root.mkdir(exist_ok=True)
    if content_type == "note":
        media_path = media_root / f"{platform}-{key}.png"
        media_bytes = IMAGE_BYTES
    else:
        media_path = media_root / f"{platform}-{key}.mp4"
        media_bytes = VIDEO_BYTES
    if not media_path.exists():
        media_path.write_bytes(media_bytes)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "delivery_key": key,
        "platform": platform,
        "account": account,
        "content_type": content_type,
        "title": "Original title",
        "body": "Original body",
        "tags": [],
        "media": [str(media_path.resolve())],
        "facts": {"ownership": "original", "ai_usage": "none", "repost_source": None},
        "declarations": {},
        "field_policy": {
            "required": ["title", "body", "media"],
            "optional": ["tags", "cover", "collection"],
            "allow_omit": [],
        },
    }
    manifest.update(overrides)
    return manifest


def readback_metadata(manifest: Mapping[str, Any]) -> dict:
    """Content applied by the controlled local target, independent of validators."""
    return {
        "title": manifest["title"], "body": manifest["body"], "tags": list(manifest["tags"]),
        "media": [{"kind": item["kind"], "sha256": item["sha256"]} for item in manifest["media"]],
        "cover": ({"kind": manifest["cover"]["kind"], "sha256": manifest["cover"]["sha256"]}
                  if manifest.get("cover") is not None else None),
        "collection": manifest.get("collection"), "declarations": dict(manifest["declarations"]),
    }
