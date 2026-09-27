# 抖音视频真实接入验证

基线：`2da5b7cd694fb386994082cbca044abe61cf9954`。实现：`cc8f7b7`；复审修正：`9cb4253`。规格：`.scratch/douyin-live-video/spec.md`。

## 实测来源

2026-09-27 在获授权的一次真实发布中观察到：`/web/api/media/user/info/` 返回稳定 `user.uid`；`/web/api/media/aweme/create_v2/` 返回 `status_code=0` 和字符串 `item_id`；`/janus/douyin/creator/pc/work_list` 返回同一作品、作者 UID、`item_title`、合并后的 `desc`、视频信息和状态。后台显示“已发布”。这次使用临时浏览器操作，不是新 CLI 的端到端验收。

后续无提交的真实页面核查观察到 `vod.bytedanceapi.com` 的 `CommitUploadInner` 响应：`Result.Results[0]` 有 `Vid`、`VideoMeta.Uri/Size/Width/Height/Duration/FileType`。预览资源路径对应该 Uri；预览就绪状态、尺寸和正时长共同确认文件处理。源 SHA256 来自本地预检，平台未返回这一摘要。

真实编辑器具有 30 字标题、1000 字正文、公开/立即发布控件、合集/声明占位项和横竖封面编辑器。适配器只选择原视频帧；页面变化时停止，不移除未知覆盖层。新增封面回读按实际观察到的横竖封面标签定位，要求图片可解码、尺寸有效，并在提交前比较来源；这部分代码已通过本地页面回归，尚待刷新登录后的真实页面验收。

适配器持久化上传视频 ID 与源摘要的关联，使用浏览器提交回执和独立作品观察确认交付，不伪造远端摘要。作品时间按接口的整数秒精度比较，不放宽任意时钟差。

## 当前验收

- 新平台的 21 项测试通过。覆盖成功提交、只准备后重启并继续同一任务、尝试上限、账号不符、转码超时、预览变化、封面保存失败/消失/替换、无弹窗认证错误、验证暂停、丢失回执、字段/视频不符、状态缺失、重复候选、重启恢复、只读不提交及失去所有权后的浏览器清理。
- 最终完整离线回归通过：`uv run --frozen --offline --no-sync pytest -q --timeout=60 --junitxml=.scratch/douyin-live-video/full-suite-final.xml`，393 项测试、20 项子测试，耗时 472.33 秒。报告保留在本地，不提交测试环境路径。
- `pyright orchestrator tests/test_douyin_live.py tests/douyin_fixture.py` 为 0 错误、0 警告。全仓库检查另有 115 错误、1 警告，位于本次未修改的旧上传器、示例和相关测试等文件；不宣称全仓库类型检查通过。
- Standards 与 Spec 两轮独立审查后均无剩余发现。修复了只准备任务不能继续、封面只依赖缓存标志、认证响应未暂停账号和只读清理被所有权检查阻断；视频预览检查合并为共享函数。审查不等于真实平台验收。
- 真实 CLI 只读验收遇到会话认证失败：接口明确返回 `status_code=8`、`status_msg=用户未登录`。有窗口登录已启动，但在 120 秒内未完成而有界退出，仍待更新会话。
- 没有再次公开发表原视频。CLI 完整真实提交未执行，因此 `production_verified=false`；`can_execute=true` 仅开放文档所列范围，不保证无风控或无需验证。

未提交 Cookie、QR、签名参数或原始响应。测试账号、视频和数据均为合成夹具。
