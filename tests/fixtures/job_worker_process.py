"""Local subprocess target for task ownership acceptance tests.

The test parent exchanges small JSON files with this process. No platform
session, browser, or network connection is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator.errors import TaskError
from orchestrator.locking import process_identity
from orchestrator.service import TaskService


TIMEOUT = 15.0


def write_json(path: str | Path, value: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def wait_for_gate(path: str | None) -> None:
    if path is None:
        return
    deadline = time.monotonic() + TIMEOUT
    gate = Path(path)
    while not gate.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {gate.name}")
        time.sleep(0.02)


async def wait_for_gate_async(path: str | None) -> None:
    if path is None:
        return
    deadline = time.monotonic() + TIMEOUT
    gate = Path(path)
    while not gate.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {gate.name}")
        await asyncio.sleep(0.02)


async def wait_for_file_async(path: str) -> None:
    deadline = time.monotonic() + TIMEOUT
    marker = Path(path)
    while not marker.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {marker.name}")
        await asyncio.sleep(0.02)


class LocalTarget:
    def __init__(self, config: dict):
        self.config = config
        self.context = None
        self.resource: subprocess.Popen | None = None

    async def prepare(self, context):
        self.context = context
        context.assert_owner()
        if self.config.get("resource_entered"):
            resource_config = {
                "mode": "resource",
                "entered": self.config["resource_entered"],
                "gate": self.config["resource_gate"],
            }
            resource_config_path = Path(self.config["result"]).with_suffix(".resource.json")
            write_json(resource_config_path, resource_config)
            context.resources_started()
            self.resource = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), str(resource_config_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            context.register_process(self.resource.pid)
            await wait_for_file_async(self.config["resource_entered"])
        write_json(self.config["entered"], {
            "pid": os.getpid(),
            "identity": process_identity(os.getpid()),
            "job_id": context.job_id,
        })
        await wait_for_gate_async(self.config.get("gate"))
        context.assert_owner()
        return {"prepared_by": os.getpid()}

    async def close(self):
        if self.resource is not None:
            Path(self.config["resource_gate"]).touch()
            deadline = time.monotonic() + TIMEOUT
            while self.resource.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Resource did not exit")
                await asyncio.sleep(0.02)
            assert self.context is not None
            self.context.resources_closed()


def main(config: dict) -> None:
    mode = config["mode"]
    if mode == "resource":
        write_json(config["entered"], {
            "pid": os.getpid(),
            "identity": process_identity(os.getpid()),
        })
        wait_for_gate(config["gate"])
        return

    service = TaskService(config["db"], runtime_dir=Path(config["runtime"]))
    try:
        if mode == "maintenance":
            with service.maintenance(config["platform"], config["account"]):
                write_json(config["entered"], {
                    "pid": os.getpid(),
                    "identity": process_identity(os.getpid()),
                })
                wait_for_gate(config.get("gate"))
            result = {"kind": "ok"}
        elif mode == "run":
            job = asyncio.run(service.run_once(
                adapters={config["platform"]: LocalTarget(config)},
                job_id=config["job_id"],
            ))
            result = {"kind": "job", "job": job}
        else:
            raise ValueError(f"Unknown mode: {mode}")
    except TaskError as exc:
        result = {"kind": "task_error", "code": exc.code}
    write_json(config["result"], result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    main(json.loads(args.config.read_text(encoding="utf-8")))
