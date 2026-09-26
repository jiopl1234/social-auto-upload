# 持久发布任务

任务接口使用独立的 SQLite 数据库保存交付意图和事件。当前已实现账号绑定、不可变清单预检、串行执行、提交边界和只读恢复；平台适配器会随 tracker 的后续任务接入。`QUEUED` 只表示已保存，不表示已发布。

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
```

省略 `--db` 时使用当前用户的 `~/.sau/tasks.sqlite3`。命令选项 `--db`、`--media-root`、`--json` 放在动作名称前。媒体根目录由本地调用者明确选择，可重复指定；清单不能自己扩大这个范围。绑定操作复制有效 storage-state JSON 到隔离的受管会话路径，不执行登录，也不把配置的 ID 当作在线身份已核实。

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

本地校验只接受列出的保守格式与能力范围，不证明平台当前规则或文件转码一定通过。`publish_at` 首版不支持；`run_at` 将在 12 号票接入，目前显式拒绝。旧任务的查询数据继续保留，旧版未带素材摘要和事实的清单不能直接用新接口执行。

平台权益可通过 `declarations.originality_rights` 与 `declarations.repost_authorized` 的显式布尔值表达；省略时不添加默认值。申请原创权益要求内容归属为原创，声明转载获授权要求内容归属为转载；两者都不会推导或改写 AI 使用事实。具体控件是否支持及权益资格仍需平台适配器核查。

平台、账号、`delivery_key` 共同标识一次交付意图。同一个键提交相同清单返回原任务；只改变 JSON 对象字段顺序不创建新任务。同键内容不同返回 `CONFLICT`，不会覆盖原清单。素材摘要用于内容一致性核查，不替代交付键。

## 查询与结果

结果包含任务及尝试标识、平台/账号、清单摘要、任务状态、提交状态、平台结果、作品 ID、核实时间、实际元数据、警告、错误和证据引用。尚未获得的结果为 `null`，并保留提交清单以供核对。刚入队的任务为 `QUEUED`、`NOT_SUBMITTED`；尝试列表为空。

任务协议与后续执行使用同一组领域语义：

| 字段 | 约定 |
|---|---|
| `job_id` | 一个平台、账号、交付键上的持久交付意图；重试不更换 |
| `attempt_id` | 某次执行的标识；尚未领取时为 null |
| `status` | QUEUED、PREPARING、READY、SUBMITTING、UNKNOWN_COMMIT、COMPLETED、FAILED_PRE_SUBMIT、BLOCKED；CANCELLED 由 06 号票接入。COMPLETED 表示已核实作品结果，不等于公开发布 |
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

## 执行器与账号维护

Python 公共接口 `TaskService(database, runtime_dir=..., media_roots=[...])` 提供 `run_once(adapters=..., job_id=..., max_attempts=2)` 和串行 `run_pending(adapters=..., max_attempts=2)`。完整适配器实现 `prepare(context)`、`submit(context)`、`reconcile(context)` 和 `close()`；准备返回实际元数据，提交最多调用一次，核对只读作品列表或详情。仅实现准备和清理的兼容适配器结束于 `READY`，该状态没有平台成功含义。

完整适配器的 `prepare` 必须报告实际读回的 `title`、`body`、`tags`、`media`、`cover`、`collection`、`declarations`。`media` 是有序的 `{kind, sha256}` 列表，摘要关联已确认上传的本地素材；`cover` 为同结构的对象或 null。编排层在 SUBMITTING 前将这些值与不可变清单比较。必需内容或声明不符会返回字段错误并阻止提交；只有明确允许遗漏的可选字段可为空，并保存 `FIELD_OMITTED` 警告。该契约要求适配器提供真实页面证据，不能通过复制请求伪造读回。素材摘要的客户端记录不代表平台返回了同一摘要。

`max_attempts` 限制同一任务的尝试总数，允许 1–5，默认 2。只有确定未越过提交边界的 `TRANSIENT_PRE_SUBMIT` 才可重试；认证、身份、声明、UI 变化和审核拒绝不进入重试循环。即使适配器在提交后错误地返回临时错误类别，持久边界也会禁止重发。重启后尚在 SUBMITTING 的任务自动进入只读恢复；UNKNOWN_COMMIT 不进入自动待发布队列，可用指定 `job_id` 的执行调用再次只读核对。核对不依赖原始本地素材仍然存在。

每次领取建立一个 attempt，并在同一事务内更新任务和事件。适配器每次写入前可调用 `context.assert_owner()`；最终提交也必须通过此所有权检查。创建浏览器前调用 `resources_started()`，成功回收后才能调用 `resources_closed()`；可用 `register_process(pid)` 登记子进程并在关闭时验证其退出。资源关闭不明会阻断账号。`AUTH_REQUIRED`、`IDENTITY_MISMATCH`、`UI_CHANGED` 等暂停原因可通过 `accounts` 查询；同账号后续任务保持排队，其他平台继续执行。

所有本机进程与数据库必须共用一个运行目录。默认是 `~/.sau/runtime`，可以统一设置 `SAU_RUNTIME_DIR`；测试或明确隔离的部署可使用 `--runtime-dir` / Python `runtime_dir`。首版每个平台一个账号，因此锁以平台为粒度，避免同一账号的不同别名或数据库绕过互斥。维护接口 `service.maintenance(platform, account)` 及三平台现有 CLI 登录、检查、视频/图文上传入口使用相同的操作系统锁。异常退出的旧流程无法证明资源已关闭时也保留阻断标记；这不把旧上传命令升级为带提交恢复的任务接口。

锁文件不会按时间删除或过期。旧进程存活、身份无法确认、浏览器关闭未确认时均拒绝接管；不要通过删除运行目录或切换运行目录解决账号繁忙。只有旧进程已退出且资源关闭能够确认，提交前的未完成尝试才可在上限内恢复；提交后的恢复复用原尝试，只读核对。离线测试证明本地防重复执行，不能证明平台远端 exactly-once。
