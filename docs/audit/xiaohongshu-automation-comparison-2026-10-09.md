# 小红书自动化风控：xiaohongshu-mcp 与本地 CLI 的代码及实测对比

研究日期：2026-10-09。结论适用于本次核对的版本，不代表平台允许全自动运营，也不是规避识别的保证。

## 结论

不建议因为“不会风控、不会封号”就把现有项目换成 xiaohongshu-mcp，在已收到警告的账号上继续全自动发表。它有值得借鉴的浏览器和页面交互实现，但维护者本人已经明确承认风险，公开问题中也有警告、封禁和发布后不可见的报告。更合适的工程路线是保留现有任务层和提交证据，修正真实平台交互与发布后风险处理；这些改进不能保证平台接受 AI 托管。

用户本次提供的通知原文为：“AI托管进行发文/互动，与真人操作习惯不符”。这是**用户提供的平台行为判定证据**，不是 AI 生成视频或搬运内容的判定；本次未登录账号独立读取通知，因此没有通知发生时间、完整处罚范围及平台内部触发信号。视频原创、没有 AI 生成内容，不能排除这类账号操作限制。

此前的 `COMPLETED/CONFIRMED` 证明了提交与作品对应关系；它不证明后续审核、曝光和账号安全。尤其 `3.mp4` 当时仍为 `IN_REVIEW`。这次用户反馈必须作为新增负面线上证据，不能继续用两次技术交付成功推导“真实平台安全可用”。[首次记录](xiaohongshu-live-video-2026-09-30.md)、[后续记录](xiaohongshu-live-video-2026-10-01.md)。

## 核查范围与版本

| 对象 | 固定版本 / 数据 |
| --- | --- |
| 本地 social-auto-upload | `b934f06620edf5459684b075d6c778cfb52e811c`；研究开始时工作区干净 |
| xpzouying/xiaohongshu-mcp | `7797fd375aba02dcd33afe143f6bee13e5870859`，提交时间 2026-10-09 10:31:57 +08:00 |
| 对方浏览器包装依赖 | `headless_browser v0.4.0`，提交 `d28e37c7448672550580fca8ff26404aed09a21f` |
| 本机运行证据 | 只读查询 `.scratch/xiaohongshu-live-2026-09-30/acceptance.sqlite3` 的 jobs / attempts / events；已有三份验收报告 |
| 在线资料 | 项目 README、源码、PR、Issue 与维护者评论；证据摘录另见[公开风险研究](xhs-mcp-public-risk-evidence-2026-10-09.md) |

本次仅克隆参考源码到本机临时目录并阅读，未安装或运行对方软件，未打开小红书登录态浏览器，未重新上传、发表、互动或更改账号状态。未导出 Cookie、签名、上传令牌、稳定账号 ID 或完整平台响应。

两个主仓库和依赖均先用 codebase-memory 建图，再进行符号查询、调用路径和片段阅读。对所有用于结论的源码路径检查覆盖；工具给出 `metadata_changed` 的路径以磁盘源码相关区段复核。本地与参考仓库均无 `.codegraph/`，未额外建 CodeGraph。结论限于已核对的发布链路，不宣称审计完整浏览器内核。

## “不会风控”的说法经不起核对

README 是作者对自己过去运行经历的描述，并非“不被屏蔽/不被限流/所有账号不封号”的承诺。更直接的是，维护者在 2026-07-28 明说：**“风险确实存在，不能说没有”**，并将验证范围描述为测试账号连续若干天正常。[README 固定版本](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/README.md#L305-L309)、[维护者原回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/316#issuecomment-5100848949)。

可以确定“有公开反例”，不能由 Issue 计算封号概率，也不能认定所有案例都是该软件导致：

- [#573](https://github.com/xpzouying/xiaohongshu-mcp/issues/573)：2026-03-11 的发布后仅自己可见报告。
- [#680](https://github.com/xpzouying/xiaohongshu-mcp/issues/680)：2026-05-02 的账号警告报告，用户附图涉及第三方工具/脚本与自动化操作；这是使用者提供的截图，未经我们独立验证账号。
- [#777](https://github.com/xpzouying/xiaohongshu-mcp/issues/777)：2026-07-28 报告少量收藏/查询操作后收到警告。报告者后来回忆使用 v2.1.0；不能当作本次 HEAD 的严格复现。

这些 Issue 的关闭状态不是“风控已解决”的证据。维护者自己的正常测试与使用者的失败报告也都没有受控对照、完整账号历史和足够观察期。[更多原始资料与局限](xhs-mcp-public-risk-evidence-2026-10-09.md)。

README 顶部推荐的 **x-mcp 浏览器扩展是另一个项目**；“在常用 Chrome/Edge 中运行”的宣传不能套到本报告分析的独立 Go 服务上。README 中的作者发帖数量经验也不能当作官方安全额度。[原项目说明](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/README.md)。

## 实际操作差异

| 环节 | 本地新版 CLI | 对方当前源码 | 可借鉴程度 |
| --- | --- | --- | --- |
| 控制方式 | Patchright 驱动创作者网页，由网页上传和提交 | Rod/CDP 驱动创作者网页；MCP/HTTP 是调用入口 | MCP 协议或 Go 语言本身不提供平台授权 |
| 浏览器 | 当时 Patchright 1.58.2 / Chromium 145.0.7632.6 | 内置定制 Chromium，版本文件为 148.0.7778.215，依赖 headless_browser 0.4.0 | 有实质差异，但无双方风控对照实验 |
| 会话 | 每 attempt 启动浏览器，新 context 加载 storage_state；不是完整持久 profile | 每次 publishVideo 同样新建、关闭浏览器；恢复 cookies，另持久保存 seed | 不能说对方天然复用常用 Chrome 完整环境 |
| 登录与检查 | 登录/检查仍走旧模块；额外注入脚本，检查强制无头且允许另一浏览器路径 | 登录与运行共用 NewBrowser 及会话 seed，登录显式有头 | 优先统一本地浏览器工厂和检查方式 |
| 进入创作者页 | 直接进入视频编辑 URL，观察稳定账号响应 | 缺创作者会话时先访问首页，等会话建立，再进编辑页 | 明确的兼容性改进，值得吸收 |
| 输入 | 标题和 Tiptap 正文用 locator.fill 整段填写，再严格回读 | humanize.Type 逐字符 CDP InsertText，有间隔；点击走统一控件检查及鼠标动作 | 可借鉴事件和就绪状态管理；没有“不被识别”证据 |
| 上传完成 | 核对真实分片及完成响应，再检查本地 blob 解码/摘要 | 设置文件输入，等发布控件可用 | 不能以更少核对等同于更安全 |
| 提交确认 | 单次点击、正向回执、列表与独立详情核对；未知不重发 | 主要以 URL 离开发布表单为成功 | 本地提交证据更强，不应退化 |
| 发布后观察 | 已完成任务保留当时结果；当前独立 inspect 未支持小红书 | 本次发布函数返回“发布完成”，没有长期可见性证明 | 两边都不能凭一次成功声称账号安全 |

出处与具体边界如下。

### 1. 定制浏览器与会话连续性

对方 `browser.NewBrowser` 强制使用其分发的内置浏览器；配置指纹功能、语言与品牌，关闭额外的 stealth JS 注入。入口把同一会话文件的 seed 传给登录和运行阶段。它采用不同的底层浏览器方案，不能简单描述成“原版 Chrome 加几个等待”。源码并不能证明定制内核会降低本次账号的处罚概率。[browser.go 50–101](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/browser/browser.go#L50-L101)、[seed.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/configs/seed.go)、[登录入口](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/cmd/login/main.go#L19-L26)。

对方发布服务仍是每次 `newBrowser()` 然后 `defer b.Close()`。其会话文件保存 cookies 和 seed，依赖关闭时清理 launcher；所查链路没有配置固定用户数据目录。**固定 seed 不等于持久保存完整浏览器状态。** 本地 storage_state 也不能等同完整 profile；本地代码未配置 `launch_persistent_context`。[service.go 353–366](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/service.go#L353-L366)、[cookies.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/cookies/cookies.go#L12-L19)、[依赖关闭逻辑](https://github.com/xpzouying/headless_browser/blob/d28e37c7448672550580fca8ff26404aed09a21f/headless_browser.go#L312-L334)、[本地 browser.py 93–154](../../orchestrator/browser.py#L93-L154)。

值得研究的是：一个账号使用稳定、独占、可恢复的真实浏览器 profile，以及登录、检查、上传之间保持一致的配置。它需要配合现有进程回收、账号锁、状态保存与迁移测试，不能直接替换一个 launch 参数就宣称完成。这里是我们的改造建议，不是对方已经具备完整持久 profile 的事实。

发现本地一个更具体的配置分叉：`sau xiaohongshu login/check` 仍调用旧模块；登录和 Cookie 检查会通过 `set_init_script` 注入 `utils/stealth.min.js`，而任务系统调用 `session.open(session_path)` 时没有传该初始化脚本。`cookie_auth` 强制 `headless=True`，并优先使用 `LOCAL_CHROME_PATH`；正式任务使用 `channel="chromium"`。登录完成后，原浏览器还未退出就调用 `cookie_auth`，再启动另一个浏览器检查编辑页。这里能证明代码路径和配置策略不同，不能证明当时配置了另一个浏览器二进制，也不能证明平台因此作出处罚。[CLI 登录入口](../../sau_cli.py#L281-L288)、[Cookie 检查](../../uploader/xiaohongshu_uploader/main.py#L179-L199)、[登录后检查](../../uploader/xiaohongshu_uploader/main.py#L265-L286)、[脚本注入](../../utils/base_social_media.py#L21-L24)、[任务会话初始化](../../orchestrator/platforms/base.py#L124-L132)。

因此，先统一登录、检查、正式发布的浏览器工厂和初始化策略；登录后的校验尽量复用已登录上下文。它比随意叠加更多伪装脚本更可测试，也能减少额外浏览器和编辑页访问。

本机**任务系统**的 11 条相关 runtime 记录均为 `headless=false`，不涵盖上述登录/Cookie 检查；对方服务入口默认反而是 `headless=true`。两次正式发表本来就是有头模式，不能把“正式发布加 --headed”当作本案例的解决方案。[对方 main.go 22](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/main.go#L22)、本地只读运行记录。

### 2. 创作者中心会话初始化：最明确的可借鉴项

2026-09-22 合并的 #855 针对“只有主站登录态，进入发布页后会重置”的具体问题。实现先检查创作者会话，缺失时通过首页建立，再进入编辑器；它解决的是初始化顺序，不是保证免风控。[PR #855](https://github.com/xpzouying/xiaohongshu-mcp/pull/855)、[creator_session.go 19–49](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/xiaohongshu/creator_session.go#L19-L49)。

本地正式适配器直接导航编辑页，虽然会核对稳定账号身份，但缺少显式创作者首页初始化分支。建议补此分支，同时保留身份一致性检查，不只凭 Cookie 名称存在就允许发表。本次两条已通过身份及作品核对，没有证据表明它们当时恰好缺失创作者会话。[本地导航](../../orchestrator/platforms/xiaohongshu_delivery.py#L216-L238)、[身份检查](../../orchestrator/platforms/base.py#L154-L164)。

### 3. 输入和点击：有差异，但随机化不是验证结论

本地 `apply_fields` 调用共享 `_fill`；对方逐字符调用 CDP InsertText，点击前校验可见、可用、落点仍在目标内。对方还有曲线移动与随机停顿。这些差异确实存在，但逐字符 InsertText 并非真实中文输入法，鼠标曲线也不意味着有真人参与。[本地 _fill](../../orchestrator/platforms/dom.py#L126-L131)、[对方 Type](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/humanize/input.go#L247-L279)、[对方 Click](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/humanize/input.go#L107-L132)。

应吸收的是统一输入层、事件正确性、焦点/失焦处理、动态控件就绪检查，以及填写后的准确回读。可以先在本地页面观察 input/change/blur 等事件是否符合编辑器需要，避免字段状态未提交就继续。整段填入也可能是正常粘贴，不能认定 `fill` 一定触发风控。[Playwright 输入语义](https://playwright.dev/python/docs/input#text-input)。

不建议把“随机浏览、自动点赞评论、养号”当成补救；它们增加额外操作。维护者也明确表示不计划自动实现发布前随机浏览、作息模拟，收益难以验证。[维护者 #674 回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/674#issuecomment-5092317636)。

### 4. 发布后核对：保留正确性，减少额外操作

本地正式点击仍由真实页面完成，并非拼造发布接口。上传分片/回执监听本身是被动观察；blob 摘要读取本地浏览器对象，不是再次上传。0.1 秒循环主要是本地 DOM 检查，也不能直接写成“每秒十次平台 API”。[上传与预览](../../orchestrator/platforms/xiaohongshu.py#L213-L261)、[单次提交](../../orchestrator/platforms/xiaohongshu_delivery.py#L248-L264)。

额外操作主要在未知回执后的核对：进入作品列表，再逐个进入候选的编辑详情视图。无回执时最多检查 10 个视频；有回执时按作品 ID 缩小范围。只读模式还会阻断创作者与接口域全部非 GET/HEAD/OPTIONS 请求。[核对链路](../../orchestrator/platforms/xiaohongshu_delivery.py#L216-L291)。

这种阻断防止误保存，但可能影响页面其他初始化请求；是否在本次阻断了平台所需请求，现有资料没有证明。可以改进为更少导航和更准确的只读视图，或者在验证业务副作用之后细分请求策略；**不能为了“像真人”放开未确认的创建、更新、草稿等写请求**。遇到回执不明仍不得重发。

对方 `waitPublishSuccess` 的实际条件只是 URL 不再包含 `/publish/publish`。从代码逻辑看，跳到登录/异常页也可能满足条件；没有在该函数中验证目标成功页和作品。这比我们的回执与作品核对弱，不能照搬为成功标准。[publish.go 421–433](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/xiaohongshu/publish.go#L421-L433)。这是静态代码推断，本次没有在真实账号制造该跳转。

### 5. 定制浏览器的额外成本

对方浏览器从项目自建 CDN 获取，下载时核对 SHA256；已存在的缓存二进制直接复用。源码开放的 Go 包装器与下载的浏览器二进制不是同一个审计对象。不能用“有校验和”推导定制内核无问题，也不能仅凭名称断言恶意。本次未审计其完整内核、可复现构建或二进制行为。[下载实现](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/browser/browser_download.go#L24-L126)。

因此，依赖升级、浏览器一致性和安装可验证性值得借鉴；直接引入整套定制指纹浏览器不是本次推荐的第一步。更不建议更换代理、轮换身份参数来反复试探已经发出警告的账号。

## 本机操作复盘：可以确认什么

下表时间均为 UTC，取实际 attempt 开始和提交边界，不将事件保存时间误当浏览器启动时间。

| 素材 | 本地可确认记录 | 当时最终结果 |
| --- | --- | --- |
| 2.mp4 | 3 个 job、4 个 attempt；一次 processing 超时，一次控件异常，一次准备完成，一次正式发表；另有原 attempt 的只读恢复 | 2026-09-30 13:12:19 提交一次，13:38:40 核对为 PUBLISHED |
| 3.mp4 | 2026-10-01 04:04–04:28 共 6 个 job/attempt；4 次 processing 超时、1 次准备完成、1 次正式发表 | 04:28:37 提交一次，04:28:46 核对为 IN_REVIEW |
| 正式 attempt 总时序 | 2.mp4 从 attempt 开始到提交边界约 15.07 秒；3.mp4 约 16.65 秒 | 包含启动/导航/上传等环节，不能当作纯输入耗时 |
| 运行模式 | 任务系统 11 条浏览器 runtime 记录全部 headless=false，同一驱动及浏览器版本；不包含登录/Cookie 检查 | 不能用“正式发布改有头模式”解释或解决本案例 |

`submit_count=1` 只说明最后发表动作没有重复。它不限制跨任务重新上传、打开编辑器、准备诊断、详情恢复的次数。该数据库也不覆盖全部临时探查脚本，故上表不是完整网络操作次数，更不能说 6 个 attempt 就等于 6 次成功上传。

这些真实操作说明我们的工程验收方式需要改进：准备失败应尽量转成本地可重放故障，不应在同一生产账号上连续试错。**重复会话、重复上传和紧凑操作序列是待验证的候选因素，不是已经确认的风控触发算法。** 现有日志没有完整请求时序、当时出口网络证据或平台判定解释，不能把某个因素写成根因。

## 应优先开发的内容

下列优先级是工程实施建议，不是已经修改的功能，也不是通过平台风控的验收标准。

| 优先级 | 改造 | 本地代码落点 | 验收重点 |
| --- | --- | --- | --- |
| P0 | 独立记录账号风险与发布后审核/可见性；收到行为警告停止后续任务 | inspection.py、store.py、worker.py、平台 capability 文案 | 已提交事实保留；新增风险事件不触发重发；风险暂停不能被换 delivery_key 或重跑成功任务清除 |
| P0 | 按账号与素材统计跨任务操作，限制异常重试；拆开离线验证和线上上传准备 | service.py、worker.py、store.py、任务与 skill 文档 | 多个新 job 仍共享预算；准备失败后不自动形成“换 key → 重传”循环；日志记录上传/导航动作而不保存秘密 |
| P1 | 统一登录、Cookie 检查和发布的浏览器配置；减少登录后的二次开浏览器检查 | sau_cli.py、xiaohongshu_uploader/main.py、browser.py、base_social_media.py | 三条路径使用同一显式浏览器与初始化策略；不因检查入口隐式切换模式/二进制；记录实际运行信息，保持账号锁与清理约束 |
| P1 | 创作者首页初始化、稳定账号复核、页面状态驱动的等待 | xiaohongshu.py / xiaohongshu_delivery.py | 仅主站会话、创作者会话过期、身份不符时在上传前停止；仍共享总截止时间 |
| P1 | 统一编辑器输入层与明确的字段提交状态 | dom.py 及小红书适配器 | 本地页面验证事件、换行、焦点、动态覆盖层、回读；不靠堆随机 sleep 验收 |
| P1 | 降低核对所需页面跳转，复用已经观察到的唯一回执和详情 | xiaohongshu_delivery.py、base.py | 保留提交前落盘、单次点击、未知不重发；有回执只核对对应作品；读取不产生业务写入 |
| P2 | 单账号持久浏览器 profile 的可选运行模式 | browser.py、会话与账号维护模块 | 登录到发表配置一致；跨进程互斥、崩溃恢复、清理期限、迁移失败均有明确行为；不伪造环境或轮换身份 |

当前 `orchestrator/inspection.py:45` 只接纳抖音与视频号，小红书的独立“发表后再检查”入口尚未开放；`store.finish_delivery` 保存的是当时一次核对结果。这是本次暴露出的具体功能缺口。应该把“已提交”“审核状态”“分发/可见性”“账号风险”分开，保留观察时间及来源；没有可观察证据的项保持 UNKNOWN，不能从播放量低或列表中存在就推断限流/正常。[inspection.py](../../orchestrator/inspection.py#L39-L47)、[store.py](../../orchestrator/store.py#L1015-L1045)。

线上重新验收的前提应是明确处理已有通知及限制，并另行确定测试边界；本研究没有安排自动回访或继续发表。延后可见性检查也应按需、有界，不以大量查询来证明“安全”。不存在本研究可以给出的可靠安全发帖数、随机等待秒数或保证解除限制的冷却时长。

## 版本时间线与证据限制

- 2026-07-20：[PR #737](https://github.com/xpzouying/xiaohongshu-mcp/pull/737) 加入内置浏览器、humanize 与提交后结果检查。PR 中的功能测试不是大样本账号风险验证。
- 2026-07-26：[PR #755](https://github.com/xpzouying/xiaohongshu-mcp/pull/755) 固定会话 seed；同期还有输入和点击修正。
- 2026-07-27/28：维护者称测试账号若干天正常，同时承认风险。#777 的警告报告晚于上述合并，但所用版本及环境没有严格钉定。
- 2026-09-22：[PR #855](https://github.com/xpzouying/xiaohongshu-mcp/pull/855) 修正创作者会话初始化。
- 2026-09-30/10-01：本地两份视频技术交付验收。
- 2026-10-09：用户反馈两条被屏蔽并提供行为风控通知；本次进行只读研究。

此前 Issue 不能证明最新 HEAD 必然失败，几天无异常也不能证明最新 HEAD 长期无风险。本次没有同账号、同内容、同网络条件下的受控比较，且这类线上实验本身会影响账号，不能为了证明某个猜测反复发布。

建议结论是：**统一登录、检查和发布的浏览器配置，吸收会话初始化、输入层组织和页面就绪处理；优先补齐本地跨任务重试约束与发布后风险状态；保留现有强提交证据。暂不把换项目、定制浏览器或拟人化参数当作恢复全自动生产的依据。**
