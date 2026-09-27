from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

from orchestrator.errors import TaskError
from orchestrator.store import Store


class TaskService:
    """Public task interface shared by the CLI and local integrations."""

    def __init__(self, database: str | Path, *, runtime_dir: Path | None = None,
                 media_roots: list[Path] | tuple[Path, ...] | None = None,
                 clock: Callable[[], datetime] | None = None):
        from orchestrator.ownership import default_runtime_dir

        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self.store = Store(database, clock=self.clock)
        self.runtime_dir = runtime_dir if runtime_dir is not None else default_runtime_dir()
        self.media_roots = tuple(Path(root).resolve() for root in (media_roots or ()))

    def enqueue(self, manifest: dict[str, Any]) -> dict:
        self._validate_intake_identity(manifest)
        if any(not isinstance(key, str) for key in manifest):
            raise TaskError("INVALID_INPUT", "Manifest keys must be strings", field="manifest")
        try:
            request = json.loads(json.dumps(manifest, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise TaskError("INVALID_INPUT", "Manifest contains a non-JSON value", field="manifest") from exc
        existing = self.store.find_intake(request)
        if existing is not None:
            return existing
        try:
            normalized = self.normalize_input(request)
        except TaskError:
            # Another process may have committed this request while local
            # validation was in flight and its inputs were being removed.
            existing = self.store.find_intake(request)
            if existing is not None:
                return existing
            raise
        return self.store.enqueue(normalized, intake=request)

    def normalize_input(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Validate one intake request and return its durable normalized snapshot."""
        from orchestrator.accounts import get_binding
        from orchestrator.manifest import normalize_manifest

        self._validate_intake_identity(manifest)
        binding = get_binding(self.store, self.runtime_dir, manifest["platform"], manifest["account"])
        return normalize_manifest(manifest, self.media_roots, binding["expected_id"])

    @staticmethod
    def _validate_intake_identity(manifest: dict[str, Any]) -> None:
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

    def bind_account(self, platform: str, account: str, expected_id: str, session_source: Path | str) -> dict:
        from orchestrator.accounts import bind_account

        return bind_account(self.store, self.runtime_dir, platform, account, expected_id, session_source)

    def status(self, job_id: str) -> dict:
        return self.store.get(job_id)

    def resume(self, job_id: str) -> dict:
        job = self.store.get(job_id)
        with self.maintenance(job["platform"], job["account"]) as lease:
            lease.assert_owner()
            return self.store.resume_preparation(job_id)

    def list_jobs(self) -> list[dict]:
        return self.store.list_jobs()

    def events(self, job_id: str) -> list[dict]:
        return self.store.events(job_id)

    def attempts(self, job_id: str) -> list[dict]:
        return self.store.attempts(job_id)

    def preflight(self, job: dict) -> Any:
        from orchestrator.accounts import get_binding
        from orchestrator.manifest import preflight_manifest
        from orchestrator.scheduling import is_due

        manifest = job["manifest"]
        if not is_due(manifest, self.clock()):
            raise TaskError("NOT_DUE", "Scheduled local start time has not arrived", field="run_at")
        binding = get_binding(self.store, self.runtime_dir, job["platform"], job["account"])
        return preflight_manifest(manifest, self.media_roots, binding["expected_id"])

    async def preflight_async(self, job: dict) -> None:
        from orchestrator.preflight_process import verify_job
        from orchestrator.scheduling import is_due

        manifest = job["manifest"]
        if not is_due(manifest, self.clock()):
            raise TaskError("NOT_DUE", "Scheduled local start time has not arrived", field="run_at")
        await verify_job(job, self.store.path, self.runtime_dir, self.media_roots)
        if not is_due(manifest, self.clock()):
            raise TaskError("NOT_DUE", "Scheduled local start time has not arrived", field="run_at")

    async def run_once(self, *, adapters, job_id: str | None = None, max_attempts: int = 2,
                       limits: dict[str, float] | None = None) -> dict | None:
        from orchestrator.worker import run_once

        return await run_once(self.store, self.runtime_dir, adapters, job_id,
                              preflight=self.preflight_async, max_attempts=max_attempts,
                              limits=limits, wall_clock=self.clock)

    async def run_pending(self, *, adapters, max_attempts: int = 2,
                          limits: dict[str, float] | None = None) -> list[dict]:
        completed = []
        while True:
            # Deliver cancellation requested between results before run_once
            # can synchronously claim another task ahead of its first await.
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                return completed
            result = await self.run_once(adapters=adapters, max_attempts=max_attempts, limits=limits)
            if result is None:
                return completed
            completed.append(result)
            # run_once persists cancellation instead of propagating it. It can
            # appear as a warning when delivery or another error is already
            # known; either form stops this executor before another claim.
            diagnostics = [result.get("error") or {}, *result.get("warnings", [])]
            if any(item.get("code") == "CANCELLED" for item in diagnostics):
                return completed

    def accounts(self) -> list[dict]:
        return self.store.accounts()

    def prune_evidence(self, *, retention_days: int = 30) -> int:
        from orchestrator.diagnostics import prune_private_evidence

        return prune_private_evidence(self.runtime_dir, retention_days=retention_days)

    def capabilities(self) -> list[dict]:
        from orchestrator.platforms import capability_report

        return capability_report()

    async def inspect_work(self, platform: str, account: str, content_id: str, *, headed: bool = False) -> dict:
        from orchestrator.inspection import inspect_work

        return await inspect_work(self, platform, account, content_id, headed=headed)

    def maintenance(self, platform: str, account: str):
        from orchestrator.ownership import AccountLease

        return AccountLease(self.runtime_dir, platform, account)

    def enqueue_batch(self, payload: dict[str, Any]) -> dict:
        from orchestrator.fanout import enqueue_batch

        return enqueue_batch(self, payload)

    def batch_status(self, batch_id: str) -> dict:
        from orchestrator.fanout import batch_status

        return batch_status(self, batch_id)
