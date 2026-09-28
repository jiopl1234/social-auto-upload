# Platform delivery integration contract

Tickets 07/09/11 use `orchestrator.platforms.base.BrowserDeliveryAdapter`. Root owns this common implementation and CLI registration. Platform workers own the concrete page ports and platform-specific tests. Tickets 08/10 begin only after 07/09 close.

Each port has `platform`, `content_types: frozenset[str]`, `capability_version: str`, `verification_scope: str` (`unverified`, `local_fixture`, or the explicitly implemented `douyin_video_v1`) and async methods:

```python
async def navigate(self, page, content_type: str, *, readonly: bool) -> None: ...
async def identify_account(self, page) -> str: ...
async def check_challenge(self, page) -> None: ...
async def upload(self, page, manifest) -> None: ...
async def wait_media(self, page, manifest) -> None: ...
async def apply_fields(self, page, manifest) -> None: ...
async def readback(self, page) -> dict: ...
async def arm_submission(self, page) -> None: ...
async def submit_once(self, page) -> dict | None: ...
async def list_works(self, page, content_type: str) -> list[dict]: ...
```

The adapter constructor is `BrowserDeliveryAdapter(runtime_dir: Path, port_factory: Callable[[], PagePort], *, session_factory: Callable[[ExecutionContext], BrowserSession] | None = None, verification_wait: float = 0, on_verification: Callable[[str], None] | None = None)`. Each attempt creates its own port and session. A local fixture port must navigate only a file/loopback URL; the shared adapter also guards origin and blocks external network requests. Default unverified production ports return `UNSUPPORTED` before opening a browser. There is no CLI flag to bypass missing production evidence.

The concrete DouyinVideoPort alone may use the fixed Douyin creator origin. Its available scope is original/non-AI video, title/body, immediate public publication and source-frame covers. Other fields fail before browser creation. `can_execute` reports this implemented scope separately from full CLI live-publication acceptance (`production_verified`). The latter remains false until that exact end-to-end path is exercised.

Douyin upload evidence binds the selected source snapshot to CommitUploadInner Vid/VideoMeta and a matching processed preview, and stores the Vid/hash/attempt link before READY. Remote works expose Vid, not source SHA256. Their matching therefore requires the same attempt's saved receipt ID and uploaded Vid, stable author, independently observed title/body, known result and integer-second creation time. Do not copy a source SHA into a remote observation. Missing receipts or fields remain UNKNOWN_COMMIT. Source-frame default cover and empty optional settings are checked in the editor; remote media/cover hashes are not claimed.

Prepare navigates, checks challenge and stable identity, uploads, waits for positive per-file processing evidence, applies and reads fields, rechecks identity/challenge, saves state, then returns the actual seven-field readback. Each operation runs under the existing cumulative stage budget. The adapter's optional `verify_ready(context)` hook rechecks identity/challenge/readback against persisted ready metadata and arms the final control before worker persists SUBMITTING. Submit then invokes exactly one final action; ownership and SUBMITTING persistence remain in worker. Reconciliation navigates read-only and returns independent work observations; no copying job metadata to manufacture evidence. Recovery can open a new session without original media files.

Managed session loading and validation run in a watched, terminable child within the navigation budget. The browser receives that validated in-memory snapshot, not a filename that its driver would read again. Receipt waiting must continue to observe challenges after the final action; an existing editor is checked before reconciliation navigation can remove a prompt. Authentication errors after SUBMITTING pause the account while preserving uncertain delivery, rather than treating the prompt as proof of success or permission to resubmit.

An explicit `jobs run --job ... --headed --verification-wait 1..600` may keep the original Douyin video submission open for human verification. Default is off; prepare-only, batches, other platforms and already-submitted jobs cannot enable it through the CLI. The adapter pauses the account and persists a verification event immediately, then uses Douyin's `wait_for_receipt(page)` to observe the same submission without another final action. Repeated prompts share one timeout, subordinate to the existing cumulative stage/total budgets, cancellation, ownership fence and cleanup reserve. Never automate the challenge, dismiss it or refresh it. Receipt, refreshed stable identity and saved session precede the verified event. Only independently confirmed work and clean delivery finalization may clear this attempt's exact verification pause. All uncertain outcomes keep the account paused. Notices use stderr and fixed state strings; no challenge payload, QR content or session secrets enter diagnostics.

A port that starts a receipt observer exposes it as `receipt_task`. The adapter captures the port for the attempt and registers this child with `ExecutionBudget.track` as submission unwinds, even if browser cleanup clears the adapter's port field. Unfinished observers participate in the existing resource-fence check; their late exceptions are consumed without printing browser details.

Ports must preserve title/body, match exact topic candidates, apply declarations only when explicit, fail closed for missing required controls, and never remove unknown overlays. Actual readback contains `title`, `body`, `tags`, `media`, `cover`, `collection`, `declarations`. Media entries contain kind/sha256 only after a positive, traceable per-file upload observation. Work observations match `orchestrator/delivery.py` and must include observed stable account/type/id/status/time/metadata and media fingerprint when no receipt ID exists. Local DOM fixtures are synthetic contracts, not a claim that platform production DOM exposes these fields.

Keep real creator selectors from current legacy code as documented candidates. If a stable account or work evidence source is unknown, express that in capability metadata and leave live execution restricted; do not invent attributes and claim live support. Legacy unsafe publishing entry points must not remain a parallel bypass of the task protocol. Preserve parsing and positional constructor meanings; an explicit migration error is preferable to silently dropping facts or returning success.
