# 定时延期保留取消信号

修复基线：`61030dd004202ae0a4958074724be33664f3b6c6`。用户复审发现 Spec P1：时钟回拨触发 NOT_DUE，清理期间取消执行器，延期分支清空 CANCELLED 警告，随后继续提交第二项。

固定修复提交：`8a12e9ef5a36add341d3e338a7010170be74469d`。独立复审与干净归档验收均使用此提交。

## 复现与修复

直接导入用户提供的 `reproduce_deferred_cancel.py`，只运行其中的本地 probe，没有改写原脚本或其结果文件。修复前输出为 `[QUEUED, COMPLETED]`、第二项写入 1 次且有 1 次 attempt；两项 warnings 都为空。

公共任务接口的新回归先在原实现失败。修复后，worker 将本次警告交给延期事务；Store 在 QUEUED 与 ATTEMPT_LIMIT 两个分支都保存经白名单处理的警告，并将其写入关联当前 attempt 的 schedule 事件。`run_pending` 因此能识别取消并结束本轮。调度状态、尝试上限、提交边界和显式恢复语义保持一致，不需要数据库迁移。

新回归覆盖两个分支：取消后只返回第一项，第二项保持 QUEUED、无 attempt、零提交；重启后可查询 CANCELLED 警告及调度事件；显式再次运行可完成第二项，延期任务到期后仍能执行且不继承旧取消警告。

新增 2 项测试通过；核心、CLI 与修改测试的 Pyright 为 0 errors、0 warnings、0 informations。用户原复现脚本在修复后返回 `[QUEUED]`、持久保留 CANCELLED 警告，第二项提交数与 attempt 数均为 0；[原始输出](deferred-cancellation-2026-09-27-clean/reproduction-after.json) 已保存。

## Standards

独立只读复审未发现明确标准违规或值得整改的代码异味。worker 将清理阶段的警告传入延期事务；Store 在 QUEUED 和 ATTEMPT_LIMIT 两分支保存经白名单处理的取消警告及调度事件，执行器因而停止领取。两种结局及显式恢复均有公共接口测试覆盖。

本轮图工具返回 Transport closed；审查依据固定提交差异和当前源码，未运行全仓测试或真实平台验证。

## Spec

独立只读复审未发现遗漏、错误实现或范围膨胀。延期的两个重试上限分支都持久保留取消警告，使执行器停止领取下一任务；新尝试清除当前警告，同时保留历史事件。此行为符合 `spec.md` 第 67、69、108、118 行、`docs/tasks.md` 第 96–98 行及 06/12 号票。

本轮图工具返回 Transport closed，结论依据当前源码；审查者未运行全仓测试。两轴结果：Standards 0 项发现；Spec 0 项发现。

## 干净环境完整验收

源代码通过 git archive 固定为 `8a12e9e`，使用全新虚拟环境、锁定离线依赖及独立运行目录；配置只来自 `conf.example.py`，没有复制用户会话。`uv sync --frozen --offline --group dev` 安装 27 个锁定依赖，再运行 `scripts/check_offline.py` 的统一入口，退出码 0。

- 环境：Windows、Python 3.12.10、Patchright 1.58.2、Chrome 152.0.7977.83、pytest 9.1.1、Pyright 1.1.414。
- 完整测试：369 passed、20 subtests passed，406.33 秒；0 failures、0 errors、0 skipped。JUnit 合计 389，包含子测试。
- 核心与 CLI 类型检查：0 errors、0 warnings、0 informations。
- 原始证据：[环境](deferred-cancellation-2026-09-27-clean/environment.txt)、[测试输出](deferred-cancellation-2026-09-27-clean/offline-pytest.txt)、[JUnit](deferred-cancellation-2026-09-27-clean/offline-pytest.xml)、[类型检查](deferred-cancellation-2026-09-27-clean/offline-pyright.txt)。

此轮 P1 已修复，06/12 的补充验收关闭。该结论对应本次修复及上述测试范围。

## 代码证据范围

本轮按 codebase-memory 技能尝试 index_status、search_graph 和 check_index_coverage，均返回 Transport closed，无法取得当前图代次和覆盖结果。仓库没有 `.codegraph/`。已回退直接读取 service、worker、store、runtime、diagnostics 和相关测试源码，并通过源代码搜索确认延期的调用与持久化路径；没有声称获得本轮 MCP 图验证。

只使用真实临时 SQLite、受控时钟、本地替身和离线测试素材；没有真实平台访问或发布。
