# 小红书视频 CLI 使用与验收

当前视频路径为 `available_limited`，支持准备、一次公开发表和只读详情恢复。真实 CLI 已提交 `2.mp4` 和 `3.mp4`，后者的实际正向创建回执已捕获并通过 CLI 恢复到原 attempt。能力版本为 `1.2.1`，`can_prepare=true`、`can_execute=true`、`live_receipt_verified=true`。`3.mp4` 首轮使用旧解析器完成详情确认，随后补齐真实 `success=true/result=0/data.id` 格式并恢复已保存回执；新版解析器在同次发表内直接持久化回执的路径仍只有离线验证，因此 `production_verified=false`、`missing_evidence=["live_inline_receipt_persistence"]`。图文仍受限；视频号的有限范围见[视频号说明](channels-video.md)。

范围：一个不超过 32 MiB 的原创、非 AI 视频，标题最多 20 个 UTF-16 单位，正文最多 1000 个 UTF-16 单位；空话题、无指定封面/合集/声明。页面的公开可见、定时发布关闭、原创声明关闭、内容类型声明为空、PK 封面关闭以及默认第一帧封面提示均须核对。原创事实与可选的“原创声明”开关是不同输入，本版本不替使用者选择声明。

## 登录与绑定

```powershell
uv run --frozen --offline --no-sync sau xiaohongshu login --account xhs_main --headed
uv run --frozen --offline --no-sync sau xiaohongshu check --account xhs_main
```

`valid` 仅表示登录页面检查通过，不自动建立任务账号绑定。绑定的 `expected-id` 使用真实创作者页面 `/api/galaxy/user/info` 响应的 `data.userId`（24 位小写十六进制），不能使用昵称或 `redId`。当前 xhs_main 实测账号已绑定到独立验收数据库；普通任务数据库仍需单独绑定。

```powershell
uv run --frozen --offline --no-sync sau jobs bind-account --platform xiaohongshu --account xhs_main --expected-id "<真实 userId>" --session-source C:/Users/Chinese/social-auto-upload/cookies/xiaohongshu_xhs_main.json
```

## 创建任务与准备

保存 UTF-8 JSON 清单，替换素材绝对路径和内容；清单的字段契约见 [任务说明](tasks.md)。

```json
{
  "schema_version": 1,
  "delivery_key": "xhs-new-original-video-001",
  "platform": "xiaohongshu",
  "account": "xhs_main",
  "content_type": "video",
  "title": "替换为新视频的标题",
  "body": "替换为新视频的正文",
  "tags": [],
  "media": ["C:/Users/Chinese/Videos/new-video.mp4"],
  "facts": {"ownership": "original", "ai_usage": "none", "repost_source": null},
  "declarations": {},
  "field_policy": {
    "required": ["title", "body", "media"],
    "optional": ["tags", "cover", "collection"],
    "allow_omit": []
  }
}
```

```powershell
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos enqueue --manifest C:/Users/Chinese/social-auto-upload/xhs-manifest.json
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos run --job "<返回的 job_id>" --prepare-only --headed --max-attempts 1
```

成功结果为 `READY / NOT_SUBMITTED`，`actual_metadata` 来自页面真实回读。仅准备任务在结束后关闭浏览器，不点击发表或暂存草稿；它不能转为真实发布任务。需要发表时，使用新的 delivery_key 建立正式任务。

## 公开发表与恢复

正式清单使用新素材、准确事实和独立 delivery_key。入队后省略 `--prepare-only`：

```powershell
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos enqueue --manifest C:/Users/Chinese/social-auto-upload/xhs-publish.json
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos run --job "<正式 job_id>" --headed --verification-wait 300 --max-attempts 1
uv run --frozen --offline --no-sync sau jobs status "<正式 job_id>"
uv run --frozen --offline --no-sync sau jobs attempts "<正式 job_id>"
```

`--verification-wait` 默认关闭，只允许指定的首次视频发布与可见浏览器；如出现本人验证，暂停账号并保留原页面供本人处理，持续共享阶段/总截止时间。完成验证后只观察原发表动作，不能再次点击发表。

进入 `SUBMITTING` 后，任何不确定结果均禁止重新发表。对 `UNKNOWN_COMMIT` 再执行原 job（不加 `--verification-wait`）只读检查作品，沿用原 attempt；不得换 delivery_key 来重发。成功结果为 `COMPLETED / CONFIRMED`，`platform_result` 来自真实详情与列表，不把上传完成或 HTTP 200 当作发表成功。

小红书详情的 `video_info.file_id` 是规范作品素材 ID，可能不同于临时上传 Key。核对 `video_info.media_v2.video.md5` 与发表前持久保存的源文件 MD5，并同时核对稳定作者、完整标题正文、公开状态、无话题/提及/定时及发表时间。远端返回原始 MD5，不能伪称远端 SHA256，也不能用转码后文件大小代替源素材证明。

上传分片大小由平台动态选择，实际观察过 2/3/5 MiB。CLI 按同一上传事务中实际编号、长度及各片 SHA256/MD5，通过可终止子进程顺序核对源文件；要求完整连续分片、总长度相同和正向完成回执。最多 64 片、总文件 32 MiB，不转码素材来掩盖协议不兼容。

有回执时必须匹配该 ID；没有回执时必须取得完整的全部作品首屏（`tab=0&page=0`、响应 `page=-1`、全部数量与行数一致），读取所有视频候选详情且唯一匹配。当前限定完整首屏最多 100 条、其中视频最多 10 条；详情缺字段或多个匹配均保持不明。新任务在发表前保存 MD5，恢复不依赖源文件；旧任务若缺 MD5，只能通过可终止子进程重新读取并验证原 SHA256 后补充，源文件变化或缺失时保留不明。

本次 `2.mp4` 已完成发表，不需再次入队。验收数据库为 `.scratch/xiaohongshu-live-2026-09-30/acceptance.sqlite3`，正式任务 ID 为 `9d794195cb124adaa66902a692398631`。查询时显式传入此数据库，与默认任务库区分。

`3.mp4` 也已提交，任务 ID 为 `5c53125b50c9478586f2a24ef6adc954`。最后独立核对时为 `IN_REVIEW`；这表示平台接受并进入审核，不能写成已审核通过。若旧解析器没有保存 attempt 的回执 ID，但同一 attempt 保留了实际正向创建响应，且作品已独立确认，可运行本地回执恢复：

```powershell
uv run --frozen --offline --no-sync sau jobs --json --db .scratch/xiaohongshu-live-2026-09-30/acceptance.sqlite3 recover-receipt 5c53125b50c9478586f2a24ef6adc954
```

此命令持有账号维护锁，仅支持已 `COMPLETED/CONFIRMED` 的小红书视频，以及唯一的真实 `success=true/result=0/data.id` 响应。回执必须匹配已确认作品和原 attempt；缺少响应、负向/冲突 ID 或未知状态均拒绝。它只补记回执及来源，不打开浏览器、不变更发表计数或核对时间；不能从作品 ID 构造缺失回执。此视频的回执恢复已经完成，重复执行保持幂等。

验证挑战或未知页面变化会有界返回并暂停账号。保留不确定的页面状态，先诊断再显式重新绑定/恢复；不可通过重复入队规避暂停。回执仅用于定位，正向回执也必须结合独立作品详情确认。

真实记录见 [准备验收](audit/xiaohongshu-video-preparation-2026-09-30.md)、[首次发表及恢复验收](audit/xiaohongshu-live-video-2026-09-30.md)和[3.mp4 实际回执及详情验收](audit/xiaohongshu-live-video-2026-10-01.md)。这些个人账号实测不表示平台页面变化、审核和账号验证均已覆盖。
