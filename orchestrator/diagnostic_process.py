"""Private diagnostic writes after browser shutdown, isolated from the event loop."""
from pathlib import Path
import sys

from orchestrator.preflight_process import child_main
from orchestrator.store import Store


def write_records(request: dict) -> None:
    store = Store(Path(request["database"]))
    for record in request["records"][:9]:
        store.record_diagnostic_evidence(request["job_id"], request["owner_token"], record["stage"],
                                        record["details"], runtime_dir=Path(request["runtime_dir"]))


if __name__ == "__main__":
    sys.exit(child_main(write_records) if sys.argv[1:] == ["--preflight-child"] else 2)
