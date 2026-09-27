"""Public intake and preparation behavior for immutable task manifests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from orchestrator.errors import TaskError
from orchestrator.service import TaskService

from tests.task_fixtures import IMAGE_BYTES, VIDEO_BYTES, make_manifest, make_service, readback_metadata


class RecordingTarget:
    def __init__(self):
        self.prepared = 0
        self.submitted = 0

    async def prepare(self, _context):
        self.prepared += 1
        return readback_metadata(_context.manifest)

    async def submit(self, _context):
        self.submitted += 1
        return {"id": "unexpected"}

    async def reconcile(self, _context):
        return None

    async def close(self):
        pass


def test_valid_local_manifest_is_normalized_and_queryable(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path)
    original_path = Path(manifest["media"][0])

    job = service.enqueue(manifest)
    persisted = service.status(job["job_id"])

    assert persisted == job
    assert persisted["status"] == "QUEUED"
    assert persisted["manifest"]["expected_account_id"] == "douyin:creator"
    assert persisted["manifest"]["media"] == [{
        "path": str(original_path.resolve()),
        "kind": "video",
        "sha256": hashlib.sha256(VIDEO_BYTES).hexdigest(),
        "version": {
            "size_bytes": len(VIDEO_BYTES),
            "modified_ns": original_path.stat().st_mtime_ns,
        },
    }]


def test_manifest_cannot_supply_a_session_path(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path, session_path=str(tmp_path / "foreign.json"))

    with pytest.raises(TaskError) as caught:
        service.enqueue(manifest)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "session_path"
    assert service.list_jobs() == []


def test_platform_publication_scheduling_remains_unsupported(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path, publish_at="2030-01-01T12:00:00Z")

    with pytest.raises(TaskError) as caught:
        service.enqueue(manifest)

    assert caught.value.code == "UNSUPPORTED"
    assert caught.value.field == "publish_at"
    assert service.list_jobs() == []


def test_replaced_media_fails_after_claim_without_platform_submission(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path)
    job = service.enqueue(manifest)
    Path(manifest["media"][0]).write_bytes(VIDEO_BYTES + b"replaced")
    assert service.enqueue(manifest) == job
    target = RecordingTarget()

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["field"] == "media[0]"
    assert target.prepared == target.submitted == 0
    assert service.status(job["job_id"]) == result
    assert len(service.attempts(job["job_id"])) == 1


def test_declaration_cannot_contradict_a_separate_ai_fact(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path, declarations={"ai_usage": "generated"})

    with pytest.raises(TaskError) as caught:
        service.enqueue(manifest)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "declarations.ai_usage"
    assert service.list_jobs() == []


def test_explicit_originality_rights_survive_enqueue_without_changing_ai_fact(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(
        tmp_path, facts={"ownership": "original", "ai_usage": "generated", "repost_source": None},
        declarations={"originality_rights": True},
    )

    job = service.enqueue(manifest)

    assert job["manifest"]["facts"]["ai_usage"] == "generated"
    assert job["manifest"]["declarations"] == {"originality_rights": True}
    assert service.status(job["job_id"])["manifest"]["declarations"] == {"originality_rights": True}


def test_absent_rights_are_not_invented_at_enqueue(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))

    assert job["manifest"]["declarations"] == {}


def test_explicit_repost_authorization_keeps_source_fact_separate(tmp_path):
    service = make_service(tmp_path)
    facts = {"ownership": "repost", "ai_usage": "assisted",
             "repost_source": "https://example.test/original-work"}

    job = service.enqueue(make_manifest(tmp_path, facts=facts,
                                        declarations={"repost_authorized": True}))

    assert job["manifest"]["facts"] == facts
    assert job["manifest"]["declarations"] == {"repost_authorized": True}


@pytest.mark.parametrize("ownership,declaration", [
    ("original", {"repost_authorized": True}),
    ("repost", {"originality_rights": True}),
])
def test_rights_claim_must_fit_stated_ownership(tmp_path, ownership, declaration):
    service = make_service(tmp_path)
    facts = {"ownership": ownership, "ai_usage": "none",
             "repost_source": "https://example.test/source" if ownership == "repost" else None}

    with pytest.raises(TaskError) as caught:
        service.enqueue(make_manifest(tmp_path, facts=facts, declarations=declaration))

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == f"declarations.{next(iter(declaration))}"
    assert service.list_jobs() == []


def test_required_title_cannot_be_silently_omitted(tmp_path):
    service = make_service(tmp_path)
    policy = {"required": ["title", "body", "media"],
              "optional": ["tags", "cover", "collection"], "allow_omit": ["title"]}
    manifest = make_manifest(tmp_path, field_policy=policy)

    with pytest.raises(TaskError) as caught:
        service.enqueue(manifest)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "field_policy.allow_omit"
    assert service.list_jobs() == []


def test_oversized_title_is_rejected_without_truncation(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path, title="x" * 256)

    with pytest.raises(TaskError) as caught:
        service.enqueue(manifest)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "title"
    assert service.list_jobs() == []


def test_video_with_impossible_container_box_is_not_accepted(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path)
    Path(manifest["media"][0]).write_bytes(b"\x00\x00\xff\xff" + VIDEO_BYTES[4:])

    with pytest.raises(TaskError) as caught:
        service.enqueue(manifest)

    assert caught.value.code == "UNSUPPORTED"
    assert caught.value.field == "media[0]"
    assert service.list_jobs() == []


def test_local_cli_can_bind_an_account_without_manifest_session_paths(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    runtime = tmp_path / "runtime"
    command = [sys.executable, "-m", "sau_cli", "jobs", "--db", str(tmp_path / "jobs.sqlite3"),
               "--runtime-dir", str(runtime), "--media-root", str(tmp_path / "media"),
               "--json", "bind-account", "--platform", "douyin", "--account", "creator",
               "--expected-id", "stable-creator-id", "--session-source", str(source)]

    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["expected_id"] == "stable-creator-id"
    assert Path(result["session_path"]).is_relative_to(runtime / "sessions")
    assert Path(result["session_path"]).read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_replaced_cover_is_fenced_before_submission(tmp_path):
    service = make_service(tmp_path)
    cover = tmp_path / "media" / "cover.png"
    cover.write_bytes(IMAGE_BYTES)
    job = service.enqueue(make_manifest(tmp_path, cover=str(cover)))
    cover.write_bytes(IMAGE_BYTES + b"replaced")
    target = RecordingTarget()

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["field"] == "cover"
    assert target.prepared == target.submitted == 0


def test_bind_account_rejects_incomplete_storage_state(tmp_path):
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    source = tmp_path / "bad-state.json"
    source.write_text('{"cookies":[{}],"origins":[]}', encoding="utf-8")

    with pytest.raises(TaskError) as caught:
        service.bind_account("douyin", "creator", "stable-id", source)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "session_source"
    assert service.accounts() == []


def test_malformed_ownership_fact_reports_its_field(tmp_path):
    service = make_service(tmp_path)
    facts = {"ownership": ["original"], "ai_usage": "none", "repost_source": None}

    with pytest.raises(TaskError) as caught:
        service.enqueue(make_manifest(tmp_path, facts=facts))

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "facts.ownership"


def test_same_size_and_timestamp_media_replacement_is_detected_by_digest(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path)
    job = service.enqueue(manifest)
    path = Path(manifest["media"][0])
    previous = path.stat()
    path.write_bytes(VIDEO_BYTES[:-1] + b"X")
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    target = RecordingTarget()

    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["field"] == "media[0]"
    assert target.submitted == 0


def test_media_changed_during_preparation_is_fenced_before_submit(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path)
    job = service.enqueue(manifest)

    class ReplaceDuringPreparation(RecordingTarget):
        async def prepare(self, context):
            self.prepared += 1
            Path(manifest["media"][0]).write_bytes(VIDEO_BYTES + b"late change")
            return readback_metadata(context.manifest)

    target = ReplaceDuringPreparation()
    result = asyncio.run(service.run_once(adapters={"douyin": target}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "FAILED_PRE_SUBMIT"
    assert result["error"]["field"] == "media[0]"
    assert target.prepared == 1
    assert target.submitted == 0


def test_adapter_cannot_mutate_nested_normalized_manifest(tmp_path):
    service = make_service(tmp_path)
    job = service.enqueue(make_manifest(tmp_path))

    class MutatingTarget:
        async def prepare(self, context):
            blocked = []
            for mutation in (
                lambda: context.manifest["facts"].__setitem__("ai_usage", "generated"),
                lambda: context.manifest["media"][0].__setitem__("sha256", "forged"),
                lambda: context.manifest["tags"].append("forged"),
            ):
                try:
                    mutation()
                except (AttributeError, TypeError):
                    blocked.append(True)
            return {"all_mutations_blocked": len(blocked) == 3}

        async def close(self):
            pass

    result = asyncio.run(service.run_once(adapters={"douyin": MutatingTarget()}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "READY"
    assert result["actual_metadata"] == {"all_mutations_blocked": True}
    assert service.status(job["job_id"])["manifest"] == job["manifest"]


def test_account_binding_cannot_be_silently_changed(tmp_path):
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    source = tmp_path / "source.json"
    source.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    service.bind_account("douyin", "creator", "first-stable-id", source)

    with pytest.raises(TaskError) as caught:
        service.bind_account("douyin", "creator", "other-stable-id", source)

    assert caught.value.code == "CONFLICT"
    assert caught.value.field == "expected_id"
    assert service.accounts()[0]["expected_id"] == "first-stable-id"


def test_account_name_cannot_escape_managed_session_directory(tmp_path):
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    source = tmp_path / "source.json"
    source.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    with pytest.raises(TaskError) as caught:
        service.bind_account("douyin", "../creator", "stable-id", source)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "account"
    assert service.accounts() == []


def test_media_outside_trusted_root_cannot_be_enqueued(tmp_path):
    service = make_service(tmp_path)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(VIDEO_BYTES)

    with pytest.raises(TaskError) as caught:
        service.enqueue(make_manifest(tmp_path, media=[str(outside)]))

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "media[0]"
    assert service.list_jobs() == []


def test_douyin_note_uses_images_and_tencent_note_is_unsupported(tmp_path):
    service = make_service(tmp_path)
    douyin = service.enqueue(make_manifest(tmp_path, content_type="note"))
    assert douyin["manifest"]["media"][0]["kind"] == "image"

    with pytest.raises(TaskError) as caught:
        service.enqueue(make_manifest(tmp_path, platform="tencent", content_type="note"))

    assert caught.value.code == "UNSUPPORTED"
    assert caught.value.field == "content_type"
    assert len(service.list_jobs()) == 1


def test_non_string_manifest_key_is_a_field_error(tmp_path):
    service = make_service(tmp_path)
    manifest = make_manifest(tmp_path)
    untrusted_manifest: Any = manifest
    untrusted_manifest[1] = "unexpected"

    with pytest.raises(TaskError) as caught:
        service.enqueue(manifest)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.field == "manifest"


def test_bind_account_reports_non_string_platform_as_unsupported(tmp_path):
    service = TaskService(tmp_path / "jobs.sqlite3", runtime_dir=tmp_path / "runtime")
    source = tmp_path / "source.json"
    source.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    invalid_platform: Any = ["douyin"]
    with pytest.raises(TaskError) as caught:
        service.bind_account(invalid_platform, "creator", "stable-id", source)

    assert caught.value.code == "UNSUPPORTED"
    assert caught.value.field == "platform"
