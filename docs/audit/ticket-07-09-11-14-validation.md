# 视频适配与诊断验证

日期：2026-09-26；固定基线 `1f8d72f`。07、09、11、14 仍待独立 Standards / Spec 复审。执行环境为 Windows，正式 uv 锁定依赖，Patchright 1.58.2 和本机 Chrome。

## 实现范围

三条视频路径共用 BrowserDeliveryAdapter 和 DomPagePort；分别登记旧代码中可定位的编辑控件候选。每个尝试独立浏览器/页面端口，准备与提交前确认稳定账号，逐素材正向处理完成，精确字段和声明读回，单次提交后查询独立作品记录。已有 worker 持有 SUBMITTING 事务和恢复策略。

生产平台的稳定账号 ID、处理完成及作品详情证据尚未得到真实页面验证，默认端口为 restricted，CLI 在浏览器启动前返回 UNSUPPORTED。没有开启生产限制的命令行开关。合成 fixture 的证据读取器只能运行在本地来源，外部 HTTP 请求被拦截；它不是三个创作者后台 DOM 的复制或实测证据。抖音图文、小红书图文暂待 08、10 号票。

旧视频 upload 及直接最终发表流程删除循环点击、未知浮层移除、默认 AI/无条件转载和 URL 推断成功，改为明确迁移错误。构造器、旧参数解析保持原语义；不能凭旧参数推断缺失的内容事实或稳定账号绑定。手动登录/维护入口保留。旧视频号实名等待现在立即 AUTH_REQUIRED，不导出二维码，也不把弹窗消失当作验证成功。

14 的会话私有 ACL、原子替换、诊断白名单、按 job/attempt 生成的独立证据和明确清理入口见 `ticket14-evidence.md`。Root 将浏览器版本、能力版本、失败摘要接入证据引用；诊断落盘失败不覆盖原错误。保留策略是显式 `jobs prune-evidence` 删除超过 30 天的托管诊断，未安装后台定时任务。

## 验证与修复

- TDD：首个限制场景因缺少 platforms 模块失败；实现默认限制后通过。正常视频 DOM 场景因缺少 BrowserDeliveryAdapter 失败，接入后通过。
- 本地 ThreadingHTTPServer 接收 DOM 实际提交内容并单独保存作品，正常、错账号、空白身份、上传后/填写后验证、永久处理、错误候选、必填声明、标题读回不符、未知弹窗、回执丢失、多候选和 false-success 均通过公共 TaskService 验证。
- CLI capabilities / prune-evidence 先因未注册动作失败，再完成 JSON 公共接口。旧 upload 绕过协议的测试先失败，删除旧最终流程后通过。
- 集成阶段 53 项 CLI/平台/声明/实名/诊断/浏览器测试通过（71.13 秒）。所选 orchestrator、CLI、日志和改动测试的 Pyright 为 0 errors / 0 warnings。
- 首轮全套 259 passed、20 subtests passed、1 failed（141.27 秒）：旧批次断言把“没有远端作品证据”写成“没有任何证据”。现有诊断引用属于本票新需求，断言改为仅允许 diagnostic 且 content_id 为空，仍确认 UNKNOWN_COMMIT 和不重发；该回归已单独通过。最终全套结果将在复审关闭前补充。
- 旧抖音 upload 浏览器关闭单测改为确认迁移错误在创建浏览器前发生；真正的任务资源关闭故障仍由 BrowserSession / 公共任务测试覆盖。视频号原先“保存 QR 后等待扫描”的测试按已批准规格改为“立即认证受阻且不输出 QR”，没有把旧错误期望改成成功。

图证据：codebase-memory Tier 2，项目 `C-Users-Chinese-social-auto-upload`，generation `2026-09-26T16:12:52Z`。关键平台方法查询 11/11 无分页；所有改动代码/测试与平台目录做 coverage 检查，代码无记录缺口但 metadata_changed，依据当前源文件和提交 diff；fixture 和文档被图排除，直接读取。旧上传器账号/作品缺失结论仅限已审计的三个 main.py，不声称整个仓库穷尽。

本票没有访问真实创作者平台、使用真实 Cookie、请求扫码或真实发表。不提供零风控或远端 exactly-once 保证。
