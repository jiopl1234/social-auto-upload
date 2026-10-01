# 小红书 3.mp4 真实提交、创建回执与作品详情验收

基线 `bdf9281`，工作区实现能力版本 `1.2.1`。用户明确授权将 `C:/Users/Chinese/Videos/3.mp4` 发布到小红书，确认原创、无 AI，标题与文案由助手拟定。本报告时间使用 UTC；实际提交对应用户本地 2026-09-30 21:28。

## 素材及上传故障修复

源文件 22,372,926 字节，1080×1920，浏览器观察时长 26.308209 秒，HEVC/H.265 视频与 AAC 音频。源 SHA256 为 `8b6bfe429e200a3bdd8224ffb0c6b2da2c0c1bd3149bd4fe48192b9c4dbe0f04`，MD5 为 `6df6731a0ac2f60942af683b883e4a9b`。使用原始文件，没有转码。

前两次正式准备尝试 `73a186c6fa52440b95f372b0b2d9482c`、`2336afeccecd44cdbe6e229382b76726` 均在 processing 阶段超时，保持 `FAILED_PRE_SUBMIT/NOT_SUBMITTED`，发表计数为 0。浏览器真实预览已解码，readyState=4，无解码错误；因此不能归因于 HEVC 不支持。

真实上传首先使用五个 5 MiB 分片，随后同一文件又使用八个 3 MiB 分片。原实现固定 2 MiB，补充固定 5 MiB 仍不能覆盖动态选择。最终按实际分片编号、长度、请求体 SHA256 和 MD5/ETag记录同一上传事务，要求连续完整编号、无冲突重复分片、总长度等于源文件、完成请求准确引用所有已观察分片及服务器正向完成响应。可终止子进程按该实际顺序读取源文件，核对每片及整文件 SHA256/MD5，沿用累计 processing/总预算与资源清理保护。接受范围为 32 MiB、最多 64 片；子进程只返回紧凑验证结果。

修复后的仅准备诊断实际观察八个 3 MiB 分片及对应完成响应，返回 `READY/NOT_SUBMITTED`。预览 blob 大小与 SHA256均与原始源文件相同。仅准备适配器不具备发表方法，诊断任务未点击发表。

## 唯一正式提交与独立核对

正式任务 `5c53125b50c9478586f2a24ef6adc954`，唯一 attempt `6ec59d3c5cf143388f2855b9756e005d`，delivery_key 为 `xhs-video-3-public-20260930-v3`。

标题：**这团小毛球，眼里只有逗猫棒**

正文保持两行：

> 小小一团，盯着逗猫棒却特别认真。
> 看它抬头、歪头的样子，忍不住多陪它玩一会儿。

2026-10-01 04:28:37.863951 UTC 持久写入发表边界，然后点击一次发表。平台接受并创建作品 `6abde176000000001303e1e8`。2026-10-01 04:28:46.686411 UTC，CLI 返回 `COMPLETED/CONFIRMED/IN_REVIEW`。

独立只读列表和详情确认稳定作者等于绑定账号、类型 video、完整标题正文一致、公开隐私值 0、无话题/提及/定时，创建时间与本次发表时间吻合。真实详情的原始视频 MD5 等于上述源 MD5，规范远端素材 ID 与临时上传 Key 分别保留。详情核对通过全部列表的完整性及唯一性条件，没有仅凭“最新作品”确认。原始 MD5是独立平台字段，不冒称远端 SHA256。

`IN_REVIEW` 表示平台接受并进入审核。本次没有观察到审核通过，报告不将其标为 `PUBLISHED`。任务完成表示本次交付结果已独立确认，不表示审核通过。

## 实际正向创建回执及 CLI 恢复

同一次真实 POST `/web_api/sns/v2/note` 返回 HTTP 200，捕获的安全语义字段为：

```json
{"success": true, "result": 0, "data.id": "6abde176000000001303e1e8"}
```

旧解析器要求 `code=0`，未识别数字型 `result=0`；因此首次执行的 attempt.receipt_id 为空，虽然真实正向字段已保存为 `submission_response`，且独立详情已确认。首次结果保留了 `SUBMISSION_UNCERTAIN` 警告，作为当时解析失败的历史记录。

解析器现支持真实 `success=true/result=0/data.id`，保留已有 code/status 信封的兼容，拒绝布尔状态、非零或冲突状态、缺状态、无效/冲突 ID。真实字段通过当前解析器重验，以及本地完整交付/持久化流程测试。

新增 `jobs recover-receipt <job_id>` 持有账号维护锁，仅从当前 attempt 唯一保存的正向真实创建响应恢复回执；要求任务已经 `COMPLETED/CONFIRMED`、原 attempt 已完成且 submit_count=1，并且解析出的 ID 与独立确认的作品一致。缺响应、负向结果、布尔状态、错 ID 或错 attempt 拒绝，不从作品 ID 生成缺失回执。

2026-10-01 04:34:11.145128 UTC，真实 CLI 恢复成功，原 attempt 的 receipt_id 为 `6abde176000000001303e1e8`，新增 `receipt_recovered` 来源证据。原 attempt、提交/结束/核对时间、确认状态及发表计数均不变；警告保留。恢复只处理本地持久证据，没有网络或浏览器操作。

随后再次运行原正式 job，直接返回同一 `COMPLETED/CONFIRMED/IN_REVIEW` 结果，原核对时间和 attempt 不变，receipt_id 与作品 ID 一致，submit_count=1。此前未提交任务和仅准备任务没有追加发表。

## 能力与验收边界

能力版本 `1.2.1` 为 `available_limited`；准备、真实提交、独立详情、实际创建回执及同 attempt 的 CLI 回执恢复均有真实证据。当前正向真实回执通过“先保存响应及详情确认，再恢复回执”完成。修正后的解析器在首次正常发表内直接保存 attempt.receipt_id 的流程有同格式本地交付测试，尚未用另一份新内容执行。因此保留 `production_verified=false`，将缺口准确缩小为 `missing_evidence=["live_inline_receipt_persistence"]`，不再标记为缺少真实创建回执。

一次个人账号验证仅覆盖上述原创、非 AI、立即公开、无额外字段的视频范围。实际审核、挑战与平台页面变化不能由这些证据推导。该视频已提交，不为补充证据而再次发表。

最终执行 `pytest tests/test_xiaohongshu_live.py tests/test_xiaohongshu_delivery.py tests/test_job_cli.py tests/test_release_acceptance.py::test_capabilities_separate_offline_evidence_from_unverified_production -q --timeout=60`：**75 项通过，193.69 秒**。覆盖动态/乱序分片、64 片紧凑子进程结果、错误分片指纹、真实回执格式的本地完整交付、负向/布尔/冲突回执、缺响应/错 ID/错 attempt 的恢复拒绝、幂等回执恢复、CLI 接口和能力边界。

`pyright orchestrator tests/test_xiaohongshu_delivery.py tests/test_xiaohongshu_live.py tests/xiaohongshu_fixture.py tests/test_platform_tasks.py tests/test_release_acceptance.py` 零错误、零警告；`git diff --check` 通过。本次没有声称重新执行仓库全量测试。

本地数据库最终核查涉及 `3.mp4` 的六个正式/诊断任务，发表计数合计为 **1**，其余均为 0；原成功 attempt 唯一，回执 ID 与作品 ID一致，核对时间不变，源 MD5 与独立远端原始 MD5一致，完整文案一致。

完整浏览器会话、数据库与诊断资料仅保存在本地运行目录和 `.scratch`，本报告不包含稳定个人账号 ID、Cookie、上传令牌或带令牌 URL。
