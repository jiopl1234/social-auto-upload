# 持久发布任务

任务接口使用独立的 SQLite 数据库保存交付意图和事件。当前已实现入队、查询、交付键去重及串行准备流程；平台执行能力会随 tracker 的后续任务接入。`QUEUED` 只表示已保存，不表示已发布。

## 命令

先按 [安装说明](install.md) 初始化配置并安装项目。旧的 `sau <platform>` 命令参数保留；新接口位于 `sau jobs`。为调度程序使用 `--json`；将任务数据库保存在固定位置，以便重启后查询。

```powershell
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json enqueue --manifest content.json
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json status <job_id>
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json events <job_id>
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json attempts <job_id>
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json list
sau jobs --db C:/Users/Chinese/.sau/tasks.sqlite3 --json accounts
```

省略 `--db` 时使用当前用户的 `~/.sau/tasks.sqlite3`。命令选项 `--db`、`--json` 放在动作名称前。

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
  "media": [{"path": "C:/Users/Chinese/Videos/episode-001.mp4"}]
}
```

`platform` 接受 `douyin`、`xiaohongshu`、`tencent`；视频号只接受 `video`。`account` 是本机账号引用，稳定平台账号绑定将由账号维护接口管理。素材及内容事实的完整预检属于 04 号票；不要把此阶段的入队成功当作素材已符合平台要求。

平台、账号、`delivery_key` 共同标识一次交付意图。同一个键提交相同清单返回原任务；只改变 JSON 对象字段顺序不创建新任务。同键内容不同返回 `CONFLICT`，不会覆盖原清单。素材摘要用于内容一致性核查，不替代交付键。

## 查询与结果

结果包含任务及尝试标识、平台/账号、清单摘要、任务状态、提交状态、平台结果、作品 ID、核实时间、实际元数据、警告、错误和证据引用。尚未获得的结果为 `null`，并保留提交清单以供核对。刚入队的任务为 `QUEUED`、`NOT_SUBMITTED`；尝试列表为空。

任务协议与后续执行使用同一组领域语义：

| 字段 | 约定 |
|---|---|
| `job_id` | 一个平台、账号、交付键上的持久交付意图；重试不更换 |
| `attempt_id` | 某次执行的标识；尚未领取时为 null |
| `status` | QUEUED、PREPARING、READY、SUBMITTING、UNKNOWN_COMMIT、COMPLETED、FAILED_PRE_SUBMIT、BLOCKED、CANCELLED；当前执行准备流程可到 READY、FAILED_PRE_SUBMIT 或 BLOCKED |
| `submission` | NOT_SUBMITTED、MAY_HAVE_SUBMITTED、CONFIRMED；只有正向回执或作品证据才可确认 |
| `platform_result` | ACCEPTED、IN_REVIEW、PUBLISHED、REJECTED、DRAFT；尚无证据时为 null，与任务是否执行结束分开 |
| `verified_at` | 最近一次作品证据的核实时间；入队时间不能替代此字段 |
| 事件 | 关联 job、可选 attempt、状态、阶段和时间；与相应状态更新处于同一事务 |
| 账号绑定 | 平台、逻辑账号引用、预期稳定账号 ID、独立会话路径和账号暂停状态；昵称不是稳定 ID |

平台拒绝不等于可重投的网络错误。进入 SUBMITTING 后如不能确认结果，后续执行必须保留 UNKNOWN_COMMIT 并只读核对，不能因为查询尚无作品就重发。02 阶段定义并持久保存这些字段，03–06 号票接入执行与恢复。

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

Python 公共接口 `TaskService(database, runtime_dir=...)` 提供 `run_once(adapters=..., job_id=...)` 和串行 `run_pending(adapters=...)`。适配器通过 `prepare(context)` 填写内容并返回实际元数据，通过 `close()` 清理资源。当前阶段只推进到准备结果，`READY` 没有平台成功含义；最终提交和只读核对由后续票接入。

每次领取建立一个 attempt，并在同一事务内更新任务和事件。适配器每次写入前可调用 `context.assert_owner()`；最终提交也必须通过此所有权检查。创建浏览器前调用 `resources_started()`，成功回收后才能调用 `resources_closed()`；可用 `register_process(pid)` 登记子进程并在关闭时验证其退出。资源关闭不明会阻断账号。`AUTH_REQUIRED`、`IDENTITY_MISMATCH`、`UI_CHANGED` 等暂停原因可通过 `accounts` 查询；同账号后续任务保持排队，其他平台继续执行。

所有本机进程与数据库必须共用一个运行目录。默认是 `~/.sau/runtime`，可以统一设置 `SAU_RUNTIME_DIR`；测试或明确隔离的部署可使用 `--runtime-dir` / Python `runtime_dir`。首版每个平台一个账号，因此锁以平台为粒度，避免同一账号的不同别名或数据库绕过互斥。维护接口 `service.maintenance(platform, account)` 及三平台现有 CLI 登录、检查、视频/图文上传入口使用相同的操作系统锁。异常退出的旧流程无法证明资源已关闭时也保留阻断标记；这不把旧上传命令升级为带提交恢复的任务接口。

锁文件不会按时间删除或过期。旧进程存活、身份无法确认、浏览器关闭未确认时均拒绝接管；不要通过删除运行目录或切换运行目录解决账号繁忙。崩溃后的恢复策略将在提交边界票中接入。
