# 新版任务 CLI

从仓库根目录使用 `uv run --frozen --offline --no-sync sau`，或相同安装环境里的 `sau`。`--db`、`--runtime-dir`、`--media-root`、`--json` 在动作名称前。`--media-root` 是明确允许读取素材的目录，可重复指定。

```powershell
$taskDb = 'C:/Users/Chinese/.sau/tasks.sqlite3'
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json capabilities
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json accounts
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json list
uv run --frozen --offline --no-sync sau jobs --db $taskDb --media-root C:/Users/Chinese/Videos --json enqueue --manifest content.json
uv run --frozen --offline --no-sync sau jobs --db $taskDb --media-root C:/Users/Chinese/Videos --json run --job JOB_ID --headed
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json status JOB_ID
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json attempts JOB_ID
```

## 清单

示例只演示当前支持的原创、无 AI 视频。`facts` 必须对应当前用户声明；不要为了通过预检填写未知事实。`delivery_key` 表示一次明确交付意图，同键同输入复用原任务；同键不同输入返回冲突。媒体/标题/正文在入队后冻结。

```json
{
  "schema_version": 1,
  "delivery_key": "my-next-original-video-001",
  "platform": "tencent",
  "account": "channels_second",
  "content_type": "video",
  "title": "新作品的短标题",
  "body": "新作品的单段正文。",
  "tags": [],
  "media": ["C:/Users/Chinese/Videos/next.mp4"],
  "facts": {"ownership": "original", "ai_usage": "none", "repost_source": null},
  "declarations": {},
  "field_policy": {
    "required": ["title", "body", "media"],
    "optional": ["tags", "cover", "collection"],
    "allow_omit": []
  }
}
```

详细字段、显式预约 `run_at` 和多目标清单以 [docs/tasks.md](../../../docs/tasks.md) 为准。预约是任务调度能力，不能据此宣称平台原生定时界面已验收。仅准备时使用指定 job 的 `--prepare-only`；它占一次尝试。`--max-attempts` 为 1–5、默认 2，准备后正式发表应留出尝试额度。完整首发验收可用 `--max-attempts 1`，不要添加仅准备尝试再称作首次全路径成功。

## 状态和证据

| 结果 | 应如何报告 |
|---|---|
| QUEUED | 已入队，没有发表 |
| READY / NOT_SUBMITTED | 准备完成，没有发表 |
| COMPLETED / CONFIRMED | 回执/允许的正向确认及独立作品核对完成；同时报告 platform_result |
| IN_REVIEW | 平台明确显示审核中 |
| ACCEPTED | 平台已接收作品，未证明审核通过或分发 |
| UNKNOWN_COMMIT / MAY_HAVE_SUBMITTED | 可能已经发表；只对原 job 执行只读恢复 |
| BLOCKED 或账号暂停 | 查看 error/pause_reason，处理真实原因再按提交状态恢复 |

成功退出码不能单独证明发表。`FOUND` 的独立查询也不能制造该任务缺失的创建回执。通过 `attempts` 汇总实际 `submit_count`，保留同 job/attempt 关系。

`inspect-work` 当前只支持 `douyin` 和 `tencent`。小红书通过原任务的详情核对/只读恢复取得证据，不能编造 `inspect-work --platform xiaohongshu` 命令。

```powershell
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json inspect-work --platform tencent --account channels_second --content-id '实际作品ID' --headed
```

视频号正向回复可能没有作品 ID。保存同 attempt 的 HTTP 201、`errCode=0`、`data.baseResp.errcode=0`，`receipt_id=null`；独立完整列表与详情唯一匹配后才完成，不能把查到的 ID 伪装为创建回执。小红书转换后的媒体 ID 与上传 ID 可不同，按正式端口的原视频 MD5 证据核对；抖音需要同 attempt 回执和上传 Vid。不要手工放宽这些匹配要求。
