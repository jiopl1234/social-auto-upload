# 视频号新版 CLI 完整首发验收：2026-10-01 UTC

用户要求使用新版 CLI 对 `C:/Users/Chinese/Videos/4.mp4` 完成首发。沿用用户已确认的原创、无 AI 和由助手拟定文案的事实。账号为 `channels_main / Manson9413`，数据库为 `.scratch/channels-live-2026-10-01/acceptance.sqlite3`。此次所有发布与查询动作均通过实际 `uv run --frozen --offline --no-sync sau jobs` 子进程执行，没有直接调用 TaskService 代替 CLI。

| 项目 | 结果 |
|---|---|
| 交付键 | `channels-4-cli-public-20261001` |
| 任务 | `2dda90cac34640acac00f5e87031d461` |
| 唯一尝试 | `1abe7388680a41dba841a81cfff7c29e` |
| 执行时间 | `2026-10-01T08:46:50.587485Z` 至 `2026-10-01T08:47:46.058146Z` |
| 平台创建时间 | `2026-10-01T08:47:39Z`（洛杉矶 2026-10-01 01:47:39） |
| 首次 CLI 结果 | `COMPLETED / CONFIRMED / IN_REVIEW` |
| 后续独立查询 | `FOUND / ACCEPTED`，`2026-10-01T08:48:19.011871Z` |
| 作品 ID | `export/UzFfBgAAxOKnNF9CMQ7Yk8zT4DCaHIZmX3hFUwdT8ck_QzzUXw` |
| 累计提交及尝试次数 | 各为 `1` |
| 源文件 | 1080×1920，约 18.433 秒，15,098,481 字节 |
| 源 SHA-256 | `7537e0b6fd46b25f3d63664284e6a19c30721ef7e00231d67b10d406d9f65ecb` |
| 源及作品 MD5 | `93476d1a8c40c67d756e5f63f33570ab` |
| 标题 | 逗猫棒下的小圆脸 |
| 正文 | 逗猫棒轻轻一晃，小圆脸就跟着认真抬头。圆圆的眼睛里全是好奇，连歪头的样子都让人忍不住多看几遍。 |

## 首次提交与证据

新清单先经 `enqueue --manifest` 入队，再执行 `run --job 2dda90cac34640acac00f5e87031d461 --headed --max-attempts 1`。首次执行退出码为 0，直接得到完成结果；没有仅准备前置尝试、额外重试、回执重解析或只读恢复来补齐第一次执行。

同次尝试持久化了源视频 SHA-256、MD5 和实际上传媒体标识。真实创建响应为 HTTP 201，允许的语义字段为 `errCode=0`、`data.baseResp.errcode=0`，没有作品 ID，`receipt_id` 保持 `null`。正向确认在本次正常发表内即时入库，未从作品列表制造回执 ID。

随后的完整创作者列表和独立作品详情在同一 CLI 调用内确认唯一作品，精确匹配短标题、正文、原视频 MD5、公开设置、创建时间及账号归属。持久证据记录 `listing_complete=true` 和 `account_evidence=authenticated_creator_list`；详情没有作者 ID，归属仍来自独立认证身份和该身份的创作者作品列表。处理后的远端媒体标识与临时上传标识不同，匹配依据为独立返回的原视频 MD5。

首次结果明确观察到审核中，因此持久任务保留 `platform_result=IN_REVIEW`。后续 CLI `inspect-work --platform tencent` 独立返回 `FOUND / ACCEPTED`；该查询不改写历史任务结果。`ACCEPTED` 表示平台接收了作品，不宣称审核通过或已经获得分发。

## 防重发与能力范围

CLI `status`、`attempts`、独立 `inspect-work`、已完成任务重复 `run` 和同清单重复 `enqueue` 均通过。重复执行返回原 attempt，重复入队返回原 job；最终仍只有一次尝试和一次提交。账号维持 `READY`。先前 `3.mp4` 的正式任务仍为完成状态，准备探针未重新执行。

本次补齐 `cli_end_to_end_live_submission`。视频号视频能力更新为 `production_verified=true`、`live_cli_submission_verified=true`、`missing_evidence=[]`，状态仍为 `available_limited`。该标记仅证明[已支持范围](../channels-video.md)的完整 CLI 路径在这个账号和视频上获得真实验收，不能推导其他内容组合、多账号、无需本人验证、不会触发风控、审核或分发结果。抖音和小红书各自未验收项继续保留。

先前 `3.mp4` 的公共任务接口提交、回执格式发现和原尝试恢复见[历史记录](channels-live-video-2026-10-01.md)。本次完整命令输出、清单、预览帧和验收断言保存在本机 Git 排除目录 `.scratch/channels-live-2026-10-01/cli-4/`；公开文档不含会话、验证内容或签名媒体地址。

本次只更新已取得证据的能力声明、相关文档与旧能力断言，没有改变发布执行逻辑。相关能力/CLI/受限路径回归 `3 passed`；`pyright orchestrator/platforms/__init__.py tests/test_job_cli.py tests/test_release_acceptance.py` 为 0 errors / 0 warnings。真实首发及独立查询的结果由 CLI 输出与持久尝试记录共同核对。
