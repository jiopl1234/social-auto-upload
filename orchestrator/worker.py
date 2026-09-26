from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from orchestrator.errors import TaskError
from orchestrator.ownership import AccountLease
from orchestrator.store import Store


@dataclass
class ExecutionContext:
    job_id: str
    attempt_id: str
    manifest: dict
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


async def run_once(store: Store, runtime_dir: Path, adapters: Mapping[str, PreparationAdapter], job_id: str | None = None) -> dict | None:
    candidates = [store.get(job_id)] if job_id is not None else store.queued_jobs()
    for candidate in candidates:
        if candidate["status"] != "QUEUED":
            return candidate if job_id is not None else None
        adapter = adapters.get(candidate["platform"])
        if adapter is None:
            if job_id is not None:
                raise TaskError("UNSUPPORTED", "No task adapter is connected for this platform")
            continue
        try:
            with AccountLease(runtime_dir, candidate["platform"], candidate["account"]) as lease:
                job = store.claim(candidate["job_id"], lease.token)
                if job is None:
                    continue
                context = ExecutionContext(job["job_id"], job["attempt_id"], job["manifest"], store, lease)
                error, metadata, warnings = None, None, []
                try:
                    context.assert_owner()
                    metadata = await adapter.prepare(context)
                    context.assert_owner()
                except TaskError as exc:
                    error = exc
                except Exception:
                    error = TaskError("PREPARATION_FAILED", "Platform preparation failed")
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
                                     if item["code"] in {"AUTH_REQUIRED", "IDENTITY_MISMATCH", "UI_CHANGED", "RESOURCES_UNCONFIRMED", "CLEANUP_FAILED"}), None)
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
