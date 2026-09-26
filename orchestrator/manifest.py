"""Normalize and recheck the local content selected for one delivery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from orchestrator.errors import TaskError


_INPUT_FIELDS = frozenset({
    "schema_version", "delivery_key", "platform", "account", "content_type",
    "title", "body", "tags", "media", "cover", "collection", "facts",
    "declarations", "field_policy",
})

# Intake ceilings are intentionally conservative offline gates, not claims
# about current platform limits.
@dataclass(frozen=True)
class Capability:
    title_limit: int
    body_limit: int
    tag_limit: int
    media_limit: int
    cover_supported: bool
    collection_supported: bool


_CAPABILITIES = {
    ("douyin", "video"): Capability(title_limit=55, body_limit=2200, tag_limit=5, media_limit=1,
                                    cover_supported=True, collection_supported=True),
    ("douyin", "note"): Capability(title_limit=30, body_limit=1000, tag_limit=5, media_limit=9,
                                   cover_supported=True, collection_supported=True),
    ("xiaohongshu", "video"): Capability(title_limit=20, body_limit=1000, tag_limit=10, media_limit=1,
                                         cover_supported=True, collection_supported=False),
    ("xiaohongshu", "note"): Capability(title_limit=20, body_limit=1000, tag_limit=10, media_limit=9,
                                        cover_supported=True, collection_supported=False),
    ("tencent", "video"): Capability(title_limit=30, body_limit=1000, tag_limit=5, media_limit=1,
                                     cover_supported=True, collection_supported=True),
}


def freeze_manifest(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a recursively read-only view without doing any file access."""
    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, (list, tuple)):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(value)


def normalize_manifest(raw: dict[str, Any], media_roots: tuple[Path, ...], expected_id: str) -> dict[str, Any]:
    for field in ("publish_at", "run_at"):
        if field in raw:
            raise TaskError("UNSUPPORTED", "Scheduling is not supported by this task stage", field=field)
    if any(not isinstance(key, str) for key in raw):
        raise TaskError("INVALID_INPUT", "Manifest keys must be strings", field="manifest")
    extra = sorted(raw.keys() - _INPUT_FIELDS)
    if extra:
        raise TaskError("INVALID_INPUT", "Manifest field is not supported", field=extra[0])
    try:
        normalized = json.loads(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TaskError("INVALID_INPUT", "Manifest contains a non-JSON value", field="manifest") from exc
    _validate_facts(normalized)
    _validate_field_policy(normalized)
    _validate_content(normalized)
    normalized["expected_account_id"] = expected_id
    media = normalized.get("media")
    if not isinstance(media, list) or not media:
        raise TaskError("INVALID_INPUT", "At least one media file is required", field="media")
    normalized["media"] = [inspect_media(path, media_roots, f"media[{index}]") for index, path in enumerate(media)]
    required_kind = "video" if normalized["content_type"] == "video" else "image"
    for index, item in enumerate(normalized["media"]):
        if item["kind"] != required_kind:
            raise TaskError("UNSUPPORTED", "Media kind does not match content type", field=f"media[{index}]")
    if normalized.get("cover") is not None:
        normalized["cover"] = inspect_media(normalized["cover"], media_roots, "cover")
        if normalized["cover"]["kind"] != "image":
            raise TaskError("UNSUPPORTED", "Cover must be an image", field="cover")
    return normalized


def _validate_facts(manifest: dict[str, Any]) -> None:
    facts = manifest.get("facts")
    if not isinstance(facts, dict) or set(facts) != {"ownership", "ai_usage", "repost_source"}:
        raise TaskError("INVALID_INPUT", "Ownership, AI use, and repost source must be stated separately", field="facts")
    if not isinstance(facts["ownership"], str) or facts["ownership"] not in {"original", "repost"}:
        raise TaskError("INVALID_INPUT", "Ownership fact is invalid", field="facts.ownership")
    if not isinstance(facts["ai_usage"], str) or facts["ai_usage"] not in {"none", "assisted", "generated"}:
        raise TaskError("INVALID_INPUT", "AI use fact is invalid", field="facts.ai_usage")
    source = facts["repost_source"]
    if facts["ownership"] == "repost":
        if not isinstance(source, str) or not source.strip():
            raise TaskError("INVALID_INPUT", "Reposted content requires its source", field="facts.repost_source")
    elif source is not None:
        raise TaskError("INVALID_INPUT", "Original content cannot have a repost source", field="facts.repost_source")
    declarations = manifest.get("declarations")
    if not isinstance(declarations, dict):
        raise TaskError("INVALID_INPUT", "Declarations must be explicit", field="declarations")
    for field, value in declarations.items():
        if field in {"originality_rights", "repost_authorized"}:
            if type(value) is not bool:
                raise TaskError("INVALID_INPUT", "Rights declaration must be boolean", field=f"declarations.{field}")
            required_ownership = "original" if field == "originality_rights" else "repost"
            if value and facts["ownership"] != required_ownership:
                raise TaskError("INVALID_INPUT", "Rights declaration contradicts ownership", field=f"declarations.{field}")
        elif field not in facts:
            raise TaskError("INVALID_INPUT", "Unknown declaration", field=f"declarations.{field}")
        elif value != facts[field]:
            raise TaskError("INVALID_INPUT", "Declaration contradicts the stated fact", field=f"declarations.{field}")


def _validate_field_policy(manifest: dict[str, Any]) -> None:
    policy = manifest.get("field_policy")
    fields = {"title", "body", "media", "tags", "cover", "collection"}
    if not isinstance(policy, dict) or set(policy) != {"required", "optional", "allow_omit"}:
        raise TaskError("INVALID_INPUT", "Field policy must be explicit", field="field_policy")
    for name in ("required", "optional", "allow_omit"):
        value = policy[name]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value) or len(value) != len(set(value)):
            raise TaskError("INVALID_INPUT", "Field policy list is invalid", field=f"field_policy.{name}")
    required, optional, allow_omit = (set(policy[name]) for name in ("required", "optional", "allow_omit"))
    if required | optional != fields or required & optional or not {"title", "body", "media"} <= required:
        raise TaskError("INVALID_INPUT", "Required and optional fields are inconsistent", field="field_policy")
    if not allow_omit <= optional:
        raise TaskError("INVALID_INPUT", "Only optional fields may be omitted", field="field_policy.allow_omit")
    for field in required:
        if field in {"title", "body", "media"}:
            continue
        if not manifest.get(field):
            raise TaskError("INVALID_INPUT", "Required field is missing", field=field)


def _validate_content(manifest: dict[str, Any]) -> None:
    limits = _CAPABILITIES.get((manifest["platform"], manifest["content_type"]))
    if limits is None:
        raise TaskError("UNSUPPORTED", "Content path is not supported", field="content_type")
    for field, maximum in (("title", limits.title_limit), ("body", limits.body_limit)):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise TaskError("INVALID_INPUT", f"{field} is missing or exceeds the offline limit", field=field)
    tags = manifest.get("tags")
    if not isinstance(tags, list) or len(tags) > limits.tag_limit or any(
        not isinstance(tag, str) or not tag.strip() or len(tag) > 20 for tag in tags
    ) or len(tags) != len(set(tags)):
        raise TaskError("INVALID_INPUT", "Tags exceed the offline capability", field="tags")
    media = manifest.get("media")
    if not isinstance(media, list) or not 1 <= len(media) <= limits.media_limit:
        raise TaskError("INVALID_INPUT", "Media count exceeds the offline capability", field="media")
    if manifest.get("cover") is not None and not limits.cover_supported:
        raise TaskError("UNSUPPORTED", "Cover is not supported for this content path", field="cover")
    collection = manifest.get("collection")
    if collection is not None:
        if not limits.collection_supported:
            raise TaskError("UNSUPPORTED", "Collection is not supported for this content path", field="collection")
        if not isinstance(collection, str) or not collection.strip() or len(collection) > 64:
            raise TaskError("INVALID_INPUT", "Collection value is invalid", field="collection")


def inspect_media(value: Any, media_roots: tuple[Path, ...], field: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise TaskError("INVALID_INPUT", "Media path must be absolute", field=field)
    try:
        path = Path(value).resolve(strict=True)
    except OSError as exc:
        raise TaskError("INVALID_INPUT", "Media file is missing", field=field) from exc
    if not any(path.is_relative_to(root) for root in media_roots):
        raise TaskError("INVALID_INPUT", "Media file is outside a trusted root", field=field)
    if not path.is_file():
        raise TaskError("INVALID_INPUT", "Media path is not a file", field=field)
    try:
        with path.open("rb") as stream:
            version = path.stat()
            header = stream.read(32)
            extension = path.suffix.lower()
            box_size = int.from_bytes(header[:4], "big") if len(header) >= 4 else 0
            if (extension == ".mp4" and len(header) >= 16 and header[4:8] == b"ftyp"
                    and 16 <= box_size <= version.st_size and box_size % 4 == 0
                    and header[8:12] in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1"}):
                kind = "video"
            elif extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
                kind = "image"
            elif extension in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"):
                kind = "image"
            else:
                raise TaskError("UNSUPPORTED", "Media type or signature is not supported", field=field)
            digest = hashlib.sha256()
            digest.update(header)
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            after = path.stat()
    except OSError as exc:
        raise TaskError("INVALID_INPUT", "Media file cannot be read", field=field) from exc
    if (version.st_size, version.st_mtime_ns, version.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise TaskError("INVALID_INPUT", "Media changed while being inspected", field=field)
    return {"path": str(path), "kind": kind, "sha256": digest.hexdigest(),
            "version": {"size_bytes": version.st_size, "modified_ns": version.st_mtime_ns}}


def preflight_manifest(
    manifest: Mapping[str, Any], media_roots: tuple[Path, ...], expected_id: str,
) -> Mapping[str, Any]:
    if manifest.get("expected_account_id") != expected_id:
        raise TaskError("INVALID_INPUT", "Account binding changed since enqueue", field="account")
    media = manifest.get("media")
    if not isinstance(media, list) or not media:
        raise TaskError("INVALID_INPUT", "Persisted media list is invalid", field="media")
    for index, item in enumerate(media):
        field = f"media[{index}]"
        if not isinstance(item, dict):
            raise TaskError("INVALID_INPUT", "Persisted media item is invalid", field=field)
        current = inspect_media(item.get("path"), media_roots, field)
        if current != item:
            raise TaskError("INVALID_INPUT", "Media changed since enqueue", field=field)
    cover = manifest.get("cover")
    if cover is not None:
        if not isinstance(cover, dict):
            raise TaskError("INVALID_INPUT", "Persisted cover is invalid", field="cover")
        current_cover = inspect_media(cover.get("path"), media_roots, "cover")
        if current_cover != cover or current_cover["kind"] != "image":
            raise TaskError("INVALID_INPUT", "Cover changed since enqueue", field="cover")
    return freeze_manifest(manifest)
