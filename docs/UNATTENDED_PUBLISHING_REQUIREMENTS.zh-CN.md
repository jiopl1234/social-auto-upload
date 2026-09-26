# social-auto-upload 三平台无人值守发布：代码核验与开发需求

审计日期：2026-09-26。适用场景：小红书、抖音、微信视频号各一个个人账号，发布自己的原创内容；常态运行不依赖人工，不接入官方发布接口。

**结论：以现有 CLI 和 uploader 为底座做有边界的改造，不能直接把当前 main 接定时器投入无人值守生产。** 阻断项主要是内容声明被默认改写、提交后盲重试、发布结果不可核实，以及缺少持久任务恢复。浏览器隐蔽性参数不是这些问题的解决办法。

本文件是开发规格，尚未实现其中改造。P0 表示“本项目无人值守上线前必须完成”，不是通用安全漏洞严重度。P1 是稳定运行和维护所需；明确标为可延期的能力可先关闭。

## 1. 仓库与证据基线

| 项目 | 本次结果 |
|---|---|
| 本地位置 | `C:\Users\Chinese\social-auto-upload` |
| 上游 | <https://github.com/dreammis/social-auto-upload> |
| 分支与提交 | `main` / `0012d2c355f88f683cc38dde2a2db209e14091bc` |
| 同步 | 已 fetch；`HEAD...origin/main` 左右差异 `0 / 0` |
| codebaseMCP 项目 | `social-auto-upload`，在新路径重新索引 |
| 图生成时间 | `2026-09-26T11:11:01Z` |
| 索引规模 | fast 模式，1,163 节点、4,647 边 |
| 证据层级 | Tier 2，针对三平台发布主路径和任务入口验证 |
| 方法 | `search_graph` → 双向 `trace_path` → `get_code_snippet` → `check_index_coverage` → 必要源码回读 |
| 边界 | 没有登录账号、上传素材或真实发布；没有测出平台封禁概率 |

核心搜索与调用链分页均已收完。被引用的代码文件逐一检查了覆盖；多个文件在重建索引后仍标记 `metadata_changed`，已直接读取相关源码范围交叉核对，不能把图结果当作完整性证明。图中出现测试 FakeLocator 与真实 API 的误关联，相关结论以实际源码为准。

`docs/`、`examples/`、部分 skill scripts、静态资源等在索引排除列表内；本次使用的 README、安装说明、legacy Web 说明直接读取。`requirements.txt` 有部分解析记录，已全文读取。Dockerfile 与 SCSS 的解析缺口不在本次实现范围；未审计排除的 stealth 脚本，未据此声称其有效或安全。仓库没有 `.codegraph/`。

## 2. 实际可复用的入口与边界

```text
sau_cli.main → build_parser → dispatch
  ├─ upload_video / upload_note
  │    → douyin_setup(handle=False)
  │    → DouYinVideo / DouYinNote → upload
  ├─ upload_xiaohongshu_video / upload_xiaohongshu_note
  │    → xiaohongshu_setup(handle=False)
  │    → XiaoHongShuVideo / XiaoHongShuNote → upload
  └─ upload_tencent_video
       → tencent_setup(handle=False)
       → TencentVideo → upload → submit_publish

legacy Web：sau_backend.postVideo/postVideoBatch
       → myUtils.postVideo.* → uploader
       （平行旧入口，不具备下面要求的任务协议）
```

保留已有 dataclass 请求模型、parser、登录/检查独立命令、视频和图片校验、媒体填写、部分封面/合集/图文操作。CLI upload wrapper 使用 `handle=False`，失效时抛错，**不是每次上传自动扫码登录**；`main` 对普通异常退出 1，也不是完全吞异常。

首版范围固定为抖音视频/图文、小红书视频/图文、视频号视频。视频号 `TencentNote` 的关键步骤抛 `NotImplementedError`，不能因类存在就算支持图文。首版保留立即发布，增加本地 `run_at` 调度；平台预约发布先关闭，完成 R09 后再开放。

现有 `BaseVideoUploader.validate_publish_date` 已拒绝过去时间和距离当前不超过两小时的预约时间；开发重点是时区、实际页面写入和最终确认，不能重复立项成“从零增加未来时间校验”。该两小时规则是仓库当前规则，本次没有验证它等于各平台当前政策。

## 3. 代码事实与定位

下列行号对应上述提交；修改后需更新定位。

| 证据 | 文件/符号 | 已核实行为 |
|---|---|---|
| E01 | [sau_cli.upload_video](C:/Users/Chinese/social-auto-upload/sau_cli.py:409)、[小红书 wrapper](C:/Users/Chinese/social-auto-upload/sau_cli.py:514)、[视频号 wrapper](C:/Users/Chinese/social-auto-upload/sau_cli.py:594) | 返回账号文件路径；dispatch 打印 submitted，不能表达作品状态 |
| E02 | [DouYinVideo.upload](C:/Users/Chinese/social-auto-upload/uploader/douyin_uploader/main.py:971) | 1051–1052 无声明时默认“内容由AI生成”；1071–1110 循环点击并只等 3 秒跳页；整个视频 upload 缺 finally |
| E03 | [DouYinNote.upload_note_content](C:/Users/Chinese/social-auto-upload/uploader/douyin_uploader/main.py:1179) | 1215 起同样循环提交；图文 upload 有 finally，但保护范围和关闭异常处理不足 |
| E04 | [小红书声明函数](C:/Users/Chinese/social-auto-upload/uploader/xiaohongshu_uploader/main.py:469) | 名为 check_original_declaration，实际尝试“来源转载”；视频 678、图文 801 调用；失败可继续 |
| E05 | [小红书视频](C:/Users/Chinese/social-auto-upload/uploader/xiaohongshu_uploader/main.py:623)、[图文](C:/Users/Chinese/social-auto-upload/uploader/xiaohongshu_uploader/main.py:771) | “已选择”/“100%”/标题框可被用作视频就绪；图文主要等标题框；683、806 起循环点击发布 |
| E06 | [TencentBaseUploader.submit_publish](C:/Users/Chinese/social-auto-upload/uploader/tencent_uploader/main.py:882) | 最多 20 轮，常规 click 失败再 JS click；URL 离开 create 或按钮消失即可成功；888 全局移除浮层 |
| E07 | [视频号实时验证](C:/Users/Chinese/social-auto-upload/uploader/tencent_uploader/main.py:524)、[抖音验证码](C:/Users/Chinese/social-auto-upload/uploader/douyin_uploader/main.py:748) | 前者等待管理员扫码、弹窗消失即继续；后者记录验证码、固定等待后返回成功；抖音另有 stdin 输入路径 |
| E08 | [视频号声明](C:/Users/Chinese/social-auto-upload/uploader/tencent_uploader/main.py:790) | 默认“含AI生成内容”，未暴露对应构造参数；声明/指定封面应用失败可继续 |
| E09 | [视频号预约](C:/Users/Chinese/social-auto-upload/uploader/tencent_uploader/main.py:551) | 不同月只翻一次，未核对年份；找不到日不必然失败；时间只填 `%H` 丢失分钟 |
| E10 | [通用日期校验](C:/Users/Chinese/social-auto-upload/uploader/base_video.py:55)、[CLI parse_schedule](C:/Users/Chinese/social-auto-upload/sau_cli.py:289) | 基类已有未来/两小时约束；CLI 解析无时区时间；三平台页面端缺完整读回 |
| E11 | [抖音构造器](C:/Users/Chinese/social-auto-upload/uploader/douyin_uploader/main.py:628) | collection_name 插在 desc 与 publish_strategy 之间，破坏旧位置参数绑定 |
| E12 | [数据库初始化](C:/Users/Chinese/social-auto-upload/db/createTable.py:18)、[Web 发布](C:/Users/Chinese/social-auto-upload/sau_backend.py:409) | DB 初始化为账号/素材两表；限定入口范围未见持久发布 jobs/attempts；active_queues 是登录 SSE |
| E13 | [旧发布封装](C:/Users/Chinese/social-auto-upload/myUtils/postVideo.py:13)、[时间 helper](C:/Users/Chinese/social-auto-upload/utils/files_times.py:41) | start_days 被当作第 4 个 timestamps 参数；实际 start_days 为第 5 参数 |
| E14 | [Web 批量发布](C:/Users/Chinese/social-auto-upload/sau_backend.py:520) | 554–555 漏传 thumbnail，造成商品参数位置错位；旧批次没有逐任务持久结果 |
| E15 | [pyproject](C:/Users/Chinese/social-auto-upload/pyproject.toml:10)、[百家号 import](C:/Users/Chinese/social-auto-upload/uploader/baijiahao_uploader/main.py:19) | 主线安装依赖 Patchright，但 CLI eager import 的其他 uploader 依赖未声明的 Playwright，实测导入失败 |
| E16 | [日志配置](C:/Users/Chinese/social-auto-upload/utils/log.py:32) | INFO 文件日志开启 backtrace/diagnose；需与验证码、账号文件和截图脱敏策略一起处理 |

## 4. 开发需求与验收

### R01 · P0 · 修复安装基线和已有兼容性回归

范围：`pyproject.toml`、`sau_cli.py` 顶层 import、抖音构造器、对应测试。依据 E11/E15。

- 平台加载按命令延迟进行，不能因不使用的百家号依赖缺失而阻断三平台 CLI；同时把支持的平台所需依赖完整声明。不要把临时补装全部旧 requirements 当正式修复。
- 恢复 DouYinVideo 旧位置参数 `desc → publish_strategy → debug → headless` 的语义，新增 collection_name 用关键字参数；不能未经兼容性核查就把全部旧可选参数变为 keyword-only。
- 测试 Namespace 通过真实 parser 生成；修复异步替身，避免把 `MagicMock` await 失败误当业务异常覆盖。
- 清理 setuptools 中不存在的 cli_main 陈旧项；为安装、测试配置提供无账号默认路径或明确初始化命令。

验收：干净环境只按当前声明依赖安装，三平台 `--help` 可执行；原位置参数测试不改语义即通过；全套现有测试通过。不得仅把 XHS 2000ms 断言改到 4000ms 就声称发布可靠性完成，应转为话题选择/正文不受损的行为断言。

### R02 · P0 · 内容声明和发布清单必须准确

范围：E02/E04/E08，三平台请求模型与元数据 helper。

- 清单分别表示内容归属、AI 使用情况、需要平台应用的声明及转载来源；“原创”不等于“未用 AI”。没有事实依据不能默认选择 AI、转载或平台原创权益声明。
- 不指定可选声明时不擅自更改；若当前平台要求填写，缺少事实则在提交前返回 validation_failed，不能猜测。
- 删除上传流程的 AI 默认赋值和无条件转载选择，复用抖音已有显式声明 helper 的失败阻断意图。
- 对用户要求的声明、封面、合集、标题正文话题做最终读回；必需字段失败阻止提交。可选降级必须在任务中明确允许，并返回 applied_metadata/warnings。
- 超长标题、标签数量超限在上传前拒绝或按明确转换策略处理；禁止 helper 静默截断、修改原请求。话题不能无条件点第一候选，失败不能只靠 Backspace 猜测恢复正文。

验收：原创非 AI、原创 AI、明确转载分别有正确选择；缺少转载来源先失败；声明入口消失、读回不一致、required 封面失败时提交计数 0；不匹配的话题首候选不被选中。视频和图文完整流程都测，不能只测声明 helper。

### R03 · P0 · 提交边界、结果协议和只读核对

范围：E01–E06。把单体 upload 拆成可控阶段，保留平台 UI 填写逻辑。

- 提交前记录 SUBMITTING 和 attempt_id；一次提交尝试最多触发一次平台最终提交动作。若平台有已识别的两阶段确认，分别记录阶段，不能用重新点击第一步代替未知状态判断。
- 取消“等 3 秒失败就再点”、常规 click 超时后 JS 再点、URL 变化/按钮消失即成功。
- 点击可能已送出后的任何不确定异常进入 UNKNOWN_COMMIT；先只读查询目标账号作品列表/详情。以可获得的作品 ID 优先匹配，并结合素材、标题、提交时间范围；只匹配标题不够。
- 找不到、找到多个候选、没有可靠匹配字段，均保持 UNKNOWN_COMMIT；不能因短期列表不可见就证明“未发布”。
- 分开记录 ACCEPTED、PLATFORM_PENDING、SCHEDULED、PUBLISHED、REJECTED、DRAFT；没有明确发布证据不能填 PUBLISHED。回读通道 unavailable 也是结果，不可擅自重投。

验收：点击已生效但导航延迟 30 秒、点击回执丢失、导航到登录页、按钮短暂卸载，最终提交动作计数不超过 1；重启核对发现作品后不重发；成功页无作品证据不算 PUBLISHED；审核中与预约成功具有不同结果。

平台没有给此非官方路径提供可依赖的幂等提交协议，本系统不能保证远端 exactly-once。设计目标是防止本地已知的重复执行，并在结果不明时停止提交，而不是假称消除所有重复可能。

### R04 · P0 · 持久任务、账号串行与崩溃恢复

范围：新增任务核心；E12 表明不能复用登录 SSE 队列充当发布任务队列。

- 单机 SQLite 足以承载本场景；新增 jobs、attempts、events、account_bindings，事务更新状态和事件。不要初版引入 Redis/Celery/Kubernetes。
- `(platform, account_id, idempotency_key)` 唯一；同一请求重试沿用 key。素材摘要与规范化清单版本单独保存，用于重复内容识别和防止执行前素材被替换。跨平台发布是三个独立 job。
- 首版一个 worker 串行执行即可；账号同一时间只允许一个写入流程，login/check/profile 维护也不得与上传抢同一会话。
- 使用进程间锁及任务所有权检查。只靠 lease 超时不够：旧进程仍可能点发布；未确认旧 worker/browser 已停止，不允许新执行器接管写入。
- PREPARING 阶段崩溃可按明确条件恢复；SUBMITTING 后崩溃一律走 UNKNOWN_COMMIT/核对。即使实际点击前崩溃，只要无法证明，也取保守路径。
- 重试按阶段/原因区分：确定未提交的临时错误可有界重试；身份、内容、UI 变化不自动重试；提交不明禁止重新上传。

验收：双进程同时领同账号任务只有一个进入写入；重复入队命中同一个 job；各阶段杀进程后恢复状态正确；提交后崩溃不再点发布；暂停一个账号时其他两个平台正常运行。

### R05 · P0 · 正向账号校验与挑战停止

范围：三平台 cookie_auth/setup/upload，E07。

- 预期账号用平台稳定 ID 绑定；昵称/头像只作诊断。发布前和最终提交前核对，不能仅凭“没看到登录框”判有效。
- 区分 AUTHENTICATED、AUTH_REQUIRED、IDENTITY_MISMATCH、CHALLENGE_REQUIRED、NAVIGATION_ERROR；空白壳页不是登录成功。
- 常态无人值守发现验证码、实名验证、扫码或未知安全提示就返回并暂停该账号；不读取 stdin、不重复索取验证码、不在弹窗后继续点发布。
- 删除全局移除 `.mask/.common-dialog` 等策略；只关闭明确识别的普通引导提示。未知浮层返回 UI_CHANGED/BLOCKED 并留脱敏证据。
- 登录保持独立维护命令，已有 `setup(handle=False)` 继续保留；既有会话不满足条件时不自动切另一套设备/账号。

验收：错误账号的有效 session、晚加载登录框、各阶段突然出现验证，都不能提交；不调用 input；弹窗消失但身份未确认不能继续。无人工响应时系统可保持暂停，不承诺账号挑战后仍能自动恢复。

### R06 · P0 · 阶段截止、上传就绪与资源回收

范围：三平台 upload/wait/close，E02/E03/E05；视频号已有一小时上传截止与有限重试，可复用但需错误分类。

- 统一总 deadline 和分阶段预算，使用单调时钟；导航、素材上传、处理、元数据、提交观察、只读核对均有上限。
- “标题框可填写”“已选择”“100%”不能单独证明素材已处理完成；图文确认成功预览数量、失败项，视频确认无处理错误和所需状态。
- 页面关闭、任务取消、UI 变化不能一律吞为“再等一下”。禁止无界 while True。
- browser 创建成功即纳入清理；context 创建、脚本注入、storage_state 保存失败也能关闭资源。context.close 失败仍需 browser.close，清理失败不覆盖主要业务异常。

验收：视频仍处理时不提交；十张图片只成功九张不提交；永不就绪会准时退出；依次注入资源创建/关闭/取消异常，所有已创建资源被清理，保留最初错误。

### R07 · P0 · 结果可消费、诊断可追溯

范围：sau_cli wrapper/dispatch/main、日志配置 E01/E16、新任务命令。

- uploader 返回结构化 PublishResult，wrapper 不再以 cookie 路径代表上传结果。新增机器可读 JSON 输出，stdout 只含结果，日志走 stderr。
- 至少包含 job_id、attempt_id、platform/account_id、state、submission_attempted、external_id、时间、applied_metadata、warnings、error_kind、evidence_refs；未获得的字段为 null，不能猜值。
- 可重试性由状态和提交边界决定，不能仅靠一个任意 retryable=true 字段。提交不明必须不可自动重投。
- 日志绑定任务/尝试/阶段；保存脱敏错误截图和可用页面证据。Cookie、验证码、二维码、含认证 token 的 URL 不进普通日志；证据限定本机访问和保留期。
- 现有 diagnose/backtrace 在生产关闭或采用经过验证的脱敏过滤；会话文件采用本机用户权限隔离，更新使用临时文件加原子替换。

验收：状态与 JSON/任务库一致；CLI 异常仍非零；注入测试验证码和 Cookie 后检索日志/证据不存在明文；失败前后的审计事件可还原提交是否可能发生。

### R08 · P1 · 会话与浏览器环境集中管理

当前路径主要是新建 browser/context 后导入 storage_state，并非完整持久浏览器 profile。先集中封装生命周期、固定支持的浏览器/驱动版本和三个账号的独立会话目录，消除各平台分散配置；发布过程中不随机轮换环境。

是否迁移至每账号独立 persistent profile，应作为独立改动验证登录保留、IndexedDB/存储依赖、崩溃恢复和 Windows 文件锁。不能未经验证同时切驱动、profile 和提交逻辑。保留旧状态备份且不与日常浏览器共享用户目录。

验收：不同平台/账号会话不串用；重启后仍能识别正确账号；第二进程无法并发打开同一 profile；浏览器升级有版本记录和回退路径。以上是工程一致性要求，不构成降低平台风控概率的已测结论。

### R09 · P1，可延期关闭 · 预约与本地调度时间语义

范围：E09/E10、三平台 set_schedule_time；优先支持本地到时执行立即发布。

- 区分 run_at（本地何时启动）与 publish_at（要求平台预约发布）；后者不是前者的别名。run_at 只是启动时间，不保证那一秒作品已公开。
- 保存 UTC instant 与显式 IANA 时区，国内内容计划可配置 Asia/Shanghai；不要把本机 America/Los_Angeles 当用户意图。
- 复用基类已有未来/提前量校验，并按平台能力验证窗口；提交前再次检查。页面必须完整写入年月日时分并读回预约开关与值。
- 视频号修复只翻一次月和丢分钟的问题；页面不支持目标日期/粒度时明确拒绝。

验收：23:45、跨月/年、DST 歧义、不存在时间、平台自动调整、日期不可选均有确定结果；设置失败绝不降为立即发布。首版关闭平台预约时，遇 publish_at 应返回 unsupported，不能静默忽略。

### R10 · P1 · 平台适配契约与故障注入测试

基于现有 unittest/pytest 增加有状态页面替身与本地 DOM fixtures。四条图文/视频路径和视频号视频共五条实际发布路径执行同一合同测试。对具有平台差异的元数据/模态框另测。

关键断言是提交动作次数、最终状态、账号一致、发布内容、资源释放和恢复；避免主要断言固定 sleep 毫秒或内部调用顺序。测试包括 R02–R07 的故障场景、进程终止恢复、双 worker 竞争。测试不可持有真实 Cookie、不可访问平台创作者后台。

验收分层：纯单元测试 → 本地浏览器 DOM 测试 → 平台只读能力探测 → 另行明确授权的少量真实发布验证。前两层通过仅证明工程逻辑；作品回读字段、稳定账号 ID 入口和当前页面结构仍需后两层确认。

### R11 · P1 / 后续范围 · 外部清单与 legacy Web 边界

初版不启动 legacy Web。README 已说明它不是主线，不保证与 CLI/uploader 同步。E13/E14 的参数错位记录为 legacy 缺陷；如果选择接回 Web，必须通过统一请求模型入队，不能继续平行拼装位置参数。

本地 CLI 允许用户显式选文件本身不是漏洞。将来接外部任务输入时，改为注册素材 ID/账号 ID，验证允许根目录、拒绝越界路径、执行前复核内容摘要。Web 默认监听所有网卡且发布接口无自身认证，不应作为新任务服务默认部署方式；新接口需要时先绑定 loopback 并认证。

验收：未知平台/视频号图文提前返回 unsupported；越界清单不能读写任意文件；外部未授权请求不能入队；混合结果批次可按 job 单独查询，不能整批重发。

## 5. 建议模块与实现依赖

以下为拟新增模块，当前仓库尚不存在这些实现：

```text
orchestrator/models.py       Job、PublishResult、状态及错误类型、内容清单
orchestrator/store.py        SQLite 事务、唯一键、任务/尝试/事件
orchestrator/worker.py       串行执行、阶段预算、锁、恢复分派
orchestrator/reconcile.py    只读核对流程与候选匹配规则
orchestrator/validation.py   素材摘要、账号绑定、时间与字段校验
orchestrator/cli.py          enqueue / status / run / JSON
uploader/common/runtime.py  浏览器生命周期、会话存储、任务证据
```

平台 UI 留在现有三个 uploader 目录。把 upload 逐步切成 `preflight → upload_assets → apply_metadata → verify_ready → submit_once → observe/reconcile`；统一协议不要求所有平台拥有完全相同的页面步骤。

拟定任务状态：

```text
QUEUED → PREPARING → READY → SUBMITTING
                                ├─ ACCEPTED → PLATFORM_PENDING → PUBLISHED/REJECTED
                                ├─ SCHEDULED → 后续只读追踪
                                ├─ DRAFT
                                └─ UNKNOWN_COMMIT → 只读核对 → 已核实状态或保持不明

提交前：VALIDATION_FAILED / AUTH_REQUIRED / BLOCKED / FAILED_PRE_SUBMIT
账号状态：ACTIVE / PAUSED_AUTH / PAUSED_UI（与作品状态分开）
```

没有证据的 UNKNOWN_COMMIT 不自动转 QUEUED；暂停账号不改写已经提交作品的远端状态。

实施顺序：

1. **基线修复**：R01；让安装和现有测试可信，保留基线失败记录。
2. **正确性协议**：R02/R03/R07；先确定内容清单、结果和提交边界，接入最小 store 状态记录。
3. **可恢复执行**：R04/R05/R06；完成持久恢复、账号锁、挑战停止、阶段截止和清理。
4. **验收与会话收敛**：R08/R10；完成离线故障矩阵；五条路径分别达到门槛，不以其中一平台通过代替其他平台。
5. **可选扩展**：R09 平台预约、R11 Web/外部输入。海外平台、新增视频号图文不在本轮。

这里不提供无依据的精确工期。主要不确定项是三平台当前账号 ID 提取、作品回读与稳定匹配字段；这些要先做只读验证，不能到开发末尾才发现无法判定成功。

## 6. 本次实际测试与文件变更

环境：Windows、Python 3.12.10。创建了仓库本地 `.venv`，按 frozen uv.lock 安装主线依赖；按安装文档复制 `conf.example.py` 为被 Git 忽略的 `conf.py`。没有修改业务源码或依赖声明，没有安装额外浏览器。

第一次测试由于尚无 conf.py，7 个测试模块 collection error。这是文档要求的本地配置前置条件，不直接算产品逻辑缺陷。

补齐配置后，主线依赖基线执行：

```powershell
uv run --frozen --with pytest --with pytest-timeout python -m pytest tests -q --timeout=30 --tb=short
```

结果：**2 个 collection error**，CLI 导入百家号 uploader 时缺 playwright。另行执行 `uv run --frozen python -m sau_cli --help` 复现同一错误。

仅为继续诊断，用临时运行环境补充历史 requirements 指定的 Playwright：

```powershell
uv run --frozen --with pytest --with pytest-timeout --with playwright==1.52.0 python -m pytest tests -q --timeout=30 --tb=short
```

结果：**41 passed，6 failed，5.57 秒**。临时补充依赖没有修复 pyproject；普通主线 CLI 仍存在上述缺依赖问题。

| 失败测试 | 实测原因 | 开发含义 |
|---|---|---|
| DouyinDeclarationTests.test_legacy_positional_runtime_flags_keep_their_meaning | publish_strategy 为 False，而非 scheduled | 构造器真实兼容回归，修代码，不能改成错误期望 |
| DouyinDeclarationTests.test_declaration_failure_closes_browser_resources | await 普通 MagicMock，未走到声明错误 | 修测试替身后验证真实清理逻辑；当前失败本身不能证明清理已覆盖 |
| BrowserCliDispatchTests.test_dispatch_douyin_upload_note_uses_new_request_fields | 手造 Namespace 缺 notef | 与 parser 契约同步，后续 bgm 等字段同样需覆盖 |
| BrowserCliDispatchTests.test_dispatch_douyin_upload_video_uses_dual_thumbnail_request_fields | Namespace 缺 collection | 修夹具/通过真实 parser 构造 |
| BrowserCliDispatchTests.test_dispatch_tencent_upload_video_uses_dual_thumbnail_request_fields | Namespace 缺 collection | 同上 |
| XiaohongshuUploaderTests.test_video_fill_meta_uses_desc_then_first_tag | 预期 timeout 2000，实际 4000 | 过度绑定实现细节，应增加正确话题/正文不受损的断言 |

通过项包括基于本地 HTML 的快手弹窗与视频号验证弹窗测试；它们没有访问真实发布页面。41 个通过项不证明实际账号登录、审核结果或长期无人值守可用。

保存的原始记录：

- [主线依赖测试输出](C:/Users/Chinese/social-auto-upload/docs/audit/pytest-baseline-2026-09-26.txt)
- [主线依赖 JUnit XML](C:/Users/Chinese/social-auto-upload/docs/audit/pytest-baseline-2026-09-26.xml)
- [补充依赖诊断输出](C:/Users/Chinese/social-auto-upload/docs/audit/pytest-diagnostic-2026-09-26.txt)
- [补充依赖诊断 JUnit XML](C:/Users/Chinese/social-auto-upload/docs/audit/pytest-diagnostic-2026-09-26.xml)

当前交付仅为仓库迁移/同步、代码级分析与测试记录。无人值守上线的最小门槛是 R01–R07 完成并通过 R10 的对应离线测试，再对每条目标平台路径验证真实作品回读与发布状态；没有这些证据，不能把“函数正常返回”算作生产就绪。
