# 定时延期保留取消信号

修复基线：`61030dd004202ae0a4958074724be33664f3b6c6`。用户复审发现 Spec P1：时钟回拨触发 NOT_DUE，清理期间取消执行器，延期分支清空 CANCELLED 警告，随后继续提交第二项。

## 复现与修复

直接导入用户提供的 `reproduce_deferred_cancel.py`，只运行其中的本地 probe，没有改写原脚本或其结果文件。修复前输出为 `[QUEUED, COMPLETED]`、第二项写入 1 次且有 1 次 attempt；两项 warnings 都为空。

公共任务接口的新回归先在原实现失败。修复后，worker 将本次警告交给延期事务；Store 在 QUEUED 与 ATTEMPT_LIMIT 两个分支都保存经白名单处理的警告，并将其写入关联当前 attempt 的 schedule 事件。`run_pending` 因此能识别取消并结束本轮。调度状态、尝试上限、提交边界和显式恢复语义保持一致，不需要数据库迁移。

新回归覆盖两个分支：取消后只返回第一项，第二项保持 QUEUED、无 attempt、零提交；重启后可查询 CANCELLED 警告及调度事件；显式再次运行可完成第二项，延期任务到期后仍能执行且不继承旧取消警告。

初步验证：新增 2 项测试通过；核心、CLI 与修改测试的 Pyright 为 0 errors、0 warnings、0 informations。完整验收与独立两轴复审结果待补齐。

## 代码证据范围

本轮按 codebase-memory 技能尝试 index_status、search_graph 和 check_index_coverage，均返回 Transport closed，无法取得当前图代次和覆盖结果。仓库没有 `.codegraph/`。已回退直接读取 service、worker、store、runtime、diagnostics 和相关测试源码，并通过源代码搜索确认延期的调用与持久化路径；没有声称获得本轮 MCP 图验证。

只使用真实临时 SQLite、受控时钟、本地替身和离线测试素材；没有真实平台访问或发布。
