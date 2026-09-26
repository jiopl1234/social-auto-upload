"""Public task diagnostics and local session safety, using synthetic secrets."""

from __future__ import annotations

import asyncio
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from orchestrator.accounts import managed_session_path
from orchestrator.errors import TaskError
from tests.task_fixtures import make_manifest, make_service


def _assert_private_windows_acl(path: Path) -> None:
    env = os.environ.copy()
    env["SAU_PRIVATE_TARGET"] = str(path)
    kind = "DirectoryInfo" if path.is_dir() else "FileInfo"
    script = (f"[System.IO.{kind}]::new($env:SAU_PRIVATE_TARGET)"
              ".GetAccessControl().GetSecurityDescriptorSddlForm('Access')")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, env=env, check=True)
    acl = result.stdout.strip()
    assert acl.startswith("D:P")
    assert acl.count("(A;") == 1


class SecretFailure:
    cleanup_warnings = [{
        "code": "UI_CHANGED",
        "message": "challenge URL https://example.invalid/login?token=WARNING_SECRET",
        "field": "verification_code",
    }]

    async def prepare(self, _context):
        raise TaskError("AUTH_REQUIRED", "verification code 739124; cookie=ERROR_SECRET", field="verification_code")

    async def close(self):
        pass


def test_task_result_and_events_keep_error_category_without_auth_secrets(tmp_path):
    service = make_service(tmp_path)
    title = "Artwork about cookie=STORY_LITERAL, exactly as authored"
    job = service.enqueue(make_manifest(tmp_path, title=title))

    result = asyncio.run(service.run_once(adapters={"douyin": SecretFailure()}, job_id=job["job_id"]))

    assert result is not None
    assert result["status"] == "BLOCKED"
    assert result["error"]["code"] == "AUTH_REQUIRED"
    assert result["manifest"]["title"] == title
    assert service.status(job["job_id"])["manifest"]["title"] == title
    diagnostics = json.dumps({
        "error": result["error"],
        "warnings": result["warnings"],
        "events": service.events(job["job_id"]),
        "accounts": service.accounts(),
    })
    for secret in ("739124", "ERROR_SECRET", "WARNING_SECRET", "https://example.invalid/login"):
        assert secret not in diagnostics


def test_interrupted_account_session_replacement_retains_the_previous_state(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    target = managed_session_path(service.runtime_dir, "douyin", "creator")
    previous = target.read_bytes()
    source = tmp_path / "replacement.json"
    source.write_text(json.dumps({"cookies": [], "origins": [{
        "origin": "https://local.invalid", "localStorage": [{"name": "updated", "value": "yes"}],
    }]}), encoding="utf-8")
    storage = import_module("orchestrator.session_storage")
    replace = storage.os.replace

    def interrupted(source_path, destination_path):
        if destination_path == target:
            raise OSError("simulated replacement interruption")
        return replace(source_path, destination_path)

    monkeypatch.setattr(storage.os, "replace", interrupted)
    with pytest.raises(TaskError) as caught:
        service.bind_account("douyin", "creator", "douyin:creator", source)

    assert caught.value.code == "STORAGE_ERROR"
    assert target.read_bytes() == previous
    assert service.accounts()[0]["status"] == "READY"
    assert list(target.parent.glob("*.tmp")) == []


class RuntimeEvidence:
    def __init__(self, runtime_dir):
        self.runtime_dir = runtime_dir

    async def prepare(self, context):
        context.store.record_diagnostic_evidence(
            context.job_id, context.lease.token, "runtime", {
                "driver": "patchright", "driver_version": "1.58.2",
                "browser_version": "Chromium-local-127", "evidence_scope": "offline",
                "token_url": "https://example.invalid/login?token=ARTIFACT_SECRET",
            }, runtime_dir=self.runtime_dir,
        )
        return {"title": context.manifest["title"], "body": context.manifest["body"]}

    async def close(self):
        pass


def test_consecutive_tasks_get_private_attempt_scoped_evidence_without_secrets(tmp_path):
    service = make_service(tmp_path)
    references = []
    for key in ("one", "two"):
        job = service.enqueue(make_manifest(tmp_path, key=key))
        result = asyncio.run(service.run_once(
            adapters={"douyin": RuntimeEvidence(service.runtime_dir)}, job_id=job["job_id"],
        ))
        assert result is not None and result["status"] == "READY"
        events = [item for item in service.events(job["job_id"]) if item["stage"] == "runtime"]
        assert len(events) == 1
        event = events[0]
        assert event["attempt_id"] == result["attempt_id"]
        assert event["details"]["browser_version"] == "Chromium-local-127"
        reference = event["details"]["evidence_ref"]
        assert reference in result["evidence"]
        path = service.runtime_dir / reference["path"]
        assert path.is_file()
        contents = path.read_text(encoding="utf-8")
        assert result["job_id"] in contents and result["attempt_id"] in contents
        assert "ARTIFACT_SECRET" not in contents
        assert "ARTIFACT_SECRET" not in json.dumps(events)
        if os.name == "nt":
            _assert_private_windows_acl(path.parent)
            _assert_private_windows_acl(path)
        references.append(reference["path"])
    assert references[0] != references[1]


def test_normal_logger_uses_stderr_without_exception_or_auth_data():
    script = (
        "from loguru import logger\n"
        "import utils.log\n"
        "one_time_code = '650312'\n"
        "try:\n"
        "    raise RuntimeError('cookie=LOG_SECRET token URL https://example.invalid/?token=LOG_SECRET')\n"
        "except RuntimeError:\n"
        "    logger.exception('login QR and verification code ' + one_time_code)\n"
    )
    project_python = Path(__file__).parents[1] / ".venv" / "Scripts" / "python.exe"
    interpreter = project_python if project_python.is_file() else Path(sys.executable)
    completed = subprocess.run([str(interpreter), "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert "ERROR" in completed.stderr
    for secret in ("650312", "LOG_SECRET", "https://example.invalid"):
        assert secret not in completed.stderr


def test_evidence_retention_removes_only_expired_managed_artifacts(tmp_path):
    service = make_service(tmp_path)
    session = managed_session_path(service.runtime_dir, "douyin", "creator")
    original_session = session.read_bytes()
    first = service.enqueue(make_manifest(tmp_path, key="old-evidence"))
    asyncio.run(service.run_once(adapters={"douyin": RuntimeEvidence(service.runtime_dir)}, job_id=first["job_id"]))
    reference = next(item["details"]["evidence_ref"] for item in service.events(first["job_id"])
                     if item["stage"] == "runtime")
    expired = service.runtime_dir / reference["path"]
    old_time = time.time() - 31 * 86400
    os.utime(expired, (old_time, old_time))
    expired_temp = expired.with_name(f"{expired.name}.{uuid4().hex}.tmp")
    expired_temp.write_bytes(b"interrupted managed write")
    os.utime(expired_temp, (old_time, old_time))
    fresh_temp = expired.with_name(f"{expired.name}.{uuid4().hex}.tmp")
    fresh_temp.write_bytes(b"recent managed write")
    unmanaged_temp = expired.with_name(f"{expired.name}.not-a-uuid.tmp")
    unmanaged_temp.write_bytes(b"unmanaged temporary file")
    os.utime(unmanaged_temp, (old_time, old_time))
    outside = tmp_path / "outside-evidence.txt"
    outside.write_text("keep linked file", encoding="utf-8")
    os.utime(outside, (old_time, old_time))
    linked_temp = expired.with_name(f"{uuid4().hex}.json.{uuid4().hex}.tmp")
    try:
        linked_temp.symlink_to(outside)
    except (NotImplementedError, OSError):
        linked_temp = None
    unmanaged = service.runtime_dir / "evidence" / "user-notes.txt"
    unmanaged.write_text("unmanaged content", encoding="utf-8")

    second = service.enqueue(make_manifest(tmp_path, key="new-evidence"))
    asyncio.run(service.run_once(adapters={"douyin": RuntimeEvidence(service.runtime_dir)}, job_id=second["job_id"]))
    new_reference = next(item["details"]["evidence_ref"] for item in service.events(second["job_id"])
                         if item["stage"] == "runtime")
    fresh_artifact = service.runtime_dir / new_reference["path"]
    removed = service.prune_evidence()

    assert removed == 2
    assert not expired.exists()
    assert not expired_temp.exists()
    assert fresh_artifact.is_file()
    assert fresh_temp.read_bytes() == b"recent managed write"
    assert unmanaged_temp.read_bytes() == b"unmanaged temporary file"
    if linked_temp is not None:
        assert linked_temp.is_symlink()
    assert outside.read_text(encoding="utf-8") == "keep linked file"
    assert unmanaged.read_text(encoding="utf-8") == "unmanaged content"
    assert session.read_bytes() == original_session


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL verification")
def test_managed_session_acl_is_private_even_after_a_broad_existing_grant(tmp_path):
    service = make_service(tmp_path)
    target = managed_session_path(service.runtime_dir, "douyin", "creator")
    subprocess.run(["icacls", str(target.parent), "/grant", "*S-1-1-0:(OI)(CI)(R)"],
                   check=True, capture_output=True, text=True)
    source = tmp_path / "state.json"
    source.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    service.bind_account("douyin", "creator", "douyin:creator", source)

    _assert_private_windows_acl(target.parent)
    _assert_private_windows_acl(target)


@pytest.mark.parametrize("targets, expected_field", [
    ([None], "targets[0]"),
    ([{"account": "creator"}], "targets[0].platform"),
    ([{"platform": "douyin", "account": "creator"},
      {"platform": "douyin", "account": "creator"}], "targets[1]"),
    ([{"platform": "douyin", "account": "creator"},
      {"platform": "xiaohongshu", "account": "creator",
       "overrides": {"run_at": {"fold": "invalid"}}}], "targets[1].overrides.run_at.fold"),
    ([{"platform": "douyin", "account": "creator",
       "overrides": {"declarations": {"ai_usage": 42}}}],
     "targets[0].overrides.declarations.ai_usage"),
    ([{"platform": "douyin", "account": "creator",
       "overrides": {"field_policy": {"required": "title"}}}],
     "targets[0].overrides.field_policy.required"),
    ([{"platform": "douyin", "account": "creator",
       "overrides": {"delivery_key": "another"}}],
     "targets[0].overrides.delivery_key"),
])
def test_batch_input_errors_keep_safe_target_field_paths(tmp_path, targets, expected_field):
    service = make_service(tmp_path)
    payload = make_manifest(tmp_path)
    payload.pop("platform")
    payload.pop("account")
    payload["targets"] = targets

    with pytest.raises(TaskError) as caught:
        service.enqueue_batch(payload)

    public_error = caught.value.as_dict()
    assert public_error["code"] == "INVALID_INPUT"
    assert public_error["field"] == expected_field


@pytest.mark.parametrize("overrides", [
    {"otp_739124_COOKIE_SECRET": "private value"},
    {"run_at": {"otp_739124_COOKIE_SECRET": "private value"}},
])
def test_batch_input_error_drops_unknown_secret_field_name(tmp_path, overrides):
    service = make_service(tmp_path)
    payload = make_manifest(tmp_path)
    payload.pop("platform")
    payload.pop("account")
    payload["targets"] = [{
        "platform": "douyin", "account": "creator",
        "overrides": overrides,
    }]

    with pytest.raises(TaskError) as caught:
        service.enqueue_batch(payload)

    public_error = caught.value.as_dict()
    assert public_error["code"] == "INVALID_INPUT"
    assert public_error["field"] is None
    assert "739124" not in json.dumps(public_error)
    assert "COOKIE_SECRET" not in json.dumps(public_error)
