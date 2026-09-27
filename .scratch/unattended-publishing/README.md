# 三平台无人值守发布：实施索引

Tracker: local Markdown

规格和 15 张票已按用户批准的拆分发布，实施与离线验收全部完成。逐票 done 表示已达到约定的离线交付标准；真实平台生产验证仍未完成。

| 票号 | 任务 | 状态 |
|---|---|---|
| 01 | [干净安装后可运行三平台 CLI，并恢复旧参数语义](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/01-runnable-cli-and-compatibility.md) | done |
| 02 | [提交清单后获得可持久查询、可去重的单平台任务](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/02-durable-job-intake-and-query.md) | done |
| 03 | [串行执行任务，同账号只允许一个写入者](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/03-single-writer-job-execution.md) | done |
| 04 | [发布前验证不可变清单、素材版本和声明事实](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/04-immutable-content-preflight.md) | done |
| 05 | [提交后失联或崩溃时只核对结果，不重新发布](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/05-submission-boundary-and-recovery.md) | done |
| 06 | [任务超时或取消后准时退出，并释放浏览器资源](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/06-bounded-runtime-and-cancellation.md) | done |
| 07 | [抖音视频完成准确填写、单次提交和作品核对](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/07-douyin-video-safe-delivery.md) | done |
| 08 | [抖音图文复用可靠任务流程，并确认全部图片](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/08-douyin-note-safe-delivery.md) | done |
| 09 | [小红书视频准确声明来源，并单次提交核对作品](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/09-xiaohongshu-video-safe-delivery.md) | done |
| 10 | [小红书图文确认图片和文案后完成可追溯发布](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/10-xiaohongshu-note-safe-delivery.md) | done |
| 11 | [视频号视频正确处理声明、实名验证和发布状态](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/11-channels-video-safe-delivery.md) | done |
| 12 | [按显式时区到时启动任务，重启后不重复触发](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/12-timezone-aware-local-scheduling.md) | done |
| 13 | [一份清单分发到多个平台，部分失败可独立追踪](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/13-multi-platform-fanout.md) | done |
| 14 | [按任务追踪失败证据，同时保护凭据和会话](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/14-safe-diagnostics-and-session-storage.md) | done |
| 15 | [五条发布路径通过共同验收，并明确生产验证状态](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/15-five-path-release-acceptance.md) | done |

当前 frontier：无，01–15 及后续补充修复完成。`8a12e9e` 修复 NOT_DUE 清理取消信号丢失，干净环境完整验收为 369 项测试与 20 项子测试通过、0 skipped；Pyright 零错误，独立 Standards / Spec 复审均无新发现。详见 [定时延期取消修复](C:/Users/Chinese/social-auto-upload/docs/audit/deferred-cancellation-fix-2026-09-27.md)。此前 366 项全套与 99 项影响范围结果保留为历史证据。

2026-09-27 补充审查已关闭：基线 0012d2c...064f7ff 发现的取消、提交后挑战、重复入队与会话读取四类问题均已修复，复审补充的取消交接边界也已修复。代码提交 ed915a4、9b16bed；Standards / Spec 均无未关闭项。历史 342 项结果保留为当时基线；各次提交与测试范围见 [补充修复记录](C:/Users/Chinese/social-auto-upload/docs/audit/review-fixes-2026-09-27.md)。

最终证据：[五路径联合验收](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-15-validation.md)、[独立双轴审查](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-15-review.md)。五路径均已通过本地任务协议与合成页面验证；真实平台账号身份、逐素材状态和作品读回没有实测证据，生产执行保持 restricted。

04/05 的证据：[验证记录](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-04-05-validation.md)、[审查记录](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-04-05-review.md)。完整离线测试为 158 项与 20 项子测试通过；真实平台发布仍未验证。

01 的证据：[验证记录](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-01-validation.md)、[审查记录](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-01-review.md)。未进行真实账号发布测试，整个方案尚未达到无人值守生产验收状态。

历史草稿继续留存供审计；本目录为正式跟踪位置。

06/12/13 的证据：`docs/audit/ticket-06-12-13-validation.md`、`docs/audit/ticket-06-12-13-review.md`。完整离线测试为 236 项与 20 项子测试通过；真实平台发布仍未验证。
