# Issue tracker: Local Markdown

用户已选择本地 Markdown tracker，并批准三平台无人值守发布的 15 张票及其依赖。

- 每个功能在 `.scratch/<feature>/` 内保存一份 `spec.md`。
- 每张实现票独立保存为 `issues/<NN>-<slug>.md`，按依赖顺序编号。
- 当前功能为 `.scratch/unattended-publishing/`；README 是进度索引，逐票文件是状态依据。
- `Blocked by` 记录直接阻断票；只有阻断票全部完成才可实施。
- `Status` 初始为 `ready-for-agent`；领取后为 `in-progress`；验收和审查完成后为 `done`。
- 发布到 tracker 指写入这些本地文件，不创建外部 Issue，不修改上游远端。
- 验证结果、实现提交和限制附在各票下方；规格的 ready-for-agent 不代表现有系统生产就绪。
- 当前目标为完成全部 15 张票，按依赖推进；已完成票仍需在集成验收时保持通过。简单或中等任务可委派给 gpt-6-sol，MAX 推理。
