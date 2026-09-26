# 三平台无人值守发布：实施索引

Tracker: local Markdown

规格和 15 张票已按用户批准的拆分发布。ready-for-agent 表示规格可实施；只有前置票均为 done 才能领取。

| 票号 | 任务 | 状态 |
|---|---|---|
| 01 | [干净安装后可运行三平台 CLI，并恢复旧参数语义](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/01-runnable-cli-and-compatibility.md) | done |
| 02 | [提交清单后获得可持久查询、可去重的单平台任务](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/02-durable-job-intake-and-query.md) | in-progress |
| 03 | [串行执行任务，同账号只允许一个写入者](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/03-single-writer-job-execution.md) | ready-for-agent |
| 04 | [发布前验证不可变清单、素材版本和声明事实](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/04-immutable-content-preflight.md) | ready-for-agent |
| 05 | [提交后失联或崩溃时只核对结果，不重新发布](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/05-submission-boundary-and-recovery.md) | ready-for-agent |
| 06 | [任务超时或取消后准时退出，并释放浏览器资源](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/06-bounded-runtime-and-cancellation.md) | ready-for-agent |
| 07 | [抖音视频完成准确填写、单次提交和作品核对](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/07-douyin-video-safe-delivery.md) | ready-for-agent |
| 08 | [抖音图文复用可靠任务流程，并确认全部图片](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/08-douyin-note-safe-delivery.md) | ready-for-agent |
| 09 | [小红书视频准确声明来源，并单次提交核对作品](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/09-xiaohongshu-video-safe-delivery.md) | ready-for-agent |
| 10 | [小红书图文确认图片和文案后完成可追溯发布](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/10-xiaohongshu-note-safe-delivery.md) | ready-for-agent |
| 11 | [视频号视频正确处理声明、实名验证和发布状态](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/11-channels-video-safe-delivery.md) | ready-for-agent |
| 12 | [按显式时区到时启动任务，重启后不重复触发](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/12-timezone-aware-local-scheduling.md) | ready-for-agent |
| 13 | [一份清单分发到多个平台，部分失败可独立追踪](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/13-multi-platform-fanout.md) | ready-for-agent |
| 14 | [按任务追踪失败证据，同时保护凭据和会话](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/14-safe-diagnostics-and-session-storage.md) | ready-for-agent |
| 15 | [五条发布路径通过共同验收，并明确生产验证状态](C:/Users/Chinese/social-auto-upload/.scratch/unattended-publishing/issues/15-five-path-release-acceptance.md) | ready-for-agent |

当前 frontier：02。01 已完成安装、兼容和测试基线，并通过双线审查；其余 14 张票尚未实现。每票验收结果以其自身记录为准。

01 的证据：[验证记录](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-01-validation.md)、[审查记录](C:/Users/Chinese/social-auto-upload/docs/audit/ticket-01-review.md)。未进行真实账号发布测试，整个方案尚未达到无人值守生产验收状态。

历史草稿继续留存供审计；本目录为正式跟踪位置。
