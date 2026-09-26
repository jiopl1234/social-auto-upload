"""Atomically replace private session state in a watched, cancellable process."""
import json
from pathlib import Path
import sys

from orchestrator.accounts import _storage_state_bytes
from orchestrator.preflight_process import child_main
from orchestrator.session_storage import atomic_private_write


def write_session(request: dict) -> None:
    payload = json.dumps(request["state"], ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > 5_000_000:
        raise ValueError("Storage state exceeds the managed limit")
    atomic_private_write(Path(request["path"]), payload, validate=_storage_state_bytes)


if __name__ == "__main__":
    sys.exit(child_main(write_session) if sys.argv[1:] == ["--preflight-child"] else 2)
