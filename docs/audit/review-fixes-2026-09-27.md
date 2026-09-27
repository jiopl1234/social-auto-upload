# 全票审查补充修复

审查基线：`0012d2c...064f7ff`；本轮修复从 `064f7ffe2b188e7d8233c883a25045933ab43f85` 开始。用户报告 Standards 1 项 P2、Spec 4 项（最高 P1）；会话读取问题在两轴重复计入，共四个独立根因。

修复提交：`ed915a4f41f361f2f14cc75daa714ddd8951c2dc`；复审补修：`9b16beda3b0b0a7ea6642ef8bbdd98684c2cc9d4`。两位只读审查者分别核对 `064f7ff...ed915a4` 的 Standards 与 Spec，并独立复核 `ed915a4...9b16bed`；验收从固定提交的独立归档运行，不使用工作区未提交的文件。

## 修复与回归范围

- 批量取消：`run_pending` 在当前任务持久化取消结果后结束本轮，包括取消作为附加警告的情况。下一任务保持 QUEUED 且没有 attempt；显式重新运行仍能处理剩余任务。回归覆盖准备、远端写入后等待、清理以及已有主要错误时的清理取消。首个测试在旧实现返回两个任务，修复后运行时测试 18 项通过。
- 重复投递：先比对持久化原始请求，再做新的会话/素材校验。schema v4 同事务保存原始输入及其指纹和规范化素材快照；并发插入仍复核唯一键和请求。素材移走的 CLI 重放在旧实现为 INVALID_INPUT，修复后返回原 job；变更请求返回 CONFLICT。旧 schema v3 的快照可按已保存路径与内容识别重放，移除素材、封面和会话的回归也通过。CLI、迁移、素材、分发和交付相关 90 项通过。
- 会话读取：读取和验证移入受父进程存活监护、可终止的子进程；向浏览器传入已验证的内存快照，避免驱动再次打开文件。验证慢读/永久等待时的预算、取消、子进程退出与资源状态。
- 提交后挑战：等待回执期间检查认证挑战，回执与挑战同时出现时保持暂停；导航查询前再次检查现有编辑器，避免跳转清除提示。提交边界已经跨过时仍保持 UNKNOWN_COMMIT，账号暂停，后续排队任务不写入。

## 兼容性与限制

对外结果 JSON 版本仍为 1，既有 job_id、清单摘要、尝试与事件不改写。旧数据库升级在事务内完成，旧程序会拒绝打开 schema v4；需要回退程序时应使用升级前数据库备份。旧记录未保存原始路径别名，只有其已保存规范路径可可靠比对，无法还原的别名返回冲突。相同原请求的素材后来变化不创建新任务，真正执行时仍按旧快照校验并拒绝变化。

本轮只使用真实 SQLite、受控子进程和本地页面，没有真实后台访问或真实发布。五路径生产能力继续 restricted。

## 定向回归

- CLI、持久入队、迁移、素材预检、分发、交付与执行器取消：108 passed，57.36 秒。
- 浏览器生命周期、预检进程与安全诊断：39 passed，35.71 秒。
- 五路径平台、图文与联合交付场景：73 passed，279.03 秒。
- 核心、CLI 及全部改动 Python 测试/支持文件的 Pyright：0 errors、0 warnings、0 informations。

这些集合有重叠，不将其相加作为全仓测试数。新增回归包括缺失或同时到达的回执、导航前验证、忽略取消的回执观察者和延迟异常；观察者无法停止时保留账号资源阻断，异常不输出页面细节。

## 干净环境完整验收

固定提交 `ed915a4` 经 `git archive` 导出；新建虚拟环境，仅从 `conf.example.py` 创建配置，未复制用户配置或会话。`uv sync --frozen --offline --group dev` 安装 27 个锁定依赖，再通过 `scripts/check_offline.py` 的统一入口运行。

- 环境：Windows、Python 3.12.10、Patchright 1.58.2、Chrome 152.0.7977.83、pytest 9.1.1、pytest-timeout 2.4.0、Pyright 1.1.414。
- 全部测试：366 passed、20 subtests passed，448.84 秒；0 failures、0 errors、0 skipped。JUnit 将子测试一起计为 386。
- 核心及 CLI 类型检查：0 errors、0 warnings、0 informations；统一入口退出码 0。
- 原始证据：[环境](review-fixes-2026-09-27-clean/environment.txt)、[测试](review-fixes-2026-09-27-clean/offline-pytest.txt)、[JUnit](review-fixes-2026-09-27-clean/offline-pytest.xml)、[类型检查](review-fixes-2026-09-27-clean/offline-pyright.txt)。

## Standards

对 `ed915a4` 的独立只读复审发现一项 P2 契约违规：首项正常完成、下一项尚未开始时请求取消，下一次 `run_once` 会在收到取消异常前先领取任务，与 `docs/tasks.md` 的“不再领取下一项”冲突。定向复现得到首项 COMPLETED、第二项 CANCELLED 且已有一次 attempt。其余改动未发现明确契约违规或值得单列的代码异味。

`9b16bed` 在每次领取前交付待决取消，返回已保存的结果。新增公共接口回归先失败再通过，验证第二项仍为 QUEUED、无 attempt，且同一异步任务随后显式恢复仍能执行。原审查者复核确认 P2 已关闭，小改未见新问题；未重复全仓测试。结论限于当前源码、契约和本地定向场景，不构成真实平台验证。

## Spec

独立只读复审未发现可行动问题：四项修复均符合规格中重复投递、单次提交与挑战停止、取消及预算要求（`spec.md` 第 53–54、57、67、92、102、110、112 行）。未见范围膨胀或看似实现、实际错误的行为。历史 v1–v3 任务的符号链接别名无法从旧快照还原，已明确记录此限制，不作为本次新回归。

`9b16bed` 的增量复审同样通过：领取前的取消检查阻止第二项被提前领取，公共接口测试覆盖同一异步任务显式恢复执行，符合规格和任务接口说明；无新发现。审查基于当前源码与本地合成测试，没有将离线结果扩展为真实平台验证。

两轴结果：Standards 1 项 P2 已修复并复核关闭、0 项未关闭；Spec 0 项发现。

后续用户复审 `064f7ff...61030dd` 发现遗漏的 NOT_DUE 延期清理取消场景，上述结论仅记录当时审查范围，不能据此认为该场景已验证。修复提交 `8a12e9e` 及补充回归、复审证据见 [定时延期保留取消信号](deferred-cancellation-fix-2026-09-27.md)。

## 复审补修的影响验证

`9b16bed` 相比已通过完整验收的 `ed915a4` 只增加领取前的取消让出点及其回归测试。代码图 Tier 2 的 `TaskService.run_pending` 入向一层查询返回 21/21 个调用点，无截断；据此选择运行时、定时、分发、任务交付、任务执行器与联合发布验收。另建归档与全新虚拟环境运行这些场景，不重跑不受这处变化影响的全套。

定向复验：99 passed，101.20 秒，0 failures、0 errors、0 skipped；核心、CLI 与新增测试的 Pyright 为 0 errors、0 warnings、0 informations。命令包含 `tests/test_runtime_limits.py`、`test_scheduling.py`、`test_fanout.py`、`test_job_delivery.py`、`test_job_worker.py`、`test_release_acceptance.py`，退出码 0。因此 366 项全套结果对应 `ed915a4`，99 项补修验证对应 `9b16bed`，两者不混称同一次完整运行。

原始补修证据：[环境与提交](review-fixes-2026-09-27-followup/environment.txt)、[测试输出](review-fixes-2026-09-27-followup/pytest.txt)、[JUnit](review-fixes-2026-09-27-followup/pytest.xml)、[类型检查](review-fixes-2026-09-27-followup/pyright.txt)。用户报告的四个独立根因和复审补充的取消交接边界均已关闭。

代码图项目为 `C-Users-Chinese-social-auto-upload`。初审 generation `2026-09-27T07:26:02Z`；补修前 coverage generation `2026-09-27T07:37:18Z`。改动路径没有记录覆盖缺口，但 freshness 为 metadata_changed，已直接读取当前源码兜底；调用图存在同名方法的启发式误归因，已按源码确认，图结果不作为完整性的证明。
