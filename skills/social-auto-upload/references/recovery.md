# 故障与恢复

先保存原 job、attempt、状态、error 和提交次数，再确定下一步。不要因超时、浏览器关闭、缺回执或页面跳转就创建新键重新发表。

| 现象 | 处理 |
|---|---|
| 旧 upload-video / upload-note 返回迁移错误 | 改用已绑定账号的 `sau jobs` 清单；不调用旧类绕开迁移 |
| check=valid，但任务不识别账号 | 查询同一数据库的 accounts；由 agent 在线识别 ID 并绑定 |
| IDENTITY_MISMATCH / CONFLICT | 核对扫码账号；不同真实账号使用新别名，不覆盖绑定 |
| BrowserType.launch 缺 chrome.exe | 用同一项目环境 `python -m patchright install chromium` |
| previous browser shutdown has not been confirmed | 核实旧进程/浏览器/辅助子进程的真实关闭；未确认前不删除资源所有权标记、不换运行目录绕开锁 |
| 元数据/上传/处理失败，NOT_SUBMITTED | 修复实际原因，按错误的可重试性和尝试额度恢复原任务；不把可播放预览当处理完成 |
| UNKNOWN_COMMIT / MAY_HAVE_SUBMITTED | 仅对原 job 执行 run，正式端口只读核对，不重新上传或点击发表 |
| 本人验证、扫码、验证码 | 暂停账号，让用户合法完成验证；不能自动绕过 |
| UI_CHANGED 或未知弹窗 | 不随意关闭/删除覆盖层，保留不确定状态并检查正式实现 |

## 验证窗口

抖音和小红书的新未提交视频任务可在明确单 job、headed、非 prepare-only 时使用 `--verification-wait 1..600`。调用前据情况选择等待，向用户说明实际可见窗口，不保证手机会收到推送。保持同一 CLI 进程，用户完成验证后继续等待同次回执；不再次点发表。默认不开启。视频号暂不支持此等待参数。

会话刷新后，未提交的 BLOCKED job 可 `resume`；已经可能提交的 job 仍走原任务只读恢复。验证期间取消或超时不会给重发授权。

## 只读结果核对

查看 `status` 和 `attempts`，然后在支持的平台使用 `inspect-work`，或执行原未知任务的正式恢复。核对在线身份、精确标题/正文、独立媒体指纹、创建时间、实际作品 ID 和平台状态。历史任务仍可能保留首发时的 IN_REVIEW；后续查询的 ACCEPTED 应带观察时间分别说明，不手改历史状态。

同一清单重复 enqueue 和完成任务重复 run 应返回原 job/attempt，提交次数不增加。准备探针也可能处于可执行状态，不能对这些探针执行无 job 批量发布；不要只靠本地页面测试声称生产能力已验收。
