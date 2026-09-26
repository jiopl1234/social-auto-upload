from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.errors import TaskError
from orchestrator.store import Store


class TaskService:
    """Public task interface shared by the CLI and local integrations."""

    def __init__(self, database: str | Path):
        self.store = Store(database)

    def enqueue(self, manifest: dict[str, Any]) -> dict:
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
        try:
            # Detach from caller-owned containers and reject non-JSON values.
            normalized = json.loads(json.dumps(manifest, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise TaskError("INVALID_INPUT", "Manifest contains a non-JSON value", field="manifest") from exc
        return self.store.enqueue(normalized)

    def status(self, job_id: str) -> dict:
        return self.store.get(job_id)

    def list_jobs(self) -> list[dict]:
        return self.store.list_jobs()

    def events(self, job_id: str) -> list[dict]:
        return self.store.events(job_id)

    def attempts(self, job_id: str) -> list[dict]:
        return self.store.attempts(job_id)
