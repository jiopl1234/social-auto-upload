from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, cast

from orchestrator.delivery import match_work
from orchestrator.errors import TaskError
from orchestrator.manifest import freeze_manifest
from orchestrator.metadata import validate_prepared
from orchestrator.ownership import AccountLease
from orchestrator.store import Store


@dataclass
class ExecutionContext:
    job_id: str
    attempt_id: str
    manifest: Mapping[str, Any]
    store: Store
    lease: AccountLease

    def assert_owner(self) -> None:
        self.lease.assert_owner()
        self.store.assert_owner(self.job_id, self.lease.token)

    def resources_started(self) -> None:
        self.lease.resources_started()

    def register_process(self, pid: int) -> None:
        self.lease.register_process(pid)

    def resources_closed(self) -> None:
        self.lease.resources_closed()


class PreparationAdapter(Protocol):
    async def prepare(self, context: ExecutionContext) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class DeliveryAdapter(PreparationAdapter, Protocol):
    async def submit(self, context: ExecutionContext) -> dict | None: ...
    async def reconcile(self, context: ExecutionContext) -> list[dict]: ...


PAUSE_CODES = frozenset({"AUTH_REQUIRED", "IDENTITY_MISMATCH", "UI_CHANGED", "RESOURCES_UNCONFIRMED", "CLEANUP_FAILED"})


async def run_once(
    store: Store, runtime_dir: Path, adapters: Mapping[str, PreparationAdapter], job_id: str | None = None,
    *, preflight: Callable[[dict], Mapping[str, Any]] | None = None, max_attempts: int = 2,
) -> dict | None:
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise TaskError("INVALID_INPUT", "max_attempts must be an integer from 1 to 5", field="max_attempts")
    candidates = [store.get(job_id)] if job_id is not None else store.runnable_jobs(max_attempts)
    for candidate in candidates:
        recovering = candidate["status"] in {"SUBMITTING", "UNKNOWN_COMMIT"}
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
                context = ExecutionContext(job["job_id"], job["attempt_id"], freeze_manifest(job["manifest"]), store, lease)
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
                            context.manifest = preflight(job)
                        prepared = await adapter.prepare(context)
                        if not isinstance(prepared, dict):
                            raise TaskError("INVALID_RESULT", "Adapter preparation must return actual metadata")
                        metadata = json.loads(json.dumps(prepared, ensure_ascii=False, allow_nan=False))
                        context.assert_owner()
                    if full_delivery and not recovering:
                        metadata, field_warnings = validate_prepared(context.manifest, cast(dict, metadata))
                        warnings.extend(field_warnings)
                        job = store.mark_ready(job["job_id"], lease.token, cast(dict, metadata))
                        if preflight is not None:
                            preflight(job)
                        context.assert_owner()
                        job = store.begin_submission(job["job_id"], lease.token)
                        submitted = True
                        try:
                            receipt = await cast(DeliveryAdapter, adapter).submit(context)
                            if isinstance(receipt, dict) and isinstance(receipt.get("content_id"), str) and receipt["content_id"].strip():
                                store.record_receipt(job["job_id"], lease.token, receipt["content_id"])
                        except TaskError as exc:
                            error = exc
                        except Exception:
                            error = TaskError("SUBMISSION_UNCERTAIN", "The final action may have reached the platform")
                    context.assert_owner()
                    if submitted and (error is None or error.code not in PAUSE_CODES):
                        try:
                            evidence = await cast(DeliveryAdapter, adapter).reconcile(context)
                            if not isinstance(evidence, (list, tuple)):
                                raise TaskError("RECONCILIATION_UNAVAILABLE", "Read-only work evidence is unavailable")
                            work = match_work(job, store.current_attempt(job["job_id"]), evidence)
                        except TaskError as exc:
                            warnings.append(exc.as_dict())
                        except Exception:
                            warnings.append(TaskError("RECONCILIATION_UNAVAILABLE", "Read-only work verification did not complete").as_dict())
                except TaskError as exc:
                    error = exc
                except Exception:
                    error = TaskError("SUBMISSION_UNCERTAIN" if submitted else "PREPARATION_FAILED",
                                      "The final action may have reached the platform" if submitted else "Platform preparation failed")
                finally:
                    try:
                        await adapter.close()
                        lease.assert_resources_closed()
                    except Exception as exc:
                        cleanup = exc if isinstance(exc, TaskError) else TaskError("CLEANUP_FAILED", "Adapter cleanup failed")
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
                status = "BLOCKED" if paused else "FAILED_PRE_SUBMIT" if error else "READY"
                return store.finish_preparation(job["job_id"], lease.token, status=status,
                                                metadata=metadata, error=error.as_dict() if error else None,
                                                warnings=warnings, pause_reason=pause_reason)
        except TaskError as exc:
            if job_id is None and exc.code in {"ACCOUNT_BUSY", "RESOURCES_UNCONFIRMED"}:
                continue
            raise
    return None
