# 抖音视频 CLI

当前可执行范围：一个已绑定账号、一个原创且未使用 AI 的 MP4、标题和正文、公开立即发布、原视频帧横竖封面。暂不支持话题、合集、指定封面、转载和额外声明；这些输入在打开浏览器前拒绝。标题最多 30 个 UTF-16 单位，正文最多 1000 个，不截断。

真实账号的 CLI 只读核对和仅准备已于 2026-09-27 通过。封面会等待平台检测通过及横竖裁剪成品后再校验；检测未完成或未通过时有界停止，不继续发表。

能力报告中抖音视频为 `available_limited`、`can_execute=true`。真实提交及原任务只读恢复已确认一次公开发布；`production_verified=false` 保留验证后同一进程自动续行尚未验收的事实，其余四条路径仍为 `restricted`。见 [验证记录](audit/douyin-live-video-2026-09-27.md)及[最新重测](audit/douyin-verification-wait-2026-09-27.md#用户明确要求的真实-cli-重测)。

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

发布前可运行以下命令：上传素材、选择封面并核对字段，平台可能保存草稿，但绝不点击发表。

```powershell
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos run --job JOB_ID --prepare-only --headed
```

结果为 `READY/NOT_SUBMITTED`，浏览器关闭。以后正常运行同一 `JOB_ID` 会重新准备并消耗另一次尝试；重启 CLI 后仍可继续。默认最多 2 次，可明确指定 `--max-attempts 3`（最大 5），只准备也计入预算。后续执行仍需传入相同的 `--media-root`。不要为已经发表的视频新建交付任务做测试。

省略 `--job` 会串行执行当前可用路径，未接入路径保持排队。只准备操作必须指定一个任务。

## 发表后保留验证窗口

对尚未提交的新任务，可明确开启本人验证等待：

```powershell
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos run --job JOB_ID --headed --verification-wait 180
```

仅支持抖音视频，必须同时指定单个 `--job` 和 `--headed`，不能与 `--prepare-only` 混用。`--verification-wait` 范围 1–600 秒，默认 0（关闭）。这会实际公开发表，请用尚未提交的新任务。

如果最终发表动作之后出现验证，CLI 立即在 stderr 提示，账号暂停，原浏览器和任务锁保持打开。请按该窗口中的实际提示完成验证，例如扫码；不保证手机会收到推送。不要再次点击发表。程序不会代填验证码、移除验证或重复点击发表，重复出现的验证也不会重置等待期限。CLI 的 stdout 仍只输出最终任务 JSON。

验证通过后，CLI 继续等待同次提交的回执，要求页面产生新的账号身份响应，再保存会话并独立查询作品。页面未重新提供身份凭据时也会有界退出，不用验证前的缓存身份宣告成功。只有证据完整且清理成功，才自动解除本次验证造成的账号暂停。提示消失本身不代表发布成功。收到回执后才出现的验证、作品核对时再次出现的验证，都共享首次提示开始的截止时间；等待仍受总执行期限和清理预留约束，剩余总预算不足时会提前结束。

取消、关闭窗口、等待超时、身份改变或缺少回执时，结果保持保守，账号保持暂停，不自动重发。该选项仅处理本次发表后的验证；发表前要求重新登录时仍按原有方式退出。已经关闭的旧浏览器无法由此选项恢复，已提交任务使用该选项会被拒绝，应按下文只读恢复。

该流程已经过本地浏览器故障测试。一次用户明确要求的真实重测已公开发表，并通过原任务只读恢复得到确认。当时验证后的未知弹窗中断了同一进程，现已加入“有本次回执时保留原页面、在新页面只读核对”的本地测试修复；尚未针对真实未知弹窗再次验收，`production_verified=false` 不变。见[真实重测记录](audit/douyin-verification-wait-2026-09-27.md#用户明确要求的真实-cli-重测)。程序不会自动将未知提交换键重发。

## 只读检查和恢复

```powershell
uv run --frozen --offline --no-sync sau jobs inspect-work --platform douyin --account douyin_main --content-id WORK_ID
```

此命令只看作品列表，不上传或发表；当前列表页未出现指定作品时返回 `NOT_FOUND`，不代表远端不存在。

未开启上述等待，或等待失败时，遇到扫码、短信或实名提示会有界退出并暂停账号。重新登录后再次 `bind-account` 更新受管会话，再查询原任务：

- `BLOCKED/NOT_SUBMITTED`：执行 `sau jobs resume JOB_ID` 后运行原任务；不重置尝试次数。
- `UNKNOWN_COMMIT/MAY_HAVE_SUBMITTED`：直接运行原 `JOB_ID`，只查询，不重新上传或发表。
- 已完成：重复运行只返回保存结果。

恢复必须同时匹配本次回执作品 ID、准备阶段保存的上传视频 ID、账号、标题正文和创建时间。平台不返回源文件 SHA256；缺回执、证据不足或多个匹配结果时保留 `UNKNOWN_COMMIT`。不能通过新交付键、删数据库或删锁自动重发。
