"""Public delivery checks against metadata read back from an external target."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from tests.task_fixtures import IMAGE_BYTES, make_manifest, make_service, readback_metadata


class ReadbackTarget:
    def __init__(self, service, actual, *, visible=False):
        self.service = service
        self.actual = actual
        self.visible = visible
        self.writes = []

    async def prepare(self, context):
        return self.actual

    async def submit(self, context):
        self.writes.append({
            "content_id": "remote-1", "platform": context.manifest["platform"],
            "account_id": context.manifest["expected_account_id"],
            "content_type": context.manifest["content_type"], "platform_result": "PUBLISHED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": self.actual,
            "media_sha256": [asset["sha256"] for asset in self.actual["media"]],
            "source": "work_detail",
        })
        return {"content_id": "remote-1"}

    async def reconcile(self, context):
        return list(self.writes) if self.visible else []

    async def close(self):
        pass


def test_changed_title_readback_blocks_external_submission(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path)
    job = service.enqueue(manifest)
    actual = {
        "title": "Changed on platform",
        "body": manifest["body"],
        "tags": [],
        "media": [{"kind": "video", "sha256": job["manifest"]["media"][0]["sha256"]}],
        "cover": None,
        "collection": None,
        "declarations": {},
    }
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["submission"] == "NOT_SUBMITTED"
    assert result["error"]["code"] == "METADATA_MISMATCH"
    assert result["error"]["field"] == "title"
    assert target.writes == []
    assert "SUBMITTING" not in [event["status"] for event in service.events(job["job_id"])]


@pytest.mark.parametrize("field,changed", [
    ("body", "Changed body"),
    ("tags", ["unexpected"]),
    ("collection", "Injected collection"),
    ("media[0].sha256", "0" * 64),
])
def test_changed_content_readback_blocks_external_submission(tmp_path, field, changed):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    actual = readback_metadata(job["manifest"])
    if field == "media[0].sha256":
        actual["media"][0]["sha256"] = changed
    else:
        actual[field] = changed
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "METADATA_MISMATCH"
    assert result["error"]["field"] == field
    assert target.writes == []


def test_unread_required_metadata_cannot_approve_submission(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    actual = readback_metadata(job["manifest"])
    del actual["body"]
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["field"] == "body"
    assert target.writes == []


def test_unknown_readback_field_is_not_accepted(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))
    actual = readback_metadata(job["manifest"])
    actual["unverified"] = "value"
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "METADATA_MISMATCH"
    assert result["error"]["field"] == "unverified"
    assert target.writes == []


def test_note_image_count_must_match_upload_order(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path, content_type="note")
    second = tmp_path / "media" / "second.png"
    second.write_bytes(IMAGE_BYTES + b"second")
    manifest["media"].append(str(second))
    job = service.enqueue(manifest)
    actual = readback_metadata(job["manifest"])
    actual["media"] = actual["media"][:1]
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "METADATA_MISMATCH"
    assert result["error"]["field"] == "media"
    assert target.writes == []


def test_cover_digest_must_match_selected_image(tmp_path):
    service = make_service(tmp_path)
    cover = tmp_path / "media" / "cover.png"
    cover.write_bytes(IMAGE_BYTES)
    job = service.enqueue(make_manifest(tmp_path, cover=str(cover)))
    actual = readback_metadata(job["manifest"])
    actual["cover"]["sha256"] = "0" * 64
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "METADATA_MISMATCH"
    assert result["error"]["field"] == "cover.sha256"
    assert target.writes == []


def test_changed_rights_readback_blocks_submission(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, declarations={"originality_rights": True}))
    actual = readback_metadata(job["manifest"])
    actual["declarations"]["originality_rights"] = False
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "DECLARATION_MISMATCH"
    assert result["error"]["field"] == "declarations.originality_rights"
    assert target.writes == []


def test_explicit_rights_readback_can_reach_submission(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path, declarations={"originality_rights": True}))
    target = ReadbackTarget(service, readback_metadata(job["manifest"]))

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["actual_metadata"]["declarations"] == {"originality_rights": True}
    assert len(target.writes) == 1
    assert "SUBMITTING" in [event["status"] for event in service.events(job["job_id"])]


def test_allowed_optional_tag_omission_is_recorded_as_a_warning(tmp_path):
    service = make_service(tmp_path)
    policy = {"required": ["title", "body", "media"],
              "optional": ["tags", "cover", "collection"], "allow_omit": ["tags"]}
    job = service.enqueue(make_manifest(tmp_path, tags=["requested"], field_policy=policy))
    actual = readback_metadata(job["manifest"])
    actual["tags"] = []
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["actual_metadata"]["tags"] == []
    assert any(item["code"] == "FIELD_OMITTED" and item["field"] == "tags" for item in result["warnings"])
    assert len(target.writes) == 1


def test_only_explicitly_allowed_missing_optional_field_can_degrade(tmp_path):
    service = make_service(tmp_path)
    policy = {"required": ["title", "body", "media"],
              "optional": ["tags", "cover", "collection"], "allow_omit": ["collection"]}
    job = service.enqueue(make_manifest(tmp_path, collection="Requested collection", field_policy=policy))
    actual = readback_metadata(job["manifest"])
    del actual["collection"]
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["actual_metadata"]["collection"] is None
    assert any(item["code"] == "FIELD_OMITTED" and item["field"] == "collection"
               for item in result["warnings"])
    assert len(target.writes) == 1


def test_allowed_tag_omission_warning_survives_read_only_recovery(tmp_path):
    service = make_service(tmp_path)
    policy = {"required": ["title", "body", "media"],
              "optional": ["tags", "cover", "collection"], "allow_omit": ["tags"]}
    job = service.enqueue(make_manifest(tmp_path, tags=["requested"], field_policy=policy))
    actual = readback_metadata(job["manifest"])
    actual["tags"] = []
    target = ReadbackTarget(service, actual, visible=False)

    unknown = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert unknown is not None
    assert unknown["status"] == "UNKNOWN_COMMIT"
    assert any(item["code"] == "FIELD_OMITTED" and item["field"] == "tags" for item in unknown["warnings"])
    assert len(target.writes) == 1

    target.visible = True
    recovered = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert recovered is not None
    assert recovered["status"] == "COMPLETED"
    assert recovered["actual_metadata"]["tags"] == []
    assert any(item["code"] == "FIELD_OMITTED" and item["field"] == "tags" for item in recovered["warnings"])
    assert len(target.writes) == 1
    assert len(service.attempts(job["job_id"])) == 1


def test_optional_field_cannot_be_replaced_with_another_value(tmp_path):
    service = make_service(tmp_path)
    policy = {"required": ["title", "body", "media"],
              "optional": ["tags", "cover", "collection"], "allow_omit": ["tags"]}
    job = service.enqueue(make_manifest(tmp_path, tags=["requested"], field_policy=policy))
    actual = readback_metadata(job["manifest"])
    actual["tags"] = ["other"]
    target = ReadbackTarget(service, actual)

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["code"] == "METADATA_MISMATCH"
    assert result["error"]["field"] == "tags"
    assert target.writes == []
