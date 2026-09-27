# 抖音视频 CLI

当前可执行范围：一个已绑定账号、一个原创且未使用 AI 的 MP4、标题和正文、公开立即发布、原视频帧横竖封面。暂不支持话题、合集、指定封面、转载和额外声明；这些输入在打开浏览器前拒绝。标题最多 30 个 UTF-16 单位，正文最多 1000 个，不截断。

能力报告中抖音视频为 `available_limited`、`can_execute=true`。`production_verified=false` 保留这版 CLI 完整真实提交尚未验收的事实；其余四条路径仍为 `restricted`。见 [验证记录](audit/douyin-live-video-2026-09-27.md)。

## 登录和绑定

在仓库目录执行。首次安装浏览器：`uv run --frozen --offline --no-sync patchright install chromium`。

```powershell
uv run --frozen --offline --no-sync sau douyin login --account douyin_main --headed
uv run --frozen --offline --no-sync sau jobs bind-account --platform douyin --account douyin_main --expected-id YOUR_STABLE_UID --session-source cookies/douyin_douyin_main.json
```

稳定 UID 来自已登录创作者页面自身的 `/web/api/media/user/info/` 响应中的 `user.uid`，不是昵称或抖音号。绑定只复制会话；执行时仍独立核对账号。

## 创建一次交付

保存 UTF-8 的 `content.json`，替换成下一条新视频。每条新内容使用新的交付键；重复提交相同请求返回原任务。

```json
{
  "schema_version": 1,
  "delivery_key": "original-next-video-001",
  "platform": "douyin",
  "account": "douyin_main",
  "content_type": "video",
  "title": "今天的家常一餐",
  "body": "记录日常生活的小满足。",
  "tags": [],
  "media": ["C:/Users/Chinese/Videos/NEXT_VIDEO.mp4"],
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
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos enqueue --manifest content.json
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos run --job JOB_ID --headed
uv run --frozen --offline --no-sync sau jobs status JOB_ID
```

`run` 会公开发表。`COMPLETED/CONFIRMED/PUBLISHED` 才表示已核实公开作品；审核中单独返回 `IN_REVIEW`。退出码 0 不能代替任务字段的判断。

发布前可运行 `run --job JOB_ID --prepare-only --headed`：上传素材、选择封面并核对字段，平台可能保存草稿，但绝不点击发表。结果为 `READY/NOT_SUBMITTED`，浏览器关闭。以后正常运行会重新准备并消耗另一次尝试；默认最多 2 次，可明确指定 `--max-attempts 3`（最大 5）。不要为已经发表的视频新建交付任务做测试。

省略 `--job` 会串行执行当前可用路径，未接入路径保持排队。只准备操作必须指定一个任务。

## 只读检查和恢复

```powershell
uv run --frozen --offline --no-sync sau jobs inspect-work --platform douyin --account douyin_main --content-id WORK_ID
```

此命令只看作品列表，不上传或发表；当前列表页未出现指定作品时返回 `NOT_FOUND`，不代表远端不存在。

遇到扫码、短信或实名提示，任务有界退出并暂停账号。重新登录后再次 `bind-account` 更新受管会话，再查询原任务：

- `BLOCKED/NOT_SUBMITTED`：执行 `sau jobs resume JOB_ID` 后运行原任务；不重置尝试次数。
- `UNKNOWN_COMMIT/MAY_HAVE_SUBMITTED`：直接运行原 `JOB_ID`，只查询，不重新上传或发表。
- 已完成：重复运行只返回保存结果。

恢复必须同时匹配本次回执作品 ID、准备阶段保存的上传视频 ID、账号、标题正文和创建时间。平台不返回源文件 SHA256；缺回执、证据不足或多个匹配结果时保留 `UNKNOWN_COMMIT`。不能通过新交付键、删数据库或删锁自动重发。
