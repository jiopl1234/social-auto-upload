from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from orchestrator.errors import TaskError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=Path.home() / ".sau" / "tasks.sqlite3")
    parser.add_argument("--runtime-dir", type=Path, help="Shared account-lock directory for all local task databases")
    parser.add_argument("--media-root", action="append", type=Path, default=[],
                        help="Trusted local media directory; repeat for more than one root")
    parser.add_argument("--json", action="store_true", help="Emit one structured JSON result")
    actions = parser.add_subparsers(dest="action", required=True)
    enqueue = actions.add_parser("enqueue", help="Persist one delivery without publishing")
    enqueue.add_argument("--manifest", type=Path, required=True)
    enqueue_batch = actions.add_parser("enqueue-batch", help="Persist one delivery for multiple targets")
    enqueue_batch.add_argument("--manifest", type=Path, required=True)
    actions.add_parser("batch-status", help="Query every target in a batch").add_argument("batch_id")
    for action in ("status", "events", "attempts"):
        actions.add_parser(action).add_argument("job_id")
    actions.add_parser("list", help="List persisted tasks")
    actions.add_parser("accounts", help="Query account status and pause reasons")
    actions.add_parser("capabilities", help="Query per-path offline and production verification status")
    actions.add_parser("prune-evidence", help="Remove managed diagnostic artifacts older than 30 days")
    bind = actions.add_parser("bind-account", help="Bind one local account to a managed session")
    bind.add_argument("--platform", dest="account_platform", required=True)
    bind.add_argument("--account", required=True)
    bind.add_argument("--expected-id", required=True)
    bind.add_argument("--session-source", type=Path, required=True)
    run = actions.add_parser("run", help="Execute queued tasks when an adapter is available")
    run.add_argument("--job", dest="job_id")


def emit_error(error: TaskError) -> int:
    print(json.dumps({"schema_version": 1, "status": "ERROR", "error": error.as_dict()}, ensure_ascii=False))
    return {"INVALID_INPUT": 2, "NOT_FOUND": 3, "CONFLICT": 4, "UNSUPPORTED": 5}.get(error.code, 1)


async def dispatch(args: argparse.Namespace) -> int:
    from orchestrator.service import TaskService

    try:
        service = TaskService(args.db, runtime_dir=args.runtime_dir, media_roots=args.media_root)
        if args.action in {"enqueue", "enqueue-batch"}:
            try:
                manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise TaskError("INVALID_INPUT", "Cannot read a valid JSON manifest", field="manifest") from exc
            result = service.enqueue(manifest) if args.action == "enqueue" else service.enqueue_batch(manifest)
        elif args.action == "batch-status":
            result = service.batch_status(args.batch_id)
        elif args.action == "status":
            result = service.status(args.job_id)
        elif args.action == "events":
            result = {"job_id": args.job_id, "events": service.events(args.job_id)}
        elif args.action == "attempts":
            result = {"job_id": args.job_id, "attempts": service.attempts(args.job_id)}
        elif args.action == "list":
            result = {"jobs": service.list_jobs()}
        elif args.action == "accounts":
            result = {"accounts": service.accounts()}
        elif args.action == "capabilities":
            result = {"capabilities": service.capabilities()}
        elif args.action == "prune-evidence":
            result = {"removed": service.prune_evidence(), "retention_days": 30}
        elif args.action == "bind-account":
            result = service.bind_account(args.account_platform, args.account, args.expected_id, args.session_source)
        elif args.action == "run":
            from orchestrator.platforms import default_adapters

            selected = service.status(args.job_id) if args.job_id else None
            if selected is not None and selected["status"] not in {
                "QUEUED", "PREPARING", "READY", "FAILED_PRE_SUBMIT", "SUBMITTING", "UNKNOWN_COMMIT",
            }:
                # Querying a finished intent must not require a live adapter.
                print(json.dumps(selected, ensure_ascii=False, allow_nan=False))
                return 0
            paths = service.capabilities()
            if selected is not None:
                paths = [item for item in paths if item["platform"] == selected["platform"]
                         and item["content_type"] == selected["manifest"]["content_type"]]
            if not paths or not all(item["production_verified"] for item in paths):
                raise TaskError("UNSUPPORTED", "Live account and work evidence is not verified; inspect jobs capabilities")
            adapters = default_adapters(service.runtime_dir)
            result = (await service.run_once(adapters=adapters, job_id=args.job_id) if args.job_id
                      else {"jobs": await service.run_pending(adapters=adapters)})
        else:
            raise TaskError("INVALID_INPUT", "Unknown task action", field="action")
        from orchestrator.diagnostics import sanitize_public_result

        print(json.dumps(sanitize_public_result(result), ensure_ascii=False, allow_nan=False))
        return 0
    except TaskError as exc:
        return emit_error(exc)
    except Exception:
        print("Task operation failed; no success result was inferred.", file=sys.stderr)
        return emit_error(TaskError("INTERNAL_ERROR", "Task operation failed"))
