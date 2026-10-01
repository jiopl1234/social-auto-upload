"""Run account and media preflight in a disposable, watched Python process."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Callable, cast

from orchestrator.errors import TaskError
from orchestrator.locking import process_identity, process_state


_CHILD_ARGUMENT = "--preflight-child"
_SHUTDOWN_SECONDS = 0.6
_SAFE_CODES = frozenset({
    "INVALID_INPUT", "UNSUPPORTED", "STORAGE_ERROR", "ACCOUNT_BUSY", "LOCK_ERROR", "OWNERSHIP_LOST",
})


def _base_python() -> str:
    # Windows virtualenv executables can be launch shims; the base executable
    # runs the same installed Python without depending on that shim's lifetime.
    executable = Path(getattr(sys, "_base_executable", sys.executable))
    if not executable.is_file():
        raise TaskError("PREFLIGHT_FAILED", "Preflight Python is unavailable")
    return str(executable)


def _child_environment() -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run_request(request: dict[str, Any]) -> None:
    from orchestrator.accounts import get_binding
    from orchestrator.manifest import preflight_manifest
    from orchestrator.store import Store

    store = Store(Path(request["database"]))
    binding = get_binding(
        store, Path(request["runtime_dir"]), request["platform"], request["account"],
    )
    preflight_manifest(
        request["manifest"],
        tuple(Path(root) for root in request["media_roots"]),
        binding["expected_id"],
    )


def _watch_parent(pid: int, identity: str) -> None:
    """End this local worker child if its exact originating process disappears."""
    while True:
        try:
            state = process_state(pid, identity)
        except Exception:
            state = "UNKNOWN"
        if state != "LIVE":
            os._exit(3)
        time.sleep(0.1)


def child_main(operation: Callable[[dict[str, Any]], Any] = _run_request) -> int:
    try:
        request = json.loads(sys.stdin.buffer.read())
        pid = request["parent_pid"]
        identity = request["parent_identity"]
        if type(pid) is not int or pid <= 0 or not isinstance(identity, str) or not identity:
            return 2
        if process_state(pid, identity) != "LIVE":
            return 3
        threading.Thread(target=_watch_parent, args=(pid, identity), daemon=True).start()
        result = operation(request)
        response: dict[str, Any] = {"ok": True}
        if result is not None:
            response["result"] = result
    except TaskError as exc:
        response = {"ok": False, "code": exc.code if exc.code in _SAFE_CODES else "PREFLIGHT_FAILED",
                    "field": exc.field if isinstance(exc.field, str) else None}
    except (KeyError, TypeError, ValueError, OSError):
        response = {"ok": False, "code": "PREFLIGHT_FAILED", "field": None}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False, allow_nan=False,
                                       separators=(",", ":")).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def _read_child(
    process: subprocess.Popen[bytes], payload: bytes,
    loop: asyncio.AbstractEventLoop, result: asyncio.Future[tuple[int, bytes | None]],
) -> None:
    try:
        output, _ = process.communicate(payload)
        value = (process.returncode, output)
    except (OSError, ValueError):
        value = (-1, None)
    try:
        loop.call_soon_threadsafe(_set_result, result, value)
    except RuntimeError:
        # The event loop has already closed; the daemon reader can now exit.
        pass


def _set_result(result: asyncio.Future[tuple[int, bytes | None]], value: tuple[int, bytes | None]) -> None:
    if not result.done():
        result.set_result(value)


async def _wait_exit(process: subprocess.Popen[bytes], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while process.poll() is None:
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.02)
    return True


async def _stop_child(process: subprocess.Popen[bytes], reader: asyncio.Future[tuple[int, bytes | None]]) -> bool:
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    if not await _wait_exit(process, _SHUTDOWN_SECONDS / 2):
        try:
            process.kill()
        except OSError:
            pass
        if not await _wait_exit(process, _SHUTDOWN_SECONDS / 2):
            return False
    try:
        await asyncio.wait_for(asyncio.shield(reader), timeout=_SHUTDOWN_SECONDS)
    except (asyncio.TimeoutError, OSError, ValueError):
        return False
    return True


async def verify_job(job: dict, database: Path, runtime_dir: Path,
                     media_roots: tuple[Path, ...]) -> None:
    """Verify binding and media without blocking the worker event loop."""
    manifest = job["manifest"]
    request = {
        "database": str(database), "runtime_dir": str(runtime_dir),
        "media_roots": [str(root) for root in media_roots],
        "platform": job["platform"], "account": job["account"],
        "manifest": {key: manifest.get(key) for key in ("expected_account_id", "media", "cover")},
    }
    await run_child_request(request)


async def run_child_request(request: dict[str, Any], *, module: str = "orchestrator.preflight_process") -> Any:
    """Run an internal operation in a watched child that is reaped on cancellation."""
    if module not in {"orchestrator.preflight_process", "orchestrator.diagnostic_process", "orchestrator.session_process",
                      "orchestrator.file_parts_process"}:
        raise TaskError("UNSUPPORTED", "Child operation is not supported")
    parent_pid = os.getpid()
    parent_identity = process_identity(parent_pid)
    if parent_identity is None:
        raise TaskError("PREFLIGHT_FAILED", "Preflight parent identity is unavailable")
    request = {**request, "parent_pid": parent_pid, "parent_identity": parent_identity}
    try:
        payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TaskError("PREFLIGHT_FAILED", "Preflight request is invalid") from exc

    options: dict[str, Any] = {
        "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
        "cwd": str(Path(__file__).resolve().parent.parent), "env": _child_environment(),
        "shell": False,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        process = cast("subprocess.Popen[bytes]", subprocess.Popen(
            [_base_python(), "-m", module, _CHILD_ARGUMENT], **options,
        ))
    except OSError as exc:
        raise TaskError("PREFLIGHT_FAILED", "Preflight process could not start") from exc

    loop = asyncio.get_running_loop()
    reader: asyncio.Future[tuple[int, bytes | None]] = loop.create_future()
    try:
        threading.Thread(target=_read_child, args=(process, payload, loop, reader), daemon=True).start()
    except Exception as exc:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if not await _wait_exit(process, _SHUTDOWN_SECONDS):
            try:
                process.kill()
            except OSError:
                pass
        if not await _wait_exit(process, _SHUTDOWN_SECONDS):
            raise TaskError("RESOURCES_UNCONFIRMED", "Preflight process shutdown could not be confirmed") from exc
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        raise TaskError("PREFLIGHT_FAILED", "Preflight reader could not start") from exc

    try:
        returncode, output = await asyncio.shield(reader)
    except BaseException:
        if not await _stop_child(process, reader):
            raise TaskError("RESOURCES_UNCONFIRMED", "Preflight process shutdown could not be confirmed")
        raise
    # A read returns one validated storage-state snapshot (source is capped at
    # 5 MB). Other child operations only return compact status envelopes.
    response_limit = (10_000_000 if module == "orchestrator.session_process"
                      and request.get("action") == "read" else 4096)
    if returncode != 0 or output is None or len(output) > response_limit:
        raise TaskError("PREFLIGHT_FAILED", "Preflight process failed")
    try:
        response = json.loads(output)
    except (UnicodeError, ValueError) as exc:
        raise TaskError("PREFLIGHT_FAILED", "Preflight process returned an invalid result") from exc
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise TaskError("PREFLIGHT_FAILED", "Preflight process returned an invalid result")
    if response["ok"]:
        return response.get("result")
    code = response.get("code")
    field = response.get("field")
    raise TaskError(code if code in _SAFE_CODES else "PREFLIGHT_FAILED", "Preflight verification failed",
                    field=field if isinstance(field, str) else None)


if __name__ == "__main__":
    sys.exit(child_main() if sys.argv[1:] == [_CHILD_ARGUMENT] else 2)
