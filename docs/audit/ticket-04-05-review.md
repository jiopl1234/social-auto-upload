# 04、05 号票独立双线审查

固定基线 `7deffa063cf076d5a94e16710442a3273b871ffb`，首轮固定提交 `d72900b1ea5ce36865e804371dc99a439ffd1bba`。

命令：`git diff 7deffa0...d72900b`。提交列表：`d72900b feat: freeze publishing manifests and recover uncertain deliveries`。两位未参与实现的代理分别进行只读 Standards 与 Spec 审查，均依据固定 diff 和当前源码补充图证据；没有重复运行整套测试。

## Standards

初审 3 项，最高 P2：

- P2 文档规则：`docs/agents/issue-tracker.md` 要求每票记录验证结果、实现提交和限制。04、05 当时仅标 in-progress，详细证据只有独立 audit 文件。已在各票补入记录，验收与审查完成前不标 done。
- P3 判断项，Primitive Obsession：平台能力通过六元位置元组表达。已改为冻结的具名 `Capability` 记录，字段行为保持不变；22 项清单测试及该模块 Pyright 通过。
- P3 判断项，Divergent Change：`worker.run_once` 集中处理多个阶段。已明确移交 06 号票，在加入总截止与阶段预算时提取阶段操作，同时保持持久提交边界清晰；不构成本票阻断。

## Spec

初审 2 项，最高 P1，无明确范围扩张：

- P1：准备结果只要求为 JSON 对象，缺少与不可变清单、必需字段和降级策略的比较。改写后的内容可能进入提交，并以同一错误实际元数据完成核对。要求来自“对标题、正文、话题、封面、合集和图片数量验证最终结果”“用户指定的必需字段默认不能降级”。已增加 SUBMITTING 前七字段精确比对，缺失、变更或数量不符返回字段错误；显式允许的可选遗漏记录实际值和持久警告。公共红测转绿，等待独立复审。
- P2：声明键只允许复制内容归属、AI 使用和转载来源，无法表达独立的平台权益。要求来自“原创归属、AI 使用和平台权益声明相互独立”。已增加显式 `originality_rights` / `repost_authorized`，不默认赋值、不推导 AI 事实；入队与实际读回测试通过，等待独立复审。

复审与最终处置待修正提交完成后记录。当前不据此宣布两票完成。
