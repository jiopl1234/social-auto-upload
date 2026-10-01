---
name: social-auto-upload
description: 使用 social-auto-upload 新版 sau jobs CLI 管理抖音、小红书和视频号的视频发布，包括扫码登录后的账号识别与绑定、仅准备验收、完整首发、结果核对和原任务恢复。用于操作这个项目，不用于泛化的社交平台选型或风控规避研究。
---

# social-auto-upload 任务发布

在仓库根目录执行命令，以当前 `sau jobs --json capabilities` 和 `docs/*-video.md` 为能力依据。抖音、小红书和视频号发布使用 `sau jobs`；旧 `upload-video` / `upload-note` 的参数仍可能出现在帮助中，但这些平台的旧发表入口会返回迁移错误。视频号的 CLI 平台名是 `tencent`。

## 工作流程

1. 确定平台、账号别名、素材、公开/仅准备意图以及用户已提供的事实。发布授权和原创/AI 声明按当前内容及会话继承；不要重复询问已确认的信息，也不要把过去一条视频的声明套到所有未来素材。
2. 保持任务数据库、运行目录和交付键连续。先查询已有账号/任务；同一发布意图复用原键或原 job，不能因为命令失败就换键。默认数据库与用户显式选择的数据库是不同文件。
3. 新账号使用新别名，扫码后由 agent 读取在线稳定 ID 并执行绑定，不要求用户手动翻开发者工具。操作方法见[账号识别与绑定](references/accounts.md)。`check=valid` 只表示登录状态有效，不能替代任务账号绑定。
4. 根据视频画面拟定获授权的标题/正文；按能力支持范围写 UTF-8 清单。平台不支持的字段不能悄悄省略或改成另一个发布意图。
5. 先 `enqueue`，再对返回的唯一 `job_id` 执行 `run --job ...`。真实首发验收直接执行普通 run；`--prepare-only` 会占一次尝试，且只返回准备完成，不是发表。
6. 核对状态、同 attempt 回执/正向确认、独立作品详情和累计提交次数。结果不确定时只恢复原任务；遇到验证、身份不符、界面变化或资源清理失败则停止相应账号。

命令、清单和状态含义见[任务接口](references/task-cli.md)。环境和浏览器问题见[运行前提](references/runtime.md)及[故障处理](references/recovery.md)。

## 执行边界

- 使用明确授权的平台、账号和内容；调用本技能不等于授权新增公开发表。已有明确公开发布指令无需再确认。
- 针对单条请求使用 `run --job`，不要执行会领取其他待办任务的无 job 批量 run。
- 不绕过扫码、实名或验证码；不通过伪造指纹、代理轮换或调用旧 uploader 规避任务检查。不承诺无风控。
- 不删除数据库、账号绑定或资源所有权标记来恢复任务，不手工修改成功状态/回执/提交次数。
- 不将 Cookie、原始认证响应、二维码、上传凭证或签名媒体 URL 放入技能、公开文档、Git 或普通日志。
- CLI 的 JSON 在 stdout，验证通知在 stderr；PowerShell 调用 Python 子进程时用 UTF-8，避免中文内容被 GBK 解码后损坏。

## 平台路由

| 平台 | 平台技能/真实范围说明 |
|---|---|
| 抖音 | [douyin-upload](../douyin-upload/SKILL.md)、[使用说明](../../docs/douyin-video.md) |
| 小红书 | [xiaohongshu-upload](../xiaohongshu-upload/SKILL.md)、[使用说明](../../docs/xiaohongshu-video.md) |
| 视频号 | [tencent-upload](../tencent-upload/SKILL.md)、[使用说明](../../docs/channels-video.md) |
| Bilibili / 快手 | [bilibili-upload](../bilibili-upload/SKILL.md) / [kuaishou-upload](../kuaishou-upload/SKILL.md)，仍是独立旧 CLI 路径，不具备本任务接口的防重发和恢复证明 |

## 模板

`scripts/task_example.py` 默认只打印一个 CLI 命令；传 `--execute` 才执行。平台示例同样不会默认连续登录并发表多个内容。
