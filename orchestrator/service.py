from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.errors import TaskError
from orchestrator.store import Store


class TaskService:
    """Public task interface shared by the CLI and local integrations."""

    def __init__(self, database: str | Path, *, runtime_dir: Path | None = None,
                 media_roots: list[Path] | tuple[Path, ...] | None = None):
        from orchestrator.ownership import default_runtime_dir

        self.store = Store(database)
        self.runtime_dir = runtime_dir if runtime_dir is not None else default_runtime_dir()
        self.media_roots = tuple(Path(root).resolve() for root in (media_roots or ()))

    def enqueue(self, manifest: dict[str, Any]) -> dict:
        from orchestrator.accounts import get_binding
        from orchestrator.manifest import normalize_manifest

        if not isinstance(manifest, dict):
            raise TaskError("INVALID_INPUT", "Manifest must be a JSON object", field="manifest")
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise TaskError("UNSUPPORTED", "Only manifest schema_version 1 is supported", field="schema_version")
        for field in ("delivery_key", "platform", "account", "content_type"):
            value = manifest.get(field)
            if not isinstance(value, str) or not value.strip():
                raise TaskError("INVALID_INPUT", "A non-empty string is required", field=field)
        if manifest["platform"] not in {"douyin", "xiaohongshu", "tencent"}:
            raise TaskError("UNSUPPORTED", "Platform is not supported by the task interface", field="platform")
        if manifest["content_type"] not in {"video", "note"}:
            raise TaskError("UNSUPPORTED", "Content type is not supported", field="content_type")
        if manifest["platform"] == "tencent" and manifest["content_type"] != "video":
            raise TaskError("UNSUPPORTED", "Tencent tasks support video only", field="content_type")
        binding = get_binding(self.store, self.runtime_dir, manifest["platform"], manifest["account"])
        normalized = normalize_manifest(manifest, self.media_roots, binding["expected_id"])
        return self.store.enqueue(normalized)

    def bind_account(self, platform: str, account: str, expected_id: str, session_source: Path | str) -> dict:
        from orchestrator.accounts import bind_account

        return bind_account(self.store, self.runtime_dir, platform, account, expected_id, session_source)

    def status(self, job_id: str) -> dict:
        return self.store.get(job_id)

    def list_jobs(self) -> list[dict]:
        return self.store.list_jobs()

    def events(self, job_id: str) -> list[dict]:
        return self.store.events(job_id)

    def attempts(self, job_id: str) -> list[dict]:
        return self.store.attempts(job_id)

    def preflight(self, job: dict) -> Any:
        from orchestrator.accounts import get_binding
        from orchestrator.manifest import preflight_manifest

        manifest = job["manifest"]
        binding = get_binding(self.store, self.runtime_dir, job["platform"], job["account"])
        return preflight_manifest(manifest, self.media_roots, binding["expected_id"])

    async def run_once(self, *, adapters, job_id: str | None = None, max_attempts: int = 2) -> dict | None:
        from orchestrator.worker import run_once

        return await run_once(self.store, self.runtime_dir, adapters, job_id,
                              preflight=self.preflight, max_attempts=max_attempts)

    async def run_pending(self, *, adapters, max_attempts: int = 2) -> list[dict]:
        completed = []
        while True:
            result = await self.run_once(adapters=adapters, max_attempts=max_attempts)
            if result is None:
                return completed
            completed.append(result)

    def accounts(self) -> list[dict]:
        return self.store.accounts()

    def maintenance(self, platform: str, account: str):
        from orchestrator.ownership import AccountLease

        return AccountLease(self.runtime_dir, platform, account)
