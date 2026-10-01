# 视频号视频发布

`sau jobs` 已开放视频号的有限视频发布范围，平台名称是 `tencent`。2026-10-01 UTC 使用 `4.mp4` 完成新版 CLI 首次提交到完成的完整验收，见[CLI 首发记录](audit/channels-cli-video-2026-10-01.md)。先前 `3.mp4` 的公共任务接口发表和原任务恢复见[历史记录](audit/channels-live-video-2026-10-01.md)。

当前支持一条原创、未使用 AI 的视频，文件不超过 32 MiB，短标题为 6–16 个 UTF-16 单元，正文为单段且不超过 1000 个 UTF-16 单元。支持立即公开提交、平台默认封面；不支持话题、提及、位置、合集、链接、活动、自定义封面或额外声明。HEVC 输入使用页面自身的处理请求，不改变本地源文件。页面上传/转码/默认封面就绪后才允许发表。

## 本次作品与查询

账号 `channels_main` 已绑定到登录的 `Manson9413`，本次任务保存在以下数据库。继续使用这个数据库可以查询已有状态和防重发记录；默认数据库是另一份文件。

```powershell
Set-Location C:\Users\Chinese\social-auto-upload
$channelsDb = '.scratch/channels-live-2026-10-01/acceptance.sqlite3'
uv run --frozen --offline --no-sync sau jobs --db $channelsDb --json status 2dda90cac34640acac00f5e87031d461
uv run --frozen --offline --no-sync sau jobs --db $channelsDb --json attempts 2dda90cac34640acac00f5e87031d461
uv run --frozen --offline --no-sync sau jobs --db $channelsDb --json inspect-work --platform tencent --account channels_main --content-id 'export/UzFfBgAAxOKnNF9CMQ7Yk8zT4DCaHIZmX3hFUwdT8ck_QzzUXw' --headed
```

`inspect-work` 只查询已有作品，不上传或发表。`4.mp4` 首次 CLI 返回 `COMPLETED / CONFIRMED / IN_REVIEW`，后续独立查询为 `FOUND / ACCEPTED`；历史任务保留首次审核中记录，累计提交次数为 1。`ACCEPTED` 表示作品已被平台接收；若接口明确显示审核中，记录为 `IN_REVIEW`。这两个状态都不宣称审核通过或已经获得分发。

## 发布下一份内容

把新清单保存为 UTF-8 JSON，使用新素材及代表新交付意图的键。下面的路径和文字需要替换。不要给已经发表的 `3.mp4` 或 `4.mp4` 换键重发来测试。

```json
{
  "schema_version": 1,
  "delivery_key": "channels-next-original-001",
  "platform": "tencent",
  "account": "channels_main",
  "content_type": "video",
  "title": "下一条原创作品",
  "body": "这里填写新作品的单段正文。",
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

```powershell
uv run --frozen --offline --no-sync sau jobs --db $channelsDb --media-root C:/Users/Chinese/Videos --json enqueue --manifest channels-next.json
# 使用入队返回的 job_id；此命令会实际发表。
uv run --frozen --offline --no-sync sau jobs --db $channelsDb --media-root C:/Users/Chinese/Videos --json run --job JOB_ID --headed
```

需要先验收准备时，执行相同命令并加 `--prepare-only`。`READY / NOT_SUBMITTED` 仅表示准备完成；之后对同一任务执行普通 `run`，重新准备并核对后才发表。保留足够的 `--max-attempts`（默认 2，最多 5），因为每次准备都会占用一次尝试。新的准备会在浏览器加载前排除已观察到的上传续传缓存，保留登录、账号和设备状态，以便本次重新核对实际上传分片；只读恢复不做这项处理。

更换账号或数据库时，先通过 `sau tencent login --account ACCOUNT --headed` 保存登录，再用 `jobs bind-account --platform tencent --account ACCOUNT --expected-id REAL_FINDER_USERNAME --session-source cookies/tencent_ACCOUNT.json` 登记。稳定 ID 来自真实 `auth_data` 的 `finderUser.finderUsername`（`v2_…@finder`），不能用昵称或短 ID 代替。绑定后运行时仍会独立核对在线身份。

## 结果不确定时

对于 `UNKNOWN_COMMIT / MAY_HAVE_SUBMITTED`，只对原 `job_id` 执行普通 `run`；该路径只读查询，不再上传或点击发表。实际视频号正向创建回执可能只有 HTTP 201、`errCode=0`、`data.baseResp.errcode=0`，没有作品 ID。程序保存正向确认，保留 `receipt_id=null`，再通过完整账号作品列表与独立详情核对作品 ID、原视频 MD5、精确标题/正文、公开设置及创建时间。没有回执 ID 时最多读取完整第一页的 10 条作品；列表被过滤、分页未完整、详情不可读或匹配不唯一均保持未知，不能换键重发。

详情接口未提供作者 ID 时，归属证据来自独立在线身份与该身份的创作者作品列表，结果明确记录 `authenticated_creator_list`，不会声称详情返回了作者。转换后的媒体链接可以变化；原视频 MD5 必须匹配同次尝试持久化的源文件指纹。

出现登录、实名、扫码或验证码提示时暂停账号。视频号尚未实现提交后的保留窗口验证等待，不能使用抖音/小红书的 `--verification-wait`。完成合法验证并重新绑定会话后再恢复原任务；不自动处理验证、不自动重发。

`capabilities` 当前为 `available_limited / can_execute=true / production_verified=true`，并报告 `live_cli_submission_verified=true`、`missing_evidence=[]`：`4.mp4` 的真实首次 CLI 执行直接完成发表、即时回执持久化及作品核对。这仅验收本页列出的有限范围，不能据此保证其他账号或内容组合、不会触发验证、审核通过或获得分发。
