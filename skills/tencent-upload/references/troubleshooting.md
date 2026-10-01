# 视频号故障处理

先读[共用故障与恢复](../../social-auto-upload/references/recovery.md)。保留原 job/attempt/交付键和数据库；不要用反复上传确认是否成功。

视频号不支持 verification-wait。正向创建回复可能没有作品 ID；同 attempt 的正向确认 + 完整自有列表 + 独立详情/原视频 MD5 唯一匹配才完成。receipt_id=null 是真实格式，不应从列表补造回执。新准备的续传缓存由正式端口只过滤特定上传项，不手动清空认证和设备状态。

绑定冲突或身份不符时核对实际扫码账号；不同账号使用新别名。资源关闭未确认时核实真实关闭，不能删除锁、换 runtime-dir、重置数据库或绕开正式端口。最新平台限制见[使用说明](../../../docs/channels-video.md)。
