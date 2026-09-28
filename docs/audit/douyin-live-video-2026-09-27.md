# 抖音视频真实接入验证

基线：`2da5b7cd694fb386994082cbca044abe61cf9954`。实现：`cc8f7b7`；复审修正：`9cb4253`。规格：`.scratch/douyin-live-video/spec.md`。

## 实测来源

2026-09-27 在获授权的一次真实发布中观察到：`/web/api/media/user/info/` 返回稳定 `user.uid`；`/web/api/media/aweme/create_v2/` 返回 `status_code=0` 和字符串 `item_id`；`/janus/douyin/creator/pc/work_list` 返回同一作品、作者 UID、`item_title`、合并后的 `desc`、视频信息和状态。后台显示“已发布”。这次使用临时浏览器操作，不是新 CLI 的端到端验收。

后续无提交的真实页面核查观察到 `vod.bytedanceapi.com` 的 `CommitUploadInner` 响应：`Result.Results[0]` 有 `Vid`、`VideoMeta.Uri/Size/Width/Height/Duration/FileType`。预览资源路径对应该 Uri；预览就绪状态、尺寸和正时长共同确认文件处理。源 SHA256 来自本地预检，平台未返回这一摘要。

真实编辑器具有 30 字标题、1000 字正文、公开/立即发布控件、合集/声明占位项和横竖封面编辑器。适配器只选择原视频帧；页面变化时停止，不移除未知覆盖层。封面回读按实际横竖封面标签定位，等待封面检测通过及上传后的裁剪图片，要求图片可解码、尺寸符合 3:4 / 4:3（容许一像素舍入），并在提交前比较来源。

刷新登录后的实测补齐了这些页面行为：作品列表可能展示“合集管理移到这里啦”的单按钮信息提示，只有这一完整提示在精确作品页可单次确认；源帧缩略图被滑块手柄覆盖，选择器应点击手柄；保存封面会切换到封面预览并卸载视频元素，最终校验需重新点“预览视频”；封面先显示原视频截图，再异步换成裁剪成品。不能把未裁剪截图当作最终封面。

正文采用 Ace 行结构，末尾带 `data-string=true/data-enter=true` 的 U+200B 光标标记。回读只排除这一明确标记，保留用户的换行及正文中的零宽字符，不整体 trim 或清洗正文。

适配器持久化上传视频 ID 与源摘要的关联，使用浏览器提交回执和独立作品观察确认交付，不伪造远端摘要。作品时间按接口的整数秒精度比较，不放宽任意时钟差。

## 当前验收

- 抖音平台回归已扩展为 31 项，补充了作品页已知/未知提示、真实帧选择覆盖结构、视频页签切换、临时/未裁剪封面不能就绪，以及富文本标记与用户原文保留。
- 最终完整离线回归通过：`uv run --frozen --offline --no-sync pytest -q --timeout=60 --junitxml=.scratch/douyin-live-video/full-suite-final.xml`，403 项测试、20 项子测试，耗时 549.94 秒。报告保留在本地，不提交测试环境路径。
- `pyright orchestrator tests/test_douyin_live.py tests/douyin_fixture.py` 为 0 错误、0 警告。全仓库检查另有 115 错误、1 警告，位于本次未修改的旧上传器、示例和相关测试等文件；不宣称全仓库类型检查通过。
- Standards 与 Spec 独立复审后均无剩余发现。本轮额外修复了封面检测在最终读回时失效仍提交、已知提示被安全验证替换或遮住时未暂停账号两项缺陷；三个故障回归先失败、修复后通过。预览等待使用兼容 Python 3.10 的 `asyncio.wait_for`，未在本机运行 Python 3.10。此前修复包含只准备任务继续执行、认证响应暂停账号和所有权丢失后的只读清理。审查不等于真实平台验收。
- 2026-09-27 14:26:12 UTC，刷新登录后的真实 CLI `inspect-work` 返回 `FOUND/PUBLISHED`，正确核对已绑定账号及原作品。
- 2026-09-27 14:52:26 UTC，真实 CLI `run --prepare-only --headed` 返回 `READY/NOT_SUBMITTED`，标题、正文、视频摘要、空话题/合集/声明均一致，无错误或警告。账号保持 READY；attempt 的 `submit_count=0`、`submission_started_at=null`、`receipt_id=null`。浏览器正常清理。记录位于独立本地验收数据库，不能把这些仅准备记录作为下一次公开发布任务。
- 没有再次公开发表原视频。随后对另一条用户授权的原创、非 AI 视频执行了真实 CLI 提交，结果见下文；完整交付仍未验收通过，因此 `production_verified=false`。`can_execute=true` 仅开放文档所列范围，不保证无风控或无需验证。

## 第二条视频的真实 CLI 提交

2026-09-28 04:34 UTC（本地 2026-09-27），用户授权用新版 CLI 公开发布 `2.mp4`，并确认原创、无 AI，允许代拟文案。执行使用现有 `douyin_main` 账号，限定单一任务及一次尝试：

```powershell
uv run --frozen --offline --no-sync sau jobs --media-root C:/Users/Chinese/Videos run --job 53ac89bdd52e4d73b3d52f36e57170aa --headed --max-attempts 1
```

- 真实上传、封面及文字校验通过，任务从 READY 进入 SUBMITTING。
- 最终动作后检测到 `AUTH_REQUIRED`。持久化 `submit_count=1`、`receipt_id=null`，任务为 `UNKNOWN_COMMIT/MAY_HAVE_SUBMITTED`；账号暂停，浏览器清理完成。没有重新入队或再次提交。
- 用户没有收到手机验证提示。随后只读查询仍能核对已绑定账号和此前作品，说明普通登录仍有效，不能把这次发表阶段的验证当作普通登录过期处理。
- 04:39 UTC 的独立只读核查列出 1 条作品，没有匹配这次上传视频 ID 或标题的作品。这只表示当前列表没有找到，不足以断言远端未接收，所以保留未知提交状态。
- 本次未确认公开发表成功。当前 CLI 遇到验证会有界暂停并关闭浏览器，尚不支持保留同一发表页面供本人完成验证后继续核对；真实端到端发布验收仍未通过。

本次任务和提交记录保存在默认任务数据库，私有核查记录在 `.scratch/douyin-live-video/acceptance/`，不提交到仓库。

准备验收阶段的 codebase-memory MCP 返回 `Transport closed`，当时代码审查回退源码及本地故障夹具。本次真实提交诊断已恢复索引访问，对标记为 `metadata_changed` 的相关代码再次读取源码核对。

未提交 Cookie、QR、签名参数或原始响应。离线测试账号、视频和数据均为合成夹具。
