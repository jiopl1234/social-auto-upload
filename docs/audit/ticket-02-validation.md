# 02 号票验证记录

日期：2026-09-26。基线：`3d8f076`。范围：公共任务 CLI、持久入队、查询和交付键去重。

新增 `sau jobs`，使用真实 SQLite 事务保存 jobs、attempts、events 和账号绑定表。入队和查询返回统一 JSON；作品 ID、实际元数据、核实时间等未知数据为 null。尚未接入的 `run` 明确返回 UNSUPPORTED，不调用旧上传器。接口与状态约定见 [任务说明](../tasks.md)。

## 证据

- 首个公共子进程测试先因 CLI 没有 jobs 命令失败，再通过实现；新进程查询返回完全相同的任务和请求摘要。
- 同键相同清单（包括 JSON 字段重排）返回原任务；同键不同正文返回 CONFLICT，不新增任务或事件。
- 在事件写入处注入 SQLite ABORT，公共查询没有半个任务；移除故障后入队只有一个初始事件。
- 四个独立进程同时入队。测试揭示首次切换 WAL 时的 `database is locked` 竞争；修复为有界重试后，存储子代理连续 20 个新数据库运行均通过，完整测试也通过。
- 无效 JSON、命令缺参、不存在任务及未来数据库版本均产生可解析 JSON 错误和相符的非零退出码。
- 旧平台 CLI 启动测试仍使用无第三方包子进程，只增加复制新的标准库任务包；不因此加载浏览器上传器。
- Spec 审查发现机器帮助仍输出纯文本。新增公共测试先复现 JSON 解析失败，再将 `--json --help` 改为 HELP JSON；父命令与子命令帮助均可解析，且不创建数据库。

```text
uv run --frozen --with pytest --with pytest-timeout python -m pytest tests -q --timeout=30 --tb=short --junitxml=docs/audit/ticket-02-pytest.xml
uv run --frozen --with pyright pyright orchestrator sau_cli.py tests/test_job_cli.py
git diff --check
```

审查前完整测试：56 passed，20 subtests passed，18.34 秒。帮助输出修复后的最终完整测试：**57 passed, 20 subtests passed**，15.76 秒。定向 Pyright：0 errors，0 warnings。差异检查通过。

另外从临时目录直接调用已安装的 `.venv/Scripts/sau.exe`，确认新任务包被 editable 安装收录；公共 enqueue 返回 QUEUED，作品 ID 为 null。该补充检查使用现有安装，不能冒充一次新的完全隔离依赖安装。

原始结果：[文本](ticket-02-pytest.txt)、[JUnit XML](ticket-02-pytest.xml)。

没有使用真实账号、浏览器发布页面或执行平台写入。本票完成只证明持久入队契约；任务执行、素材预检、互斥、提交边界和恢复依照后继票推进。
