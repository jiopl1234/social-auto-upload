# 三平台无人值守发布：任务拆分草稿

状态：用户已批准 15 张票的粒度和依赖；仅待选择 tracker。共 15 张票，均已形成独立文件；尚未发布到 issue tracker，未应用 ready-for-agent。

依据：已确认的测试边界为统一 CLI/任务接口，平台适配器补充本地页面测试。沿用现有规格的五条内容路径、单机执行、无官方发布 API、常态无人参与和挑战停止的范围。

## 已批准的拆分

1. **[干净安装后可运行三平台 CLI，并恢复旧参数语义](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/01-runnable-cli-and-compatibility.md)**
   - **Blocked by:** 无，可立即开始。
   - **What it delivers:** 使用者按主线依赖安装并初始化配置后，可以运行三平台命令；已有命令与受支持的 Python 位置参数保持原意。先修当前可复现的安装、兼容和测试基线，再开始任务系统改造。

2. **[提交清单后获得可持久查询、可去重的单平台任务](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/02-durable-job-intake-and-query.md)**
   - **Blocked by:** 01。
   - **What it delivers:** 使用者通过公共 CLI 提交一项平台交付，获得 job_id，并在重启后继续查询；重复交付键返回原任务，同键不同清单被拒绝。首次交付只入队，不触发平台写入。

3. **[串行执行任务，同账号只允许一个写入者](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/03-single-writer-job-execution.md)**
   - **Blocked by:** 02。
   - **What it delivers:** 使用者启动 worker 后，待执行任务进入一次可查询的执行流程；多个进程竞争时仍保持账号单写入，维护会话也遵守相同锁，受阻账号不拖住其他账号。

4. **[发布前验证不可变清单、素材版本和声明事实](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/04-immutable-content-preflight.md)**
   - **Blocked by:** 03。
   - **What it delivers:** 使用者的标题正文、素材、声明及降级策略先形成不可变清单；入队和执行时发现无效或被替换的输入，会返回字段错误且不产生平台提交。

5. **[提交后失联或崩溃时只核对结果，不重新发布](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/05-submission-boundary-and-recovery.md)**
   - **Blocked by:** 03。
   - **What it delivers:** 使用者能在任务中断后恢复查询，系统区分提交前失败与可能已经提交；后者只读核对远端结果，无法确认时保留 UNKNOWN_COMMIT。

6. **[任务超时或取消后准时退出，并释放浏览器资源](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/06-bounded-runtime-and-cancellation.md)**
   - **Blocked by:** 05。
   - **What it delivers:** 使用者能够取消或限制运行时间；页面卡住、会话创建失败及资源关闭异常时，任务仍有确定结果，下一项任务能继续运行。平台接入复用这一受测执行行为。

7. **[抖音视频完成准确填写、单次提交和作品核对](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/07-douyin-video-safe-delivery.md)**
   - **Blocked by:** 04、06。
   - **What it delivers:** 使用者提交抖音视频后，系统验证账号与素材、准确应用内容和声明，最多提交一次，并根据可信作品证据返回结果。

8. **[抖音图文复用可靠任务流程，并确认全部图片](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/08-douyin-note-safe-delivery.md)**
   - **Blocked by:** 07。
   - **What it delivers:** 使用者能够用同一任务接口发布抖音图文，准确保留图片、文字和声明，并获得与视频相同的防重发和结果核对能力。

9. **[小红书视频准确声明来源，并单次提交核对作品](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/09-xiaohongshu-video-safe-delivery.md)**
   - **Blocked by:** 04、06。
   - **What it delivers:** 使用者发布小红书视频时，来源声明、话题和封面与清单一致；系统在素材完成处理后单次提交，并核对目标账号作品。

10. **[小红书图文确认图片和文案后完成可追溯发布](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/10-xiaohongshu-note-safe-delivery.md)**
   - **Blocked by:** 09。
   - **What it delivers:** 使用者发布小红书图文时，全部图片和元数据经过核对，且提交、作品查询、挑战停止与重启恢复具有一致语义。

11. **[视频号视频正确处理声明、实名验证和发布状态](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/11-channels-video-safe-delivery.md)**
   - **Blocked by:** 04、06。
   - **What it delivers:** 使用者发布视频号视频时，系统准确设置内容与声明，实名验证出现即停止；只有可靠作品证据才能报告已发表。

12. **[按显式时区到时启动任务，重启后不重复触发](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/12-timezone-aware-local-scheduling.md)**
   - **Blocked by:** 05。
   - **What it delivers:** 使用者用明确时区设置本地执行时间，worker 在到期后启动一次任务，重启仍保持原计划和交付边界。

13. **[一份清单分发到多个平台，部分失败可独立追踪](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/13-multi-platform-fanout.md)**
   - **Blocked by:** 04、05。
   - **What it delivers:** 使用者一次选择多个目标平台，获得逐平台任务及汇总视图；某个平台失败或暂停时，其他平台照常执行，重试不会重发已成功的目标。

14. **[按任务追踪失败证据，同时保护凭据和会话](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/14-safe-diagnostics-and-session-storage.md)**
   - **Blocked by:** 06。
   - **What it delivers:** 使用者能够按任务查看阶段、错误、结果证据与运行版本，而无需泄漏登录凭据；会话更新中断不会破坏可恢复状态。

15. **[五条发布路径通过共同验收，并明确生产验证状态](C:/Users/Chinese/social-auto-upload/docs/ticket-drafts/unattended-publishing/15-five-path-release-acceptance.md)**
   - **Blocked by:** 08、10、11、12、13、14。
   - **What it delivers:** 维护者获得可重复执行的验收结果和逐平台能力清单，知道哪些路径已完成离线验证、哪些仍缺真实平台证据，并能明确判断首版是否满足上线门槛。

## 依赖与粒度说明

- 01 先修安装与兼容基线，后续票在此基础上保持可运行；没有单独的大范围横向重构票。
- 02 交付可持久入队/查询；03 交付真正的任务执行与账号单写入；04 交付失败前阻断无效内容；05 交付提交不明的恢复。各票从公共接口到状态/结果都有可验收行为。
- 06 在平台接入前完成可复用的有界执行与资源回收，避免五条路径各实现一套生命周期。
- 07、09、11 只依赖共享行为，不互相阻塞；08、10 分别复用所属平台已经建立的身份与作品查询能力。
- 12、13、14 可通过受控目标独立验证，无需等全部真实平台接入完成。平台模拟目标仅用于测试，不能作为可用真实平台对用户返回成功。
- 15 是跨功能整合与验证状态交付，不承担其他功能票本应随实现提供的测试，也不暗中新增平台预约、视频号图文或海外平台。
- 首版票不包括平台后台预约及完整 persistent profile 迁移。源审计中可延期部分继续保持范围外。
- 所列边为直接阻断关系，省略已有传递依赖。初始 frontier 为 01；某票只有在全部直接阻断票完成后才可领取。
- 每张票的正文避免具体代码文件路径和代码片段；精确源码证据继续由审计报告承载。

## 批准记录与发布位置

用户已明确回复“认可这 15 张票及依赖（推荐）”，粒度和阻断关系不再重复请求确认。

发布位置尚待选择：本地 Markdown tracker，或用户指定的 GitHub 仓库。当前确认不视为授权向第三方上游公开发 Issue。

选定并配置 tracker 后，按已批准的拆分一票一文件/Issue 发布，并应用 ready-for-agent；该标签不表示阻断关系已满足，也不表示当前实现生产可用。源规格保持原状。
