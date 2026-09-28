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
    actions.add_parser("resume", help="Resume an unsubmitted blocked job after rebinding its session").add_argument("job_id")
    actions.add_parser("list", help="List persisted tasks")
    actions.add_parser("accounts", help="Query account status and pause reasons")
    actions.add_parser("capabilities", help="Query per-path offline and production verification status")
    inspect = actions.add_parser("inspect-work", help="Read one existing Douyin video without publishing")
    inspect.add_argument("--platform", dest="account_platform", choices=["douyin"], required=True)
    inspect.add_argument("--account", required=True)
    inspect.add_argument("--content-id", required=True)
    inspect.add_argument("--headed", action="store_true")
    actions.add_parser("prune-evidence", help="Remove managed diagnostic artifacts older than 30 days")
    bind = actions.add_parser("bind-account", help="Bind one local account to a managed session")
    bind.add_argument("--platform", dest="account_platform", required=True)
    bind.add_argument("--account", required=True)
    bind.add_argument("--expected-id", required=True)
    bind.add_argument("--session-source", type=Path, required=True)
    run = actions.add_parser("run", help="Execute queued tasks when an adapter is available")
    run.add_argument("--job", dest="job_id")
    run.add_argument("--headed", action="store_true", help="Show the task browser")
    run.add_argument("--prepare-only", action="store_true", help="Check and prepare one job without publishing")
    run.add_argument("--max-attempts", type=int, choices=range(1, 6), default=2)
    run.add_argument("--verification-wait", type=int, default=0, metavar="SECONDS",
                     help="Keep one headed Douyin submission open for human verification (1-600 seconds)")


def emit_error(error: TaskError) -> int:
    print(json.dumps({"schema_version": 1, "status": "ERROR", "error": error.as_dict()}, ensure_ascii=False))
    return {"INVALID_INPUT": 2, "NOT_FOUND": 3, "CONFLICT": 4, "UNSUPPORTED": 5}.get(error.code, 1)


async def dispatch(args: argparse.Namespace) -> int:
    from orchestrator.service import TaskService
    from orchestrator.diagnostics import sanitize_public_result

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
        elif args.action == "resume":
            result = service.resume(args.job_id)
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
        elif args.action == "inspect-work":
            result = await service.inspect_work(args.account_platform, args.account, args.content_id, headed=args.headed)
        elif args.action == "prune-evidence":
            result = {"removed": service.prune_evidence(), "retention_days": 30}
        elif args.action == "bind-account":
            result = service.bind_account(args.account_platform, args.account, args.expected_id, args.session_source)
        elif args.action == "run":
            from orchestrator.platforms import default_adapters
            from orchestrator.runtime import DEFAULT_LIMITS

            wait = args.verification_wait
            if not 0 <= wait <= 600 or (wait and (not args.headed or not args.job_id or args.prepare_only)):
                raise TaskError("INVALID_INPUT", "Verification wait requires one headed delivery and 1-600 seconds",
                                field="verification_wait")
            if args.prepare_only and not args.job_id:
                raise TaskError("INVALID_INPUT", "Preparation requires one explicit job", field="action")
            selected = service.status(args.job_id) if args.job_id else None
            if wait and selected is not None:
                if selected["submission"] != "NOT_SUBMITTED":
                    raise TaskError("INVALID_STATE", "An existing submission can only be recovered read-only",
                                    field="verification_wait")
                if selected["platform"] != "douyin" or selected["manifest"]["content_type"] != "video":
                    raise TaskError("UNSUPPORTED", "Verification wait is only available for Douyin video", field="platform")
            if args.prepare_only and selected is not None and selected["submission"] != "NOT_SUBMITTED":
                raise TaskError("INVALID_STATE", "Preparation cannot resume a submitted task")
            if selected is not None:
                # Let the worker decide whether this call needs an adapter;
                # finished, non-retryable and future jobs are read-only.
                try:
                    idle = await service.run_once(adapters={}, job_id=args.job_id, max_attempts=args.max_attempts)
                except TaskError as exc:
                    if exc.code != "UNSUPPORTED":
                        raise
                else:
                    print(json.dumps(sanitize_public_result(idle), ensure_ascii=False, allow_nan=False))
                    return 0
            paths = service.capabilities()
            if selected is not None:
                paths = [item for item in paths if item["platform"] == selected["platform"]
                         and item["content_type"] == selected["manifest"]["content_type"]]
            enabled = [item for item in paths if item.get("can_execute", item["production_verified"])]
            if not enabled:
                raise TaskError("UNSUPPORTED", "Live account and work evidence is not verified; inspect jobs capabilities")
            def verification_notice(state: str) -> None:
                messages = {
                    "waiting": f"抖音要求本人验证，原浏览器窗口最多保留 {wait} 秒。请按页面提示完成验证，不要再次点击发表。",
                    "verified": "已收到同次提交的回执并核对身份，正在确认作品结果。",
                    "expired": "验证等待已到期，任务保持未知提交状态，不会自动重发。",
                }
                print(messages[state], file=sys.stderr, flush=True)

            all_adapters = default_adapters(service.runtime_dir, headed=args.headed, prepare_only=args.prepare_only,
                                            verification_wait=wait, on_verification=verification_notice if wait else None)
            adapters = {item["platform"]: all_adapters[item["platform"]] for item in enabled}
            limits = {stage: DEFAULT_LIMITS[stage] + wait for stage in ("submit", "reconcile")} if wait else None
            result = (await service.run_once(adapters=adapters, job_id=args.job_id, max_attempts=args.max_attempts,
                                            limits=limits) if args.job_id
                      else {"jobs": await service.run_pending(adapters=adapters, max_attempts=args.max_attempts)})
        else:
            raise TaskError("INVALID_INPUT", "Unknown task action", field="action")
        print(json.dumps(sanitize_public_result(result), ensure_ascii=False, allow_nan=False))
        return 0
    except TaskError as exc:
        return emit_error(exc)
    except Exception:
        print("Task operation failed; no success result was inferred.", file=sys.stderr)
        return emit_error(TaskError("INTERNAL_ERROR", "Task operation failed"))
