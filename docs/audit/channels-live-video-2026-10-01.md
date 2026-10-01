# 视频号真实视频验收：2026-10-01 UTC

用户授权使用 `C:/Users/Chinese/Videos/3.mp4` 公开发表，声明为原创、无 AI，文案由助手拟定。实测账号为 `channels_main / Manson9413`。仅一次请求到达真实创建接口。

| 项目 | 结果 |
|---|---|
| 任务 | `f0859b74ad704836a89f9de929c8e0a3` |
| 发表尝试 | `0e2d6dcb5f824be9b06c1d1052fd8575` |
| 创建时间 | `2026-10-01T06:57:33Z`（洛杉矶 2026-09-30 23:57:33） |
| 只读恢复确认 | `2026-10-01T07:05:37.196751Z` |
| 任务结果 | `COMPLETED / CONFIRMED / ACCEPTED` |
| 作品 ID | `export/UzFfBgAAxJ2nIGNkfBLYk8zT4DCabJ2fJuxVY-5uSiDUPuUumA` |
| 累计提交次数 | `1`，原先被拦截的准备尝试为 `0` |
| 源文件 | HEVC/AAC，1080×1920，约 26.308 秒，22,372,926 字节；本地未转码 |
| 源 SHA-256 | `8b6bfe429e200a3bdd8224ffb0c6b2da2c0c1bd3149bd4fe48192b9c4dbe0f04` |
| 源及作品 MD5 | `6df6731a0ac2f60942af683b883e4a9b` |
| 标题 | 小毛球眼里的逗猫棒 |
| 正文 | 小小一团，盯着逗猫棒却特别认真。看它抬头、歪头的样子，忍不住多陪它玩一会儿。 |

## 在线证据

实际读取 `https://channels.weixin.qq.com` 的创作者页面、浏览器产生的请求和平台前端脚本；没有重放发表请求。相关请求来源为该登录账号的正常页面运行。

- `POST /cgi-bin/mmfinderassistant-bin/auth/auth_data`：HTTP 201、`errCode=0`，取得稳定 `finderUser.finderUsername`。在线身份与绑定相同，每次守卫检查认证 Cookie 摘要。
- 浏览器 CDN `PUT /uploadpartdfs`：实际请求体的 SHA-256、MD5/ETag 和分片编号匹配不可变源文件；完成请求与服务端确认一致。观看子进程核对有序分片，浏览器独立核对本地预览完整字节与源 SHA-256。
- `POST /micro/content/cgi-bin/mmfinderassistant-bin/post/post_clip_video` 及结果查询：页面为 HEVC 执行自己的处理流程。成功结果含原视频 MD5、文件大小及新的媒体链接；本地源文件保持不变。准备阶段允许这项处理，提交后和只读恢复时禁止发起新的处理请求。
- `POST /micro/content/cgi-bin/mmfinderassistant-bin/post/post_create`：HTTP 201、`errCode=0`、`data.baseResp.errcode=0`，**没有作品 ID**。已持久化允许的语义字段，`receipt_id` 保持 `null`。这是真实正向确认，不是从列表推导出的创建回执 ID。
- `POST /micro/content/cgi-bin/mmfinderassistant-bin/post/post_list`：当前账号、`currentPage=1`、`userpageType=11`，无筛选条件；`totalCount=1`、`continueFlag=false`，唯一作品 ID 如上。
- `POST /micro/content/cgi-bin/mmfinderassistant-bin/post/get_post_info`：独立读取该作品，标题、正文、原视频 MD5、创建时间及 `status=1 / visibleType=1` 与交付一致。详情没有作者 ID；账号归属证据是独立登录身份及该身份的创作者作品列表，不能称为详情作者字段。

最初列表含“审核中”字样；后续详情返回正常公开作品。实现保守报告 `ACCEPTED`，不以正常作品状态推导审核通过或获得分发。转换后的 URL 与临时上传 URL 不同，不要求媒体链接摘要相等；仅保存链接中的媒体键摘要，不保存签名下载 URL。

第一次准备因 HEVC 处理 RPC 被过严规则拦截，在发表前结束，提交次数为 0。修复后原任务重新准备，只点击发表一次。首版解析器因期待回执 ID 返回 `UNKNOWN_COMMIT`；随后针对真实回执和详情格式完成实现，再使用原任务、原尝试只读恢复，没有补写创建 ID、修改提交次数或重新发表。

## CLI 与实现范围

真实 CLI 已验证 `run --prepare-only`、`status`、`attempts`、`inspect-work --platform tencent`、已完成任务的重复 `run`、同清单重复 `enqueue` 及 `capabilities`。作品查询为 `FOUND`，重复调用仍是原任务/原尝试，累计提交次数为 1。

首个真实发表及原尝试恢复使用与 CLI 共用的公共 `TaskService` 和正式 `BrowserDeliveryAdapter / ChannelsVideoPort`，没有修改能力报告来冒充首个 CLI 提交。在本次 `3.mp4` 验收结束时，新版 CLI 首次提交到完成的全路径尚未用另一份新视频线上验收，因此当时 `production_verified=false`，缺失项为 `cli_end_to_end_live_submission`。同日用户随后授权的 `4.mp4` 已通过真正的 CLI 首次执行直接完成，后续能力更新与证据见[完整 CLI 首发记录](channels-cli-video-2026-10-01.md)。

已有真实准备验证了未变化的源文件、完整上传与页面预览。补充同素材复用托管会话的仅准备验收返回 `READY / NOT_SUBMITTED`，本次新的准备尝试为 `ab309d6e272a488eb22e42e76b52e924`；无需重新绑定原始登录会话，仍取得完整上传证据。缓存只在新准备的浏览器初始快照中排除，认证和设备状态保留，只读恢复不清理缓存。验收结束后，仍可执行的准备探针通过正常所有权和取消流程结束，其他探针保持不可重试的失败状态，避免以后批量执行误发表同一素材；正式发表任务保持完成状态。

本地回放测试覆盖 HTTP 200/201 与正/负状态、分片顺序和内容错误、源与转换后的媒体标识不同、没有 ID 的正向确认、身份/列表到达顺序、完整列表/筛选/缺页/重复匹配、错误作者/正文/媒体、验证暂停、页面处理未完成、仅准备不可发表、自动重试拦截、源文件移走后的原尝试恢复、只读查询及缓存处理。录制回放使用本地 HTTP 服务和合成媒体，不代表其他账号、平台 DOM 或未来版本已经验收。

私有协议记录、页面截图、任务数据库和完整测试输出保留在本机 `.scratch/channels-live-2026-10-01/`，该目录已从 Git 排除；受管登录会话继续保存在用户运行目录。公开说明不包含 Cookie、验证内容、上传凭证或签名下载地址。

## 回归验证

| 检查 | 结果 |
|---|---|
| 视频号 45 项、浏览器生命周期与 CLI 补充回归 | 64 passed |
| 公共平台任务、CLI、发布验收回归 | 64 passed |
| 小红书准备与发布回归 | 57 passed |
| `pyright orchestrator` 与本次相关测试文件 | 0 errors / 0 warnings |
| `git diff --check` | 通过 |

测试为离线、本地页面与真实任务存储验证，不会新增线上发表。类型检查通过范围为任务模块及相关测试；全仓扫描还报告旧上传器、示例和 Web 后端等目录的 116 项错误、1 项警告，未将全仓类型检查列作通过项。
