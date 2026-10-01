# 持久发布任务

任务接口使用独立的 SQLite 数据库保存交付意图和事件。已实现账号绑定、不可变清单预检、串行执行、提交边界和只读恢复，以及五条路径的本地页面适配。抖音、小红书、视频号原创非 AI 视频已接入有限真实发布范围，见 [抖音使用说明](douyin-video.md)、[小红书使用说明](xiaohongshu-video.md)和[视频号使用说明](channels-video.md)；图文真实路径仍受限。`QUEUED` 只表示已保存，不表示已发布。

## 命令

先按 [安装说明](install.md) 初始化配置并安装项目。旧的 `sau <platform>` 命令参数保留；新接口位于 `sau jobs`。为调度程序使用 `--json`；将任务数据库保存在固定位置，以便重启后查询。

```powershell
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json bind-account --platform douyin --account creator --expected-id YOUR_STABLE_ACCOUNT_ID --session-source C:/Users/Chinese/account-state.json
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --media-root C:/Users/Chinese/Videos --json enqueue --manifest content.json
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json status <job_id>
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json events <job_id>
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json attempts <job_id>
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json list
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json accounts
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json capabilities
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json prune-evidence
```

省略 `--db` 时使用当前用户的 `~/.sau/tasks.sqlite3`。命令选项 `--db`、`--media-root`、`--json` 放在动作名称前。媒体根目录由本地调用者明确选择，可重复指定；清单不能自己扩大这个范围。绑定操作复制有效 storage-state JSON 到隔离的受管会话路径，不执行登录，也不把配置的 ID 当作在线身份已核实。

`capabilities` 逐路径列出离线验证状态、证据来源、生产验证状态和缺失证据。三平台视频 `can_execute=true`、`available_limited` 仅开放各自报告所列范围。视频号通过 `4.mp4` 完成真实 CLI 首次发表、即时正向确认持久化和独立作品核对，报告 `production_verified=true`、`live_cli_submission_verified=true`、`missing_evidence=[]`；这不证明审核或分发结果，也不扩大已支持范围。抖音完整 CLI 交付仍待验收；小红书已真实发表并通过 CLI 只读恢复完成确认，修正后的正向回执即时入库仍待新视频线上验证，两者保留 `production_verified=false`。其余两条图文路径为 `restricted`，指定发布时在领取前返回 `UNSUPPORTED`，批量发布时跳过。小红书和视频号视频允许指定任务的 `run --prepare-only`。没有绕过证据门槛的环境变量或开关。已结束、不可重试或尚未到期的指定任务返回持久状态。

三平台旧视频及抖音/小红书旧图文发布调用现返回明确迁移错误，需改用绑定账号和包含事实/声明的任务清单。旧命令参数解析与位置参数绑定保持；缺失的发布事实不能静默推断后继续发表。

托管会话与诊断证据使用本机用户独占权限（Windows 为原生受保护 DACL），会话验证后原子替换。诊断按 job/attempt/UUID 独立保存，状态和事件记录相对引用；错误和运行版本使用白名单，正文与实际内容不改写。不会保存原始页面截图、Cookie、验证码或登录 QR。`prune-evidence` 显式清理超过 30 天的托管诊断文件；需由本机维护流程定期调用，未自动安装定时任务。清理不影响会话或 SQLite 交付记录，过期引用仍可留作审计。

## 清单与重复投递

```json
{
  "schema_version": 1,
  "delivery_key": "original-episode-001",
  "platform": "douyin",
  "account": "creator",
  "content_type": "video",
  "title": "原创作品",
  "body": "第一集正文",
  "tags": [],
  "media": ["C:/Users/Chinese/Videos/episode-001.mp4"],
  "facts": {"ownership": "original", "ai_usage": "none", "repost_source": null},
  "declarations": {},
  "field_policy": {
    "required": ["title", "body", "media"],
    "optional": ["tags", "cover", "collection"],
    "allow_omit": []
  }
}
```

`platform` 接受 `douyin`、`xiaohongshu`、`tencent`；视频号只接受 `video`。`account` 必须指向已登记的稳定账号 ID。素材和封面采用绝对路径，规范化时保存路径、类型、SHA-256 和文件版本；准备前及提交前再次核对。适配器拿到的清单为递归只读对象。超限或未知字段明确拒绝，不自动截断、补 AI 声明或选择转载。只有列入 `allow_omit` 的可选字段才允许后续适配器按明确记录降级。

本地校验只接受列出的保守格式与能力范围，不证明平台当前规则或文件转码一定通过。`publish_at` 首版不支持；`run_at` 指定本地执行的启动时刻，格式见下文。旧任务的查询数据继续保留，旧版未带素材摘要和事实的清单不能直接用新接口执行。

平台权益可通过 `declarations.originality_rights` 与 `declarations.repost_authorized` 的显式布尔值表达；省略时不添加默认值。申请原创权益要求内容归属为原创，声明转载获授权要求内容归属为转载；两者都不会推导或改写 AI 使用事实。具体控件是否支持及权益资格仍需平台适配器核查。

平台、账号、`delivery_key` 共同标识一次交付意图。首次入队在同一事务保存原始请求指纹和规范化素材快照；重复入队先比对持久化请求，再决定是否需要校验新素材。同一个键提交相同清单返回原任务；只改变 JSON 对象字段顺序不创建新任务。原素材、封面或会话已移走也不影响这一查询。同键内容不同返回 `CONFLICT`，不会覆盖原清单。原路径上的文件发生变化时，相同请求仍返回原任务；真正执行前仍会校验原快照并拒绝变更的素材。素材摘要用于内容一致性核查，不替代交付键。

## 查询与结果

结果包含任务及尝试标识、平台/账号、清单摘要、任务状态、提交状态、平台结果、作品 ID、核实时间、实际元数据、警告、错误和证据引用。尚未获得的结果为 `null`，并保留提交清单以供核对。刚入队的任务为 `QUEUED`、`NOT_SUBMITTED`；尝试列表为空。

任务协议与后续执行使用同一组领域语义：

| 字段 | 约定 |
|---|---|
| `job_id` | 一个平台、账号、交付键上的持久交付意图；重试不更换 |
| `attempt_id` | 某次执行的标识；尚未领取时为 null |
| `status` | QUEUED、PREPARING、READY、SUBMITTING、UNKNOWN_COMMIT、COMPLETED、FAILED_PRE_SUBMIT、BLOCKED、CANCELLED。COMPLETED 表示已核实作品结果，不等于公开发布 |
| `submission` | NOT_SUBMITTED、MAY_HAVE_SUBMITTED、CONFIRMED；回执 ID 用于定位，必须结合只读作品证据确认结果 |
| `platform_result` | ACCEPTED、IN_REVIEW、PUBLISHED、REJECTED、DRAFT；尚无证据时为 null，与任务是否执行结束分开 |
| `verified_at` | 最近一次作品证据的核实时间；入队时间不能替代此字段 |
| 事件 | 关联 job、可选 attempt、状态、阶段和时间；与相应状态更新处于同一事务 |
| 账号绑定 | 平台、逻辑账号引用、预期稳定账号 ID、独立会话路径和账号暂停状态；昵称不是稳定 ID |

平台拒绝不等于可重投的网络错误。最终动作前事务写入 SUBMITTING。进入此状态后如不能确认结果，后续执行保留 UNKNOWN_COMMIT 并只读核对，不能因为查询尚无作品就重发。作品匹配核对账号、类型、实际元数据、创建时间和可用素材摘要；没有回执 ID 时必须有匹配的素材摘要。多个候选、字段不足、普通 URL 变化和核对不可用均不能确认。当前保守时间窗从提交记录开始到一小时后，时间精度或平台时钟不兼容时保留不明，不猜测。

机器模式的 stdout 只输出一个 JSON 值；参数错误说明和运行诊断使用 stderr。`--json --help` 将帮助放在状态为 HELP 的 JSON 的 `help` 字段中，且不创建数据库。查询是对当前记录的读取，查询退出码 0 本身不代表任务已成功发布。

| 退出码 | 含义 |
|---|---|
| 0 | 入队或查询成功 |
| 1 | 存储或内部错误 |
| 2 | 输入或命令参数错误 |
| 3 | 任务不存在 |
| 4 | 交付键冲突 |
| 5 | 不支持或尚未接入的能力 |

当前 `run` 在待执行任务缺少适配器时返回明确的未接入错误，不调用旧上传流程。指定已结束的任务时返回其持久状态，不再次准备，也不宣称已发布。任务、尝试、事件和账号绑定使用真实数据库；创建任务与初始事件处于同一事务。升级发现较新数据库版本时拒绝操作，避免静默覆盖。

当前数据库为 schema v4，支持从 v1/v2/v3 在事务内升级并保留历史记录。v4 分开记录原始请求指纹与规范化清单摘要；旧记录没有原始请求时，只用已保存的规范路径、元数据和调度输入比对，不重新打开素材。历史符号链接别名不能从旧快照还原，不匹配时返回冲突，不猜测。旧版程序拒绝打开较新数据库，避免回退后忽略执行或幂等语义；对外 JSON 协议仍为 schema_version 1。

抖音真实视频的作品列表不提供源 SHA256，使用本次持久回执 ID、已保存的上传 Vid、账号、标题正文及整数秒时间共同匹配，不复制请求来伪造远端素材摘要；没有回执时保持不明。该路径的范围和只准备、只读检查、显式恢复命令见 [抖音使用说明](douyin-video.md)。

小红书真实视频在发表前保存源 SHA256/MD5 与上传临时 Key。作品详情的规范文件 ID 可以不同，使用独立返回的原始视频 MD5 核对源素材；有回执时同时匹配该 ID，没有回执时额外要求完整列表与唯一视频详情匹配。旧 attempt 缺 MD5 时只能在原 SHA256 验证成功后补充本地指纹，不补造回执、不新建 attempt、不再次发表。具体首屏/候选数量和字段边界见 [小红书使用说明](xiaohongshu-video.md)。

## 执行器与账号维护

取消 `run_pending()` 时，当前任务先完成有界清理并保存结果，执行器随后返回本次已处理的结果列表，不再领取下一项。取消也可能作为已确认结果或其他错误的附加警告保存，仍会停止该轮执行；之后显式调用可继续剩余排队任务。

时钟回拨使当前任务延期时，清理期间收到的取消保留在任务警告与该次调度事件中。尚可重试的任务仍为 QUEUED；达到尝试上限仍为 FAILED_PRE_SUBMIT / ATTEMPT_LIMIT，两者都会停止本轮执行器。新一轮尝试重新建立当前诊断，历史取消事件保留，不阻止显式恢复执行。

Python 公共接口 `TaskService(database, runtime_dir=..., media_roots=[...])` 提供 `run_once(adapters=..., job_id=..., max_attempts=2, limits=...)` 和串行 `run_pending(adapters=..., max_attempts=2, limits=...)`。完整适配器实现 `prepare(context)`、`submit(context)`、`reconcile(context)` 和 `close()`；准备返回实际元数据，提交最多调用一次，核对只读作品列表或详情。仅实现准备和清理的兼容适配器结束于 `READY`，该状态没有平台成功含义。

完整适配器的 `prepare` 必须报告实际读回的 `title`、`body`、`tags`、`media`、`cover`、`collection`、`declarations`。`media` 是有序的 `{kind, sha256}` 列表，摘要关联已确认上传的本地素材；`cover` 为同结构的对象或 null。编排层在 SUBMITTING 前将这些值与不可变清单比较。必需内容或声明不符会返回字段错误并阻止提交；只有明确允许遗漏的可选字段可为空，并保存 `FIELD_OMITTED` 警告。该契约要求适配器提供真实页面证据，不能通过复制请求伪造读回。素材摘要的客户端记录不代表平台返回了同一摘要。

`max_attempts` 限制同一任务的尝试总数，允许 1–5，默认 2。只有确定未越过提交边界的 `TRANSIENT_PRE_SUBMIT` 才可重试；认证、身份、声明、UI 变化和审核拒绝不进入重试循环。即使适配器在提交后错误地返回临时错误类别，持久边界也会禁止重发。重启后尚在 SUBMITTING 的任务自动进入只读恢复；UNKNOWN_COMMIT 不进入自动待发布队列，可用指定 `job_id` 的执行调用再次只读核对。核对不依赖原始本地素材仍然存在。

每次领取建立一个 attempt，并在同一事务内更新任务和事件。适配器每次写入前可调用 `context.assert_owner()`；最终提交也必须通过此所有权检查。创建浏览器前调用 `resources_started()`，成功回收后才能调用 `resources_closed()`；可用 `register_process(pid)` 登记子进程并在关闭时验证其退出。资源关闭不明会阻断账号。`AUTH_REQUIRED`、`IDENTITY_MISMATCH`、`UI_CHANGED` 等暂停原因可通过 `accounts` 查询；同账号后续任务保持排队，其他平台继续执行。

所有本机进程与数据库必须共用一个运行目录。默认是 `~/.sau/runtime`，可以统一设置 `SAU_RUNTIME_DIR`；测试或明确隔离的部署可使用 `--runtime-dir` / Python `runtime_dir`。首版每个平台一个账号，因此锁以平台为粒度，避免同一账号的不同别名或数据库绕过互斥。维护接口 `service.maintenance(platform, account)` 及三平台现有 CLI 登录、检查入口使用相同的操作系统锁。五条旧发布入口在获取锁或启动驱动之前明确返回迁移错误。异常退出的旧维护流程无法证明资源已关闭时仍保留阻断标记。

锁文件不会按时间删除或过期。旧进程存活、身份无法确认、浏览器关闭未确认时均拒绝接管；不要通过删除运行目录或切换运行目录解决账号繁忙。只有旧进程已退出且资源关闭能够确认，提交前的未完成尝试才可在上限内恢复；提交后的恢复复用原尝试，只读核对。离线测试证明本地防重复执行，不能证明平台远端 exactly-once。

## 截止、取消和浏览器

执行采用单调时钟，所有阶段共享总预算并预留清理时间。默认秒数：`total=900`、`preflight=60`、`prepare=720`、`navigation=60`、`upload=300`、`processing=300`、`metadata=60`、`submit=30`、`reconcile=60`、`session_save=10`、`cleanup=10`。同一阶段的多次操作累计扣减预算。`limits` 可覆盖这些键，值必须是有限正数且 `total > cleanup`；例如 `{ "total": 60, "upload": 30, "cleanup": 5 }`。

适配器通过 `context.run_stage(name, operation)` 执行可取消的异步操作。准备超时返回 `TIMEOUT` 和阶段名，提交后超时保留可能已提交的边界。取消发生在提交前且清理确认时返回 `CANCELLED / NOT_SUBMITTED`；提交后取消返回 `UNKNOWN_COMMIT / MAY_HAVE_SUBMITTED`，指定任务的后续执行只读核对。取消不会撤销远端作品。清理也有总截止，外部取消不会中断已经开始的有界清理；资源不能确认关闭时暂停账号并保留主要错误。

执行中的两次预检都在只读 Python 子进程中校验绑定、受管会话及媒体版本，避免磁盘读取阻塞浏览器事件循环。超时或取消后先终止并核实该子进程；父进程异常退出时，子进程也通过进程身份监测退出。规范化清单的同步入口校验仍保持原有公共接口。

`BrowserSession` 沿用固定版本 Patchright，创建资源后立即纳入关闭流程，独立尝试 page、context、browser 和 driver 的关闭。会话读取与验证在导航预算内由受监控、可终止的子进程完成，浏览器接收已验证的内存快照，不再次打开会话文件。会话保存同样使用受父进程监控的可终止子进程，先验证受保护临时文件再原子替换；敏感状态只经管道传递，失败保留旧文件。运行事件关联 driver/browser 版本、公开启动选项和实际执行预算，不记录会话内容或可执行文件路径。诊断在浏览器关闭后有界写入，剩余时间不足时只保留数据库结果和存储警告。适配器必须采用异步、可取消的驱动操作；任意阻塞 Python 扩展或自行忽略取消的第三方插件不属于已验证执行路径，无法证明停止时账号仍保持资源阻断。

## 本地定时启动

单平台清单或批量清单可以加入：

```json
"run_at": { "local": "2026-11-01T01:30:00", "timezone": "America/Los_Angeles", "fold": 1 }
```

`local` 不含 UTC 偏移，`timezone` 使用明确的 IANA 名称。系统保存原始本地时间、时区、所选 `fold` 与规范 UTC 时刻。夏令时不存在的时间拒绝；重复时间必须指定 `fold: 0` 或 `fold: 1`。非重复时间可省略 fold。Windows 通过固定的 tzdata 依赖提供时区数据。重启或更改本机时区不会改写已存 UTC。

未到期任务不创建尝试，也不启动浏览器；到期后由串行执行器领取。准备期间若时钟回拨到启动时刻之前，任务安全延期，本次尝试记为 `DEFERRED`；到达尝试总上限则返回 `ATTEMPT_LIMIT`。提交不明任务的只读恢复不受原始启动时间限制。`run_at` 是启动约束，实际开始见 attempts，平台结果及核实时间单独记录；它不保证作品在该时刻公开。

## 多平台分发

批量清单保留单平台清单的内容字段，移除顶层 `platform`、`account`，改为：

```json
"targets": [
  { "platform": "douyin", "account": "creator" },
  { "platform": "xiaohongshu", "account": "creator", "overrides": { "title": "平台专用标题" } },
  { "platform": "tencent", "account": "creator" }
]
```

使用 `sau jobs ... --json enqueue-batch --manifest batch.json` 保存，使用 `batch-status <batch_id>` 查询；Python 对应 `enqueue_batch(payload)` 和 `batch_status(batch_id)`。`overrides` 仅覆盖内容和调度字段，不能更换 schema、交付键或目标身份。批次交付键在同一数据库内唯一，子任务继承该键并由平台、账号区分。

所有目标的规范化内容快照或验证错误先在同一事务内保存，然后创建独立子任务。重复批次返回原集合；同键改变请求返回 `CONFLICT`。恢复未完成的批次登记使用原快照，即使本地素材已改变也不会改成另一份内容；后续执行仍会核验文件版本。成功目标和提交不明目标不会因再次登记批次而重发。

汇总包含各目标 job_id、提交状态、平台结果、错误、证据和账号暂停原因。单个目标不支持或失败不会撤销其他目标。汇总的 `COMPLETED` 表示所有目标已核实为受理、审核中或已发布；拒绝和草稿不会被计为统一成功，应继续查看逐平台结果。排队与暂停保持可见，系统不会删除已发作品来回滚部分成功。
