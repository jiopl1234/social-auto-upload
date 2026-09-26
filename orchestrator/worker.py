from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, TypeVar, cast

from orchestrator.delivery import match_work
from orchestrator.errors import TaskError
from orchestrator.manifest import freeze_manifest
from orchestrator.metadata import validate_prepared
from orchestrator.ownership import AccountLease
from orchestrator.runtime import ExecutionBudget, validate_limits
from orchestrator.scheduling import is_due
from orchestrator.store import Store

T = TypeVar("T")


@dataclass(frozen=True)
class ExecutionContext:
    job_id: str
    attempt_id: str
    manifest: Mapping[str, Any]
    store: Store
    lease: AccountLease
    budget: ExecutionBudget = field(default_factory=lambda: ExecutionBudget(validate_limits(None)))

    def assert_owner(self) -> None:
        self.lease.assert_owner()
        self.store.assert_owner(self.job_id, self.lease.token)

    def resources_started(self) -> None:
        self.lease.resources_started()

    def register_process(self, pid: int) -> None:
        self.lease.register_process(pid)

    def resources_closed(self) -> None:
        self.lease.resources_closed()

    async def run_stage(self, stage: str, operation: Callable[[], Awaitable[T]]) -> T:
        if stage != "cleanup":
            self.assert_owner()
        try:
            return await self.budget.run(stage, operation)
        except Exception as exc:
            # The pinned driver's private exception is not exported by its
            # public API. Match its exact type identity without logging text.
            if type(exc).__module__ == "patchright._impl._errors" and type(exc).__name__ == "TargetClosedError":
                raise TaskError("PAGE_CLOSED", "Browser page or context was closed", field=stage) from None
            raise

    def time_left(self, stage: str) -> float:
        return self.budget.time_left(stage)

    def record_runtime(self, details: Mapping[str, Any]) -> None:
        self.assert_owner()
        self.store.record_event(self.job_id, self.lease.token, "runtime",
                                {**details, "execution_limits": dict(self.budget.limits)})


class PreparationAdapter(Protocol):
    async def prepare(self, context: ExecutionContext) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class DeliveryAdapter(PreparationAdapter, Protocol):
    async def submit(self, context: ExecutionContext) -> dict | None: ...
    async def reconcile(self, context: ExecutionContext) -> list[dict]: ...


PAUSE_CODES = frozenset({"AUTH_REQUIRED", "IDENTITY_MISMATCH", "UI_CHANGED", "RESOURCES_UNCONFIRMED", "CLEANUP_FAILED"})


async def _prepare(adapter: PreparationAdapter, context: ExecutionContext) -> dict:
    prepared = await context.run_stage("prepare", lambda: adapter.prepare(context))
    if not isinstance(prepared, dict):
        raise TaskError("INVALID_RESULT", "Adapter preparation must return actual metadata")
    metadata = json.loads(json.dumps(prepared, ensure_ascii=False, allow_nan=False))
    context.assert_owner()
    return metadata


async def _submit(adapter: DeliveryAdapter, context: ExecutionContext) -> TaskError | None:
    try:
        receipt = await context.run_stage("submit", lambda: adapter.submit(context))
        if isinstance(receipt, dict) and isinstance(receipt.get("content_id"), str) and receipt["content_id"].strip():
            context.store.record_receipt(context.job_id, context.lease.token, receipt["content_id"])
    except TaskError as exc:
        return exc
    except Exception:
        return TaskError("SUBMISSION_UNCERTAIN", "The final action may have reached the platform")
    return None


async def _reconcile(adapter: DeliveryAdapter, context: ExecutionContext, job: dict, warnings: list[dict]) -> dict | None:
    try:
        evidence = await context.run_stage("reconcile", lambda: adapter.reconcile(context))
        if not isinstance(evidence, (list, tuple)):
            raise TaskError("RECONCILIATION_UNAVAILABLE", "Read-only work evidence is unavailable")
        return match_work(job, context.store.current_attempt(context.job_id), evidence)
    except TaskError as exc:
        warnings.append(exc.as_dict())
    except Exception:
        warnings.append(TaskError("RECONCILIATION_UNAVAILABLE", "Read-only work verification did not complete").as_dict())
    return None


async def _cleanup(adapter: PreparationAdapter, context: ExecutionContext, warnings: list[dict]) -> TaskError | None:
    failure = None
    cancelled = False
    closing = asyncio.create_task(context.run_stage("cleanup", adapter.close))
    while True:
        try:
            # User cancellation must not cancel the resource owner's shutdown.
            # The shared cleanup deadline still bounds the shielded operation.
            await asyncio.shield(closing)
            break
        except asyncio.CancelledError:
            cancelled = True
            if closing.cancelled():
                failure = TaskError("CLEANUP_FAILED", "Resource cleanup was interrupted", field="cleanup")
                break
        except Exception as exc:
            failure = (exc if isinstance(exc, TaskError) and exc.code != "TIMEOUT"
                       else TaskError("CLEANUP_FAILED", "Adapter cleanup did not complete", field="cleanup"))
            break
    for item in getattr(adapter, "cleanup_warnings", ()):
        if isinstance(item, dict) and item.get("code") in PAUSE_CODES and item not in warnings:
            warnings.append(item)
    if not await context.budget.settle():
        # An operation that suppressed cancellation must not release ownership
        # for a new publisher while it may still reach a browser.
        context.resources_started()
        failure = failure or TaskError("RESOURCES_UNCONFIRMED", "An adapter operation may still be running")
    try:
        context.lease.assert_resources_closed()
    except TaskError as exc:
        if failure is None:
            failure = exc
        elif exc.as_dict() not in warnings:
            warnings.append(exc.as_dict())
    if cancelled:
        if failure is not None:
            warnings.append(failure.as_dict())
        return TaskError("CANCELLED", "Task execution was cancelled")
    return failure


async def run_once(
    store: Store, runtime_dir: Path, adapters: Mapping[str, PreparationAdapter], job_id: str | None = None,
    *, preflight: Callable[[dict], Mapping[str, Any]] | None = None, max_attempts: int = 2,
    limits: Mapping[str, float] | None = None, wall_clock: Callable[[], datetime] | None = None,
) -> dict | None:
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise TaskError("INVALID_INPUT", "max_attempts must be an integer from 1 to 5", field="max_attempts")
    stage_limits = validate_limits(limits)
    clock = wall_clock or (lambda: datetime.now(timezone.utc))
    candidates = [store.get(job_id)] if job_id is not None else store.runnable_jobs(max_attempts)
    for candidate in candidates:
        recovering = candidate["status"] in {"SUBMITTING", "UNKNOWN_COMMIT"}
        if not recovering and not is_due(candidate["manifest"], clock()):
            if job_id is not None:
                return candidate
            continue
        if candidate["status"] not in {"QUEUED", "PREPARING", "READY", "FAILED_PRE_SUBMIT"} and not recovering:
            return candidate if job_id is not None else None
        if not recovering and not store.can_start(candidate["job_id"], max_attempts):
            if job_id is not None:
                return candidate
            continue
        adapter = adapters.get(candidate["platform"])
        if adapter is None:
            if job_id is not None:
                raise TaskError("UNSUPPORTED", "No task adapter is connected for this platform")
            continue
        submit = getattr(adapter, "submit", None)
        reconcile = getattr(adapter, "reconcile", None)
        full_delivery = callable(submit) and callable(reconcile)
        if callable(submit) != callable(reconcile):
            raise TaskError("UNSUPPORTED", "A delivery adapter must implement both submission and read-only reconciliation")
        if recovering and not full_delivery:
            raise TaskError("UNSUPPORTED", "Recovery requires a read-only work lookup")
        try:
            with AccountLease(runtime_dir, candidate["platform"], candidate["account"]) as lease:
                job = (store.claim_reconciliation(candidate["job_id"], lease.token) if recovering
                       else store.claim(candidate["job_id"], lease.token, max_attempts))
                if job is None:
                    if job_id is not None:
                        return store.get(job_id)
                    continue
                context = ExecutionContext(job["job_id"], job["attempt_id"], freeze_manifest(job["manifest"]),
                                           store, lease, ExecutionBudget(stage_limits))
                error, metadata, warnings = None, None, []
                if recovering:
                    # Content omissions remain true for this same remote work.
                    # Earlier transient/authentication diagnostics do not pause
                    # a successfully repaired session again.
                    warnings = [item for item in job["warnings"] if item.get("code") == "FIELD_OMITTED"]
                submitted, work = recovering, None
                try:
                    context.assert_owner()
                    if not recovering:
                        if preflight is not None:
                            preflight(job)
                        metadata = await _prepare(adapter, context)
                    if full_delivery and not recovering:
                        metadata, field_warnings = validate_prepared(job["manifest"], cast(dict, metadata))
                        warnings.extend(field_warnings)
                        job = store.mark_ready(job["job_id"], lease.token, cast(dict, metadata), warnings)
                        if preflight is not None:
                            preflight(job)
                        context.assert_owner()
                        if context.time_left("submit") <= 0:
                            raise TaskError("TIMEOUT", "Execution deadline expired before submission", field="submit")
                        job = store.begin_submission(job["job_id"], lease.token)
                        submitted = True
                        error = await _submit(cast(DeliveryAdapter, adapter), context)
                    context.assert_owner()
                    if submitted and (error is None or error.code not in PAUSE_CODES):
                        work = await _reconcile(cast(DeliveryAdapter, adapter), context, job, warnings)
                except asyncio.CancelledError:
                    error = TaskError("CANCELLED", "Task execution was cancelled")
                except TaskError as exc:
                    error = exc
                except Exception:
                    error = TaskError("SUBMISSION_UNCERTAIN" if submitted else "PREPARATION_FAILED",
                                      "The final action may have reached the platform" if submitted else "Platform preparation failed")
                finally:
                    cleanup = await _cleanup(adapter, context, warnings)
                    if cleanup is not None:
                        if error is None:
                            error = cleanup
                        else:
                            warnings.append(cleanup.as_dict())
                context.assert_owner()
                if error and error.code == "OWNERSHIP_LOST":
                    raise error
                pause_reason = next((item for item in ([error.as_dict()] if error else []) + warnings
                                     if item["code"] in PAUSE_CODES), None)
                if submitted:
                    if error is not None and work is not None:
                        warnings.append(error.as_dict())
                    return store.finish_delivery(job["job_id"], lease.token, work=work,
                                                 error=error.as_dict() if error else None, warnings=warnings, pause_reason=pause_reason)
                paused = pause_reason is not None
                if error is not None and error.code == "NOT_DUE" and not paused:
                    return store.defer_preparation(job["job_id"], lease.token, max_attempts=max_attempts)
                status = ("BLOCKED" if paused else "CANCELLED" if error and error.code == "CANCELLED"
                          else "FAILED_PRE_SUBMIT" if error else "READY")
                return store.finish_preparation(job["job_id"], lease.token, status=status,
                                                metadata=metadata, error=error.as_dict() if error else None,
                                                warnings=warnings, pause_reason=pause_reason)
        except TaskError as exc:
            if job_id is None and exc.code in {"ACCOUNT_BUSY", "RESOURCES_UNCONFIRMED"}:
                continue
            raise
    return None
