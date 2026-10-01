# 抖音 CLI 契约

登录/检查：

```powershell
uv run --frozen --offline --no-sync sau douyin login --account douyin_main --headed
uv run --frozen --offline --no-sync sau douyin check --account douyin_main
```

`valid` 只证明会话检查通过。[由 agent 在线识别 ID 并绑定](../../social-auto-upload/references/accounts.md)后，使用[共用任务命令和 UTF-8 清单](../../social-auto-upload/references/task-cli.md)。清单 platform 为 `douyin`，正文字段是 `body`，不是旧命令的 desc/note。

```powershell
$taskDb = 'C:/Users/Chinese/.sau/tasks.sqlite3'
uv run --frozen --offline --no-sync sau jobs --db $taskDb --media-root C:/Users/Chinese/Videos --json enqueue --manifest content.json
uv run --frozen --offline --no-sync sau jobs --db $taskDb --media-root C:/Users/Chinese/Videos --json run --job JOB_ID --headed
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json status JOB_ID
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json attempts JOB_ID
```

同一清单重复 enqueue 返回原任务；同键不同内容冲突。需要仅准备时给 run 加 `--prepare-only`，它占一次尝试。`--max-attempts` 默认 2、范围 1–5。未知提交恢复保持原 job/attempt。旧 upload-video/upload-note 参数只保留解析兼容，不能用于真实发表。不要把旧 `--schedule` 参数转抄到 jobs run；预约清单以 docs/tasks.md 为准。

当前有限范围为原创、无 AI 的视频、标题/正文和立即公开发表，支持范围以 capabilities 和 docs/douyin-video.md 为准。图文真实发布仍 restricted。完整 CLI 验收缺口仍在能力报告中；不能把某次人工验证后的只读恢复当作首轮自动续行已验收。

最新范围、回执和媒体证据见[项目使用说明](../../../docs/douyin-video.md)及当前 `jobs --json capabilities`。
