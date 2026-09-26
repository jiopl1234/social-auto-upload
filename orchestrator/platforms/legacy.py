"""Explicit migration boundary for publishing calls without a durable manifest."""
from orchestrator.errors import TaskError


def require_task_delivery() -> None:
    raise TaskError(
        "UNSUPPORTED",
        "Publishing requires tasks bind-account, enqueue and run with explicit content facts; live evidence remains restricted",
        field="manifest",
    )
