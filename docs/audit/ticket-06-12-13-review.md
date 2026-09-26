# 06、12、13 号票双线审查

固定基线：`6bf4d5868203a46578dddbf3d4925ea43546e405`；首轮候选：`1344409`（含实现 `f4d9f76`）。比较命令：`git diff 6bf4d58...1344409`。两个独立只读子代理使用 gpt-6-sol MAX，分别审查 Standards 和 Spec。没有以本地测试替代真实平台验证。

## Standards

首轮确认 1 项 P2，另有 2 项非阻断 P3 判断：

1. **P2 — Schema compatibility。** run_at 已改变领取语义，但数据库仍为 schema v2；旧版 v2 worker 会忽略启动时间。要求升级数据库版本，使旧二进制拒绝打开新库，避免回退后提前执行未来任务。
2. **P3 — Duplicated Code。** fanout 中重复 facts/declarations/field_policy 字段名；建议与 manifest 共用定义，避免校验漂移。
3. **P3 — Feature Envy。** fanout 直接创建 schema、查询 jobs/accounts 并调用私有行解码器；建议把持久化和迁移放在 Store 后面，分发模块保留编排与汇总。

逐票状态与候选证据符合 tracker 规则。修复已提交为 `da9bb22`：schema v3 与 v1/v2 升级路径、Store 分发方法、共享字段定义。已用真实 `git show 6bf4d58:orchestrator/store.py` 加载旧 Store：旧版拒绝新 schema v3，报告 STORAGE_ERROR。独立复审确认三项关闭，修复 diff 未发现新代码回归；要求更新本文的过时候选状态，已更新。

## Spec

首轮确认 1 项 P2、1 项 P3：

1. **P2 — cleanup can lose its reserved time。** 提交前同步 preflight 可能长时间散列素材，超过总截止后清理预算变为 0，导致可立即关闭的浏览器也未调用 close。违反规格“所有阶段共享总截止时间并有阶段预算”和资源清理要求。`da9bb22` 把账号绑定、SQLite、会话读取及媒体摘要预检移入可终止的只读子进程，纳入累计 preflight 预算；取消后确认退出或保留资源阻断，清理仍有预留时间。独立复审确认关闭。
2. **P3 — batch publish_at has the wrong public error category。** 批次公共字段或目标覆盖中出现 publish_at 返回 INVALID_INPUT，而 12 号票要求 UNSUPPORTED。`da9bb22` 修复公共字段与目标覆盖的错误分类，公共服务及 CLI 四项回归通过。独立复审确认关闭。

复审范围：`git diff 1344409...da9bb22`；整体范围：`git diff 6bf4d58...da9bb22`。完整回归为 **236 passed、20 subtests passed（109.44 秒）**，所选范围 pyright **0 errors、0 warnings**。两个审查者只读复核修复，未重复主执行器的完整测试。

首轮总计：Standards 3 项，最重 P2；Spec 2 项，最重 P2。最终未关闭项：Standards 0、Spec 0。06、12、13 达到本地验收条件；未验证真实平台发布。
