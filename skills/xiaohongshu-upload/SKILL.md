---
name: xiaohongshu-upload
description: 使用 social-auto-upload 的 sau CLI 完成小红书扫码登录、在线账号识别与绑定、有限视频发布、仅准备验收及原任务结果核对。新版发布入口为 sau jobs；不将旧 upload-video 或 upload-note 帮助条目当作可执行发表能力。
---

# 小红书任务发布

先读取[共用任务技能](../social-auto-upload/SKILL.md)，按当前 CLI capabilities 判断能力。普通操作优先 CLI，无需先读 uploader 源码。

当前有限范围为一条原创、无 AI、至多 32 MiB 的视频，标题/正文、立即公开和默认封面，不支持额外话题/合集/自定义封面/声明。图文仍 restricted。新解析器同次回执即时持久化仍待新内容线上验收；以当前 capabilities 为准。

## 执行要点

- 登录/检查仍使用 `sau xiaohongshu login --account ALIAS --headed` 和 `sau xiaohongshu check --account ALIAS`。
- 扫码只保存会话；agent 按[账号流程](../social-auto-upload/references/accounts.md)读取在线稳定 ID，再调用正式 bind-account。不同账号用不同别名，用户不必手动找 ID。
- 发布用不可变清单的 `platform="xiaohongshu"`、正确 account 和事实声明，先 enqueue 再指定 job run。旧发表入口返回迁移错误，禁止用旧类代替。
- 标题/正文由实际素材和已授权要求确定。`READY / NOT_SUBMITTED` 只代表准备；`UNKNOWN_COMMIT` 只允许原任务恢复，不换键重发。
- 保留固定数据库和运行目录，检查 submit_count，分别报告任务状态与平台审核/接收状态。

平台细节见[使用说明](../../docs/xiaohongshu-video.md)；命令见[CLI 契约](references/cli-contract.md)，环境见[运行前提](references/runtime-requirements.md)，失败见[故障处理](references/troubleshooting.md)。

## 示例

`scripts/examples/xiaohongshu_cli_template.py` 默认预览一条命令，`--execute` 才执行。PowerShell/bash 包装也遵循这一规则。先完成绑定；示例不会自动连续登录、发布视频和发布图文。

```powershell
uv run --frozen --offline --no-sync python skills/xiaohongshu-upload/scripts/examples/xiaohongshu_cli_template.py capabilities --db C:/Users/Chinese/.sau/tasks.sqlite3
```
