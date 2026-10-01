# 抖音故障处理

先读[共用故障与恢复](../../social-auto-upload/references/recovery.md)。保留原 job/attempt/交付键和数据库；不要用反复上传确认是否成功。

单条未提交视频可用 headed + --verification-wait 1..600 保留原窗口等待本人验证。手机未收到推送不等于没有网页验证；提示用户查看原窗口，不再次点击发表。恢复需要同 attempt 的创建回执 ID 和上传 Vid，缺证据保持未知。

绑定冲突或身份不符时核对实际扫码账号；不同账号使用新别名。资源关闭未确认时核实真实关闭，不能删除锁、换 runtime-dir、重置数据库或绕开正式端口。最新平台限制见[使用说明](../../../docs/douyin-video.md)。
