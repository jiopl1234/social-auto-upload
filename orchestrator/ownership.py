from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from orchestrator.errors import TaskError
from orchestrator.locking import ProcessLock, process_identity, process_state


def default_runtime_dir() -> Path:
    return Path(os.environ.get("SAU_RUNTIME_DIR", str(Path.home() / ".sau" / "runtime")))


class AccountLease:
    """OS ownership plus a durable marker for unconfirmed browser shutdown."""

    def __init__(self, runtime_dir: Path, platform: str, account: str):
        # First release has one account per platform. Serializing the platform
        # also prevents two aliases or databases from racing the same account.
        key = hashlib.sha256(platform.encode("utf-8")).hexdigest()
        self.path = runtime_dir / "owners" / (key + ".json")
        self.lock = ProcessLock(runtime_dir / "locks" / (key + ".lock"))
        self.platform, self.account = platform, account
        self.token = uuid4().hex
        self.record: dict = {}
        self.held = False

    def _read(self) -> dict | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("active"), bool):
                raise ValueError("Invalid owner record")
            if value["active"]:
                if type(value.get("pid")) is not int or value["pid"] <= 0:
                    raise ValueError("Invalid process id")
                if any(not isinstance(value.get(key), str) or not value[key]
                       for key in ("process_identity", "token")):
                    raise ValueError("Invalid owner identity")
                if not isinstance(value.get("resources_started"), bool):
                    raise ValueError("Invalid resource status")
                processes = value.get("processes")
                if not isinstance(processes, list) or any(
                    not isinstance(item, dict) or type(item.get("pid")) is not int or item["pid"] <= 0
                    or not isinstance(item.get("identity"), str) or not item["identity"] for item in processes
                ):
                    raise ValueError("Invalid resource process identities")
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise TaskError("RESOURCES_UNCONFIRMED", "Cannot verify the previous account owner") from exc

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix("." + self.token + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(self.record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def __enter__(self):
        self.lock.acquire()
        try:
            previous = self._read()
            if previous and previous["active"]:
                state = process_state(previous.get("pid", -1), previous.get("process_identity"))
                if state != "DEAD":
                    raise TaskError("ACCOUNT_BUSY", "The previous account executor is still alive or cannot be verified")
                if previous.get("resources_started", True):
                    raise TaskError("RESOURCES_UNCONFIRMED", "The previous browser shutdown has not been confirmed")
            identity = process_identity(os.getpid())
            if identity is None:
                raise TaskError("OWNERSHIP_UNAVAILABLE", "Cannot establish the current process identity")
            self.record = {
                "active": True, "token": self.token, "platform": self.platform, "account": self.account,
                "pid": os.getpid(), "process_identity": identity, "created_at": time.time(),
                "resources_started": False, "processes": [],
            }
            self.held = True
            self._write()
            return self
        except BaseException:
            self.held = False
            self.lock.release()
            raise

    def assert_owner(self) -> None:
        current = self._read()
        if not self.held or not current or not current["active"] or current.get("token") != self.token:
            raise TaskError("OWNERSHIP_LOST", "Account ownership is no longer held")

    def resources_started(self) -> None:
        self.assert_owner()
        self.record["resources_started"] = True
        self._write()

    def register_process(self, pid: int) -> None:
        self.resources_started()
        identity = process_identity(pid)
        if identity is None:
            raise TaskError("RESOURCES_UNCONFIRMED", "Cannot identify the browser process")
        self.record["processes"].append({"pid": pid, "identity": identity})
        self._write()

    def resources_closed(self) -> None:
        self.assert_owner()
        if any(process_state(item["pid"], item["identity"]) != "DEAD" for item in self.record["processes"]):
            raise TaskError("RESOURCES_UNCONFIRMED", "A recorded browser process may still be running")
        # Called only after successful cleanup by the resource-owning adapter.
        self.record["resources_started"] = False
        self._write()

    def assert_resources_closed(self) -> None:
        if self.record.get("resources_started", True):
            raise TaskError("RESOURCES_UNCONFIRMED", "Browser shutdown has not been confirmed")

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.held and not self.record.get("resources_started", True):
                self.assert_owner()
                self.record["active"] = False
                self._write()
        finally:
            self.held = False
            self.lock.release()
