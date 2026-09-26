"""A durable, local publishing target driven through the public task service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator.errors import TaskError
from orchestrator.locking import process_identity
from orchestrator.service import TaskService
from tests.task_fixtures import readback_metadata


TIMEOUT = 15.0


def write_json(path: str, value: dict) -> None:
    destination = Path(path)
    temporary = destination.with_name(f"{destination.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def append_jsonl(path: str, value: dict) -> None:
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_jsonl(path: str) -> list[dict]:
    destination = Path(path)
    if not destination.exists():
        return []
    return [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]


async def wait_for_gate(path: str) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not Path(path).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {Path(path).name}")
        await asyncio.sleep(0.02)


class LocalDeliveryTarget:
    def __init__(self, config: dict):
        self.config = config

    def actual_metadata(self, manifest) -> dict:
        metadata = readback_metadata(manifest)
        if self.config.get("omit_tags"):
            metadata["tags"] = []
        return metadata

    async def checkpoint(self, stage: str, context) -> None:
        marker = str(Path(self.config["stage"]).with_suffix(f".{stage}.json"))
        write_json(marker, {
            "stage": stage, "pid": os.getpid(),
            "identity": process_identity(os.getpid()), "attempt_id": context.attempt_id,
        })
        if self.config.get("gate_stage") == stage:
            await wait_for_gate(self.config["release"])

    async def prepare(self, context) -> dict:
        context.assert_owner()
        append_jsonl(self.config["calls"], {
            "stage": "prepare", "pid": os.getpid(), "attempt_id": context.attempt_id,
        })
        await self.checkpoint("prepare", context)
        context.assert_owner()
        return self.actual_metadata(context.manifest)

    async def submit(self, context) -> dict:
        context.assert_owner()
        append_jsonl(self.config["calls"], {
            "stage": "submit", "pid": os.getpid(), "attempt_id": context.attempt_id,
        })
        if self.config.get("gate_stage") == "before_submit":
            await self.checkpoint("before_submit", context)
        work = {
            "content_id": f"work-{context.attempt_id}",
            "platform": context.manifest["platform"],
            "account_id": context.manifest["expected_account_id"],
            "content_type": context.manifest["content_type"],
            "platform_result": "PUBLISHED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": self.actual_metadata(context.manifest),
            "media_sha256": [asset["sha256"] for asset in context.manifest["media"]],
            "source": "work_detail",
        }
        append_jsonl(self.config["deliveries"], work)
        if self.config.get("gate_stage") == "after_write":
            await self.checkpoint("after_write", context)
        return {"content_id": work["content_id"]}

    async def reconcile(self, context) -> list[dict]:
        context.assert_owner()
        append_jsonl(self.config["calls"], {
            "stage": "reconcile", "pid": os.getpid(), "attempt_id": context.attempt_id,
        })
        return read_jsonl(self.config["deliveries"])

    async def close(self) -> None:
        pass


def main(config: dict) -> None:
    pid = os.getpid()
    identity = process_identity(pid)
    write_json(config["hello"], {"pid": pid, "identity": identity})
    deadline = time.monotonic() + TIMEOUT
    while not Path(config["ack"]).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Parent did not acknowledge the worker process")
        time.sleep(0.02)
    service = TaskService(
        config["db"], runtime_dir=Path(config["runtime"]),
        media_roots=[Path(config["media_root"])],
    )
    try:
        job = asyncio.run(service.run_once(
            adapters={config["platform"]: LocalDeliveryTarget(config)},
            job_id=config["job_id"], max_attempts=config["max_attempts"],
        ))
        result = {"kind": "job", "job": job}
    except TaskError as exc:
        result = {"kind": "task_error", "code": exc.code}
    write_json(config["result"], result)


if __name__ == "__main__":
    config_path = Path(sys.argv[1])
    main(json.loads(config_path.read_text(encoding="utf-8")))
