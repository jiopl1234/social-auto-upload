# 03 号票验证记录

日期：2026-09-26。基线 `15e1167`；核心实现 `f91f3e7`；维护入口及记录校验 `e3ececb`；嵌套清理修复 `01e5268`。

公共 TaskService 已贯通串行准备、事务领取、attempt 和事件记录、账号暂停查询。平台尚未接入时不调用旧上传流程；READY 表示准备结束，平台结果仍为空。三平台原有 CLI 登录、检查及五条上传入口与 worker 共用操作系统锁。

## 验收证据

- 公共任务测试验证 QUEUED → PREPARING → READY、唯一 attempt、真实数据库查询一致，未推断作品结果。
- 资源关闭未确认的测试先复现错误 READY，再修复为 BLOCKED，并在同一事务内暂停账号；其他平台仍运行。
- AUTH_REQUIRED 暂停后，同账号任务留在队列且没有 attempt，其他平台继续执行。
- 外部故障改变 attempt 所有权后，旧执行器的写入和结束被阻止，原状态没有被覆盖。
- 真实子进程验证同账号竞争、过期时间戳、跨数据库别名、维护占锁和其他平台并行可用。
- 杀死持有已登记子进程的 worker 后，仍存活的资源阻止接管；即使该已知子进程随后退出，不能证明其他资源关闭时仍保持阻断。测试通过进程启动身份核验清理自己创建的进程。
- 三平台 11 个公共维护/上传入口在外部进程占锁时被拒绝；上传检查和上传期间持续持锁。受控会话文件相互隔离。
- 抖音 context.close 失败后仍执行 browser.close，并把清理失败向维护调用者报告；原业务异常优先保留。
- 损坏的所有权记录先复现 TypeError，再修复为 RESOURCES_UNCONFIRMED；没有领取新任务。
- Spec 审查发现小红书/视频号登录会吞掉内层 cookie 检查浏览器的关闭错误。新增 8 种公共登录场景，修复前 6 种失败；修复后全部通过。内层、外层、同时关闭失败均保留资源阻断；外层独立尝试两项关闭且不覆盖内层错误。
- 所有测试通过临时 SAU_RUNTIME_DIR 隔离，不使用用户会话。子进程握手和测试总耗时均有界。

```text
uv run --frozen --with pytest --with pytest-timeout python -m pytest tests -q --timeout=30 --tb=short --junitxml=docs/audit/ticket-03-pytest.xml
uv run --frozen --with pytest --with pyright pyright orchestrator sau_cli.py tests/test_job_cli.py tests/test_job_worker.py tests/test_job_processes.py tests/fixtures/job_worker_process.py tests/test_account_maintenance.py tests/conftest.py
git diff --check
```

嵌套清理修复后的完整测试：**85 passed, 20 subtests passed，23.56 秒**。定向类型检查：0 errors、0 warnings。差异检查通过。三个完整历史上传器并未在本票整体修复类型债务；子代理对旧上传器的额外 Pyright 检查仍有已有报错，不能把这里的定向通过理解为全仓库类型通过。

原始结果：[文本](ticket-03-pytest.txt)、[JUnit XML](ticket-03-pytest.xml)。本票验证在 Windows 执行；POSIX 锁分支尚未在本次环境运行。没有访问真实平台账号。严格清单、提交恢复、阶段截止由 04–06 号票接入。
