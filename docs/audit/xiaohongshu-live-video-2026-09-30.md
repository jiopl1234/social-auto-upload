# 小红书视频真实 CLI 发表与只读恢复

本文件记录 `2.mp4` 首次验收时的能力与回执缺口。后续 `3.mp4` 已捕获真实正向创建响应并完成 CLI 回执恢复，当前能力与剩余边界见 [后续验收](xiaohongshu-live-video-2026-10-01.md)。

基线 `bdf9281`，工作区新增视频交付适配器 `xiaohongshu_video_v1`，能力版本 `1.2.0`。用户在准备验收之后明确要求补齐详情、回执和完整发表实测，继续授权原创、无 AI 的 `2.mp4`，文案由助手拟定。

## 实际发表和确认

正式任务 `9d794195cb124adaa66902a692398631`，唯一 attempt `445a7a1197684b3281a7103e560c661c`。原准备任务保持 `NOT_SUBMITTED`。视频标题为“小猫的歪头杀，谁顶得住”，正文两行：

> 逗猫棒一晃，小家伙就开始认真歪头。
> 这十秒，反复看了好多遍。

2026-09-30 13:12:19.115520 UTC 持久写入 `SUBMITTING` 后，CLI 点击一次发表。首次执行未能解析出正向接口回执，返回 `UNKNOWN_COMMIT / MAY_HAVE_SUBMITTED`。没有再次发表，也没有构造回执或替换任务。

只读打开创作者作品管理页，观察真实列表 GET `/api/galaxy/v2/creator/note/user/posted?tab=0&page=0`。响应 `page=-1`，全部标签 `special.note_time_desc` 被选中，数量 2 与实际两行一致，其中一条为本次视频。加载真实编辑详情视图后，由平台页面自身发出 GET `https://edith.xiaohongshu.com/web_api/sns/capa/postgw/note/detail?note_id=...&edit_mode=1`；不填写、不保存、不点击更新，恢复浏览器阻断创作者和接口域的非 GET/HEAD/OPTIONS 请求。

真实详情确认作品 `6abd0ab30000000002010dfc`：稳定作者与绑定账号相同，标题和两行正文完全一致，类型 video，`post_time=1790773939` 对应 13:12:19 UTC，与本次持久发表时间的整数秒相同。公开隐私类型为 0，列表 `tab_status=1`，详情 `enabled=true`，无话题、提及和定时。

源文件 4,754,745 字节，720×1280，约 10.61 秒。发表前保存的 SHA256 为 `d23aa5d64d06e9dc5363ea8573284e86bd3c4aeb0f6a552cb5375aa9a8353039`。可终止子进程验证该 SHA256 后补充旧 attempt 的源 MD5：`a97c9bc910fbbd98f123ecf2b9454fd0`；真实详情 `video_info.media_v2.video.md5` 与其完全相同。该平台字段表示原始视频指纹；转码流大小不同，不重新计算转码流并冒称原始源 SHA256。

临时上传 Key 与详情 `video_info.file_id` 不相同，二者分别保留；不再用临时 Key 等于规范文件 ID 作为匹配前提。详情提供独立原始 MD5，由本次 attempt 的已保存上传证据绑定到源 SHA256。无接口回执时仍必须完整列表、可读详情和唯一匹配，不能仅靠标题或“最新作品”恢复。

新版 CLI 于 2026-09-30 13:38:40.632307 UTC 返回 `COMPLETED / CONFIRMED / PUBLISHED`，作品 ID 如上，无错误和警告，原 attempt 不变，`submit_count=1`。详情的远端素材 ID、原始 MD5、完整列表标记和观察时间已保存为任务证据。

随后再次执行相同正式 job 的 CLI run，直接返回同一完成结果，`verified_at`、attempt 和作品 ID 不变，数据库仍只有一个 attempt，`submit_count=1`；完成状态没有重新上传或再次发表。

## 回执证据的边界

首次真实创建响应未保存完整响应体，`attempt.receipt_id` 仍为空，不能由作品 ID 反填成“已收到接口回执”。本次真实成功由只读详情恢复确认。

平台公开前端将创建接口 `/web_api/sns/v2/note` 的未抽取响应交给发表成功处理器，原实现仅支持 `data.id`。新解析器支持正向 success/code 信封中的 `id`、`noteId`、`note_id` 字段及顶层 result 信封；要求合法且唯一的 24 位 ID，拒绝负向、缺字段或冲突响应。这些格式兼容通过本地合成响应测试，未宣称其中任一新格式已捕获于本次真实创建响应。

之后的尝试会持久保存安全响应结构（HTTP 状态、信封种类、允许的 ID 字段名），不保存原始响应、Cookie、签名、验证码或上传令牌。回执观察器参与原执行预算和资源清理，未知回执可进入独立详情核对，绝不触发再次点击。

能力报告因此为 `available_limited / can_execute=true`，真实发表、详情核对及同 attempt 的 CLI 恢复已验证，`production_verified=false`、`missing_evidence=["submission_receipt"]` 保留。下一份独立新内容的正常发表才能完成新解析器正向回执的线上验收；本次已发表视频不为此重复投递。

## 验证与资料保管

离线用本地页面、真实 SQLite 和浏览器验证正常交付、回执信封、MD5 错误、错作者/正文/时间、回执丢失、列表不完整、多候选、源文件移除、旧证据补充、只读写请求阻断及验证后的单次发表。

全量 `pytest -q --timeout=60` 覆盖 467 项测试，首轮 466 项与 20 项子测试通过，耗时 707.69 秒；唯一失败为旧测试仍断言小红书视频 `restricted`。将该测试改为仍受限的小红书图文/视频号，并按实际任务接口区分图文的领取前拒绝与视频号的发表前拒绝。受影响文件其余 36 项通过，修正的单项最后定向重测通过（1.87 秒）。这是全量首轮加定向复测的结果，没有声称修改后再次运行完整套件。小红书准备/交付专项 40 项均在全量首轮通过。

`pyright orchestrator tests/test_xiaohongshu_delivery.py tests/test_xiaohongshu_live.py tests/xiaohongshu_fixture.py tests/test_platform_tasks.py tests/test_release_acceptance.py` 零错误、零警告；`git diff --check` 通过。离线回执与验证挑战测试均使用合成数据，不能替代新解析器的正向线上回执证据。

真实数据库、完整详情、浏览器会话和核查脚本仅保存在本地 `.scratch` 与运行目录；公开报告不包含账号凭证、稳定个人 ID 或带令牌媒体 URL。
