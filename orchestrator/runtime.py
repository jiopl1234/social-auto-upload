"""Monotonic execution budgets shared by the coordinator and page adapters."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Awaitable, Callable, Mapping, TypeVar

from orchestrator.errors import TaskError


T = TypeVar("T")
DEFAULT_LIMITS = {
    "total": 900.0, "preflight": 60.0, "prepare": 720.0, "navigation": 60.0,
    "upload": 300.0, "processing": 300.0, "metadata": 60.0,
    "submit": 30.0, "reconcile": 60.0, "session_save": 10.0, "cleanup": 10.0,
}


def validate_limits(overrides: Mapping[str, float] | None) -> dict[str, float]:
    limits = dict(DEFAULT_LIMITS)
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise TaskError("INVALID_INPUT", "Execution limits must be an object", field="limits")
        for stage, value in overrides.items():
            if stage not in limits or type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise TaskError("INVALID_INPUT", "Stage limits must be known, finite positive seconds", field="limits")
            limits[stage] = float(value)
    if limits["total"] <= limits["cleanup"]:
        raise TaskError("INVALID_INPUT", "Total time must exceed the reserved cleanup time", field="limits.total")
    return limits


class ExecutionBudget:
    def __init__(self, limits: Mapping[str, float]):
        self.limits = dict(limits)
        self.deadline = time.monotonic() + limits["total"]
        self.cleanup_deadline: float | None = None
        self.pending: set[asyncio.Task] = set()
        self.spent: dict[str, float] = {}
        self.active: dict[str, list[float]] = {}
        self.cancelling: set[asyncio.Task] = set()
        self.cleanup_diagnostics: list[dict] = []

    def time_left(self, stage: str) -> float:
        if stage not in self.limits or stage == "total":
            raise TaskError("INVALID_INPUT", "Unknown execution stage", field="stage")
        now = time.monotonic()
        if stage == "cleanup":
            if self.cleanup_deadline is None:
                self.cleanup_deadline = min(self.deadline, now + self.limits["cleanup"])
            return max(0.0, self.cleanup_deadline - now)
        if self.cleanup_deadline is not None:
            return 0.0
        used = self.spent.get(stage, 0.0) + sum(now - start for start in self.active.get(stage, ()))
        return max(0.0, min(self.limits[stage] - used, self.deadline - self.limits["cleanup"] - now))

    def _completed(self, task: asyncio.Task) -> None:
        self.pending.discard(task)
        if not task.cancelled():
            # Retrieve late failures without exposing driver exception strings.
            error = task.exception()
            if task in self.cancelling and isinstance(error, TaskError) and error.code in {
                "RESOURCES_UNCONFIRMED", "CLEANUP_FAILED",
            }:
                self.cleanup_diagnostics.append(error.as_dict())
        self.cancelling.discard(task)

    def track(self, task: asyncio.Task) -> None:
        """Keep a child operation under the existing cleanup/fencing check."""
        self.pending.add(task)
        task.add_done_callback(self._completed)

    async def _cancel(self, task: asyncio.Task) -> None:
        if task not in self.cancelling:
            self.cancelling.add(task)
            task.cancel()
        # Give the owned operation a bounded slice to reap its resources (for
        # example the read-only preflight child) before browser shutdown starts.
        grace = max(0.0, min(1.0, self.limits["cleanup"] / 2, self.deadline - time.monotonic()))
        if grace:
            await asyncio.wait({task}, timeout=grace)

    async def run(self, stage: str, operation: Callable[[], Awaitable[T]]) -> T:
        remaining = self.time_left(stage)
        if remaining <= 0:
            raise TaskError("TIMEOUT", "Execution stage exceeded its deadline", field=stage)
        task = asyncio.ensure_future(operation())
        self.track(task)
        started = time.monotonic()
        self.active.setdefault(stage, []).append(started)
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if not done:
                await self._cancel(task)
                raise TaskError("TIMEOUT", "Execution stage exceeded its deadline", field=stage)
            return task.result()
        except asyncio.CancelledError:
            await self._cancel(task)
            raise
        finally:
            self.active[stage].remove(started)
            self.spent[stage] = self.spent.get(stage, 0.0) + time.monotonic() - started

    async def settle(self) -> bool:
        # Cooperative tasks receive cancellation before cleanup is judged. Any
        # task still running afterwards keeps the account resource fence active.
        await asyncio.sleep(0)
        return all(task.done() for task in self.pending)
