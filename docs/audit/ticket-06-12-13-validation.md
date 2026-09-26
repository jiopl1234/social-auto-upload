# 06、12、13 号票验证记录

验证日期：2026-09-26。仓库：`C:/Users/Chinese/social-auto-upload`。本记录为本地实现候选；逐票完成状态须等待双线审查关闭。

## 公共行为

- 06：统一总截止、累计阶段预算与预留清理时间；提交前取消记录 CANCELLED，提交后取消保留 UNKNOWN_COMMIT，后续只读核对。清理受到用户取消保护，同时保留主要错误、逐资源诊断和账号资源阻断。页面关闭归类为 PAGE_CLOSED。丢失写入所有权后仍关闭本执行器创建的资源。
- 06：BrowserSession 沿用 Patchright 1.58.2，覆盖 context 创建、初始化、会话保存和关闭失败。旧 CLI 的资源标记与小红书/视频号关闭策略提取到共享 helper，锁仍覆盖完整的检查及上传。
- 12：显式本地时间、IANA 时区、UTC 和 fold 持久化；DST 不存在时间拒绝，重复时间必须指定 fold；未到期无尝试，准备期间时钟回拨安全延期，尝试总上限仍有效。提交后的只读恢复不受原计划时间限制；publish_at 继续拒绝。
- 13：批次意图、每目标规范化快照及验证错误先原子保存，再创建子任务。重启和重复请求关联原任务，素材变化不会改写快照。逐目标独立错误、暂停、取消、拒绝与提交不明可查询；已成功目标不会重发。已验证公共批量定时输入与目标覆盖。

## 先失败再修复的证据

- 首个超时测试在公共 run_once 上以缺少 limits 参数失败；实现后按预算返回 TIMEOUT 且关闭适配器。
- 重复上传操作原先各自重置阶段预算，测试得到 COMPLETED；累计预算修复后在提交前 TIMEOUT。
- 清理期间取消原先中断关闭并返回 BLOCKED；有界 shield 后得到 CANCELLED 且实际关闭完成。
- 调度中时钟回拨原先返回 FAILED_PRE_SUBMIT；实现安全延期与上限后得到 DEFERRED，再到 ATTEMPT_LIMIT。
- 批量结构化 run_at 原先被字符串校验拒绝；共享规范化路径接通后到期领取与 UTC 查询通过。
- 小红书、视频号登录时 new_context 失败原先泄漏已创建 browser；公共 CLI 故障测试修复后确认 browser.close 执行。
- 完整回归发现清理错误地依赖当前写入所有权，导致失去所有权后 close 未调用；清理改为允许关闭自有资源，原有 fencing 测试恢复通过。

## 验证命令与结果

```powershell
uv run --frozen --with pytest --with pytest-timeout python -m pytest tests -q --timeout=30 --tb=short --junitxml=docs/audit/ticket-06-12-13-pytest.xml
```

结果：**225 passed、20 subtests passed，47.77 秒**。包含真实 SQLite、多进程崩溃/互斥、CLI 子进程、可控时间、状态式发布目标、合成资源故障及真实浏览器本地 HTML。文本与 XML 输出保存在同目录。

```powershell
uv run --frozen --with pytest --with pyright pyright orchestrator sau_cli.py tests/test_runtime_limits.py tests/test_browser_lifecycle.py tests/test_scheduling.py tests/test_fanout.py tests/test_account_maintenance.py tests/test_job_cli.py tests/test_job_worker.py tests/test_job_processes.py tests/test_manifest_preflight.py tests/test_job_delivery.py tests/test_metadata_readback.py tests/task_fixtures.py
```

结果：**0 errors、0 warnings**。该范围不声称所有历史 uploader 文件已满足严格类型检查。Windows 子进程结果文件短暂共享冲突改为在原有有界轮询中重试，没有延长等待上限。

## 图证据与直接源码

codebase-memory-mcp 项目 `C-Users-Chinese-social-auto-upload`，Tier 2，generation `2026-09-26T15:07:20Z`。关键符号查询 13/13，无后续页；worker.run_once 双向一跳追踪 27 个 callees、0 个静态 callers，无截断。TaskService 动态委派由直接源码确认。所有改动代码、配置和测试路径做过 coverage 核查；现有代码无记录缺口但 metadata_changed，因此依据当前源码与 diff。tests/fixtures 与 docs 被排除，已直接读取；uv.lock 直接核对。未据图作全仓完备性断言。

## 环境与限制

本地 HTML 实际使用 Patchright 1.58.2 和已安装 Chrome 152.0.7977.83，验证处理超时、页面关闭后分类以及同账号后续任务。当前机器没有 bundled chromium-1208，因此没有把 Chrome 的结果冒称为该构建已验证。该测试在没有可用 Patchright 或本机 Chromium 可执行文件时会显式 skip。

未连接三平台创作者后台，未使用真实账号、扫码或发布。五条生产平台路径仍待后续票接入和标记验证状态。预算针对协作式异步驱动操作；第三方阻塞扩展或自行忽略取消的插件没有被宣称可强制终止，未确认退出时资源阻断继续保留。旧 CLI 的共享资源 helper 不把旧发布算法升级为本任务协议。
