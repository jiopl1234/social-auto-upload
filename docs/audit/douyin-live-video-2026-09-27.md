# 抖音视频真实接入验证

基线：`2da5b7cd694fb386994082cbca044abe61cf9954`。规格：`.scratch/douyin-live-video/spec.md`。

## 实测来源

2026-09-27 在获授权的一次真实发布中观察到：`/web/api/media/user/info/` 返回稳定 `user.uid`；`/web/api/media/aweme/create_v2/` 返回 `status_code=0` 和字符串 `item_id`；`/janus/douyin/creator/pc/work_list` 返回同一作品、作者 UID、`item_title`、合并后的 `desc`、视频信息和状态。后台显示“已发布”。这次使用临时浏览器操作，不是新 CLI 的端到端验收。

后续无提交的真实页面核查观察到 `vod.bytedanceapi.com` 的 `CommitUploadInner` 响应：`Result.Results[0]` 有 `Vid`、`VideoMeta.Uri/Size/Width/Height/Duration/FileType`。预览资源路径对应该 Uri；预览就绪状态、尺寸和正时长共同确认文件处理。源 SHA256 来自本地预检，平台未返回这一摘要。

真实编辑器具有 30 字标题、1000 字正文、公开/立即发布控件、合集/声明占位项和横竖封面编辑器。适配器只选择原视频帧；选择器来自这些实际观察，页面变化时停止，不移除未知覆盖层。

适配器持久化上传视频 ID 与源摘要的关联，使用浏览器提交回执和独立作品观察确认交付，不伪造远端摘要。作品时间按接口的整数秒精度比较，不放宽任意时钟差。

## 当前验收

- 新平台测试已覆盖成功提交、只准备、账号不符、转码超时、预览变化、验证暂停、丢失回执、字段/视频不符、状态缺失、重复候选和重启恢复。
- 85 项现有受影响测试通过；新增测试及类型检查分批通过，完整套件与独立审查待补充。
- 真实 CLI 只读验收遇到会话认证失败：接口明确返回 `status_code=8`、`status_msg=用户未登录`。有窗口登录已启动，但在 120 秒内未完成而有界退出，仍待更新会话。
- 没有再次公开发表原视频。CLI 完整真实提交未执行，因此 `production_verified=false`；`can_execute=true` 仅开放文档所列范围，不保证无风控或无需验证。

未提交 Cookie、QR、签名参数或原始响应。测试账号、视频和数据均为合成夹具。
