# 小红书故障处理

先读[共用故障与恢复](../../social-auto-upload/references/recovery.md)。保留原 job/attempt/交付键和数据库；不要用反复上传确认是否成功。

HEVC 的解码预览不等于平台处理完成；网络/处理失败时不得发表。单条未提交视频可用 headed + --verification-wait 1..600，但真人验证等待仍需按真实结果报告。inspect-work 不支持 xiaohongshu；通过原任务详情恢复核对。recover-receipt 只重解析已完成原尝试保存的回执，不会发表，也不能替代未完成的即时回执验收。

绑定冲突或身份不符时核对实际扫码账号；不同账号使用新别名。资源关闭未确认时核实真实关闭，不能删除锁、换 runtime-dir、重置数据库或绕开正式端口。最新平台限制见[使用说明](../../../docs/xiaohongshu-video.md)。
