# xiaohongshu-mcp 公开风险证据核对

查证日期：2026-10-09。日期表中的 GitHub 时间均按 API 的 UTC 日期记录；状态为查证时状态。研究对象是 `xpzouying/xiaohongshu-mcp`，不把 README 推荐的另一仓库 `xpzouying/x-mcp` 当成本项目。本报告仅查公开材料，没有运行该项目、登录小红书或操作任何账号。

## 可以成立的结论

**“xiaohongshu-mcp 不会风控/封号”不是公开证据支持的结论。** README 的风险段是作者对旧项目及自己账号的历史经验。更直接的是：仓库 OWNER 在 2026-07-28 的回复中承认“风险确实存在，不能说没有”。该回复同时介绍改进、短期测试正常和使用建议，没有给出风险为零的保证。[README 固定版本](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/README.md)、[OWNER 回复 #316](https://github.com/xpzouying/xiaohongshu-mcp/issues/316#issuecomment-5100848949)

**问题区存在与自动化操作有关的通知截图，不能一概归为内容不原创或 AI 生成内容。** 本次亲眼核对 #680、#728、#777 的图片：均将第三方工具/脚本自动浏览、查看或发布，以及模拟真人操作列为通知内容。这与用户提供的“AI托管进行发文/互动，与真人操作习惯不符”属于相近的操作行为问题；本次没有在这些截图中找到与用户通知逐字相同的全文。截图仍是使用者上传的材料，真实性、完整上下文和实际触发原因未由平台独立确认。[#680](https://github.com/xpzouying/xiaohongshu-mcp/issues/680)、[#728](https://github.com/xpzouying/xiaohongshu-mcp/issues/728)、[#777](https://github.com/xpzouying/xiaohongshu-mcp/issues/777)

**现有材料不能比较两个工具的真实封禁率，也不能确定用户本次具体触发了哪项检测。** 没有统一账号条件、内容、网络、频率、版本或观察期的对照试验。旧版本报告不能直接证明今天的版本会出现同样结果，维护者测试账号正常也不能证明其他账号安全。

## README 到底说了什么

以主研究固定的提交 `7797fd375aba02dcd33afe143f6bee13e5870859` 为引用版本（主研究记录提交时间为 2026-10-09T10:31:57+08:00）：

- 风险段：作者称原来的另一个项目运行一年多未遇到封号；自己用 Claude Code 自动化运营数周后才开源。这是作者使用经历，没有样本规模、审计记录或风险上界。
- 每日 50 篇来自作者实操经验，未给出小红书官方额度依据；不能当作安全发布配额。
- 常用浏览器、本地网络、无服务器 IP 风险等宣传出现在另一个 `x-mcp` 插件的推荐段，不能据此描述本仓库全部部署方式。
- README 另有实名、网页端重复登录、引流和搬运等提醒；这些提醒不等于解决所有行为检测。

来源：[README](https://github.com/xpzouying/xiaohongshu-mcp/blob/7797fd375aba02dcd33afe143f6bee13e5870859/README.md)。以上均为项目方声明或经验，不是平台官方许可。

## 主要一手来源表

| 来源 | 创建日期；查证时状态 | 实际可读到的证据 | 能证明什么、不能证明什么 |
| --- | --- | --- | --- |
| [#316](https://github.com/xpzouying/xiaohongshu-mcp/issues/316) | 2025-12-04；closed / completed，2026-07-28 关闭 | 提问者称发了 4 篇，其中 1 篇违规；后续有人称实名申诉后再次使用仍被封。OWNER 从早期提示实名，到 [2026-07-28 明确承认风险](https://github.com/xpzouying/xiaohongshu-mcp/issues/316#issuecomment-5100848949)。 | 不能用实名解释全部投诉；帖中有关 AI 文风的评论也是用户推测，并非已证实根因。 |
| [#668](https://github.com/xpzouying/xiaohongshu-mcp/issues/668) | 2026-04-17；closed / not_planned，2026-08-02 关闭 | 主帖称每两小时检查并自动回复评论，一天后被封；多人跟帖报告警告/封禁。OWNER [8 月 2 日回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/668#issuecomment-5159291358)认为频率偏高，称已有优化、测试账号多日正常。 | 这是自报结果及维护者判断；不能由跟帖人数推算故障率，也不能据此推导一个安全间隔。 |
| [#674](https://github.com/xpzouying/xiaohongshu-mcp/issues/674) | 2026-04-28；closed / completed，2026-07-27 关闭 | 提议者称两个账号被封，评论中有一周封禁自述。OWNER [7 月 27 日回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/674#issuecomment-5092317636)介绍已落地的浏览器环境与交互改进，建议 v2.1.1，并称测试账号若干天正常。 | 是维护者声称做了哪些改进及短期观察；不构成独立效果验证。源码真实性另由主研究核对。 |
| [#680](https://github.com/xpzouying/xiaohongshu-mcp/issues/680) | 2026-05-02；closed / not_planned，2026-08-02 关闭 | 主帖称在云服务器运行，每八小时定时任务；[原图](https://github.com/user-attachments/assets/dca16ea6-d15d-4f8b-a891-c243f9ea3672)显示账号违规预警，含“疑似使用三方工具或脚本（如AI）”。[5 月 6 日跟帖](https://github.com/xpzouying/xiaohongshu-mcp/issues/680#issuecomment-4384990679)称设为仅自己可见也被封。 | 截图重点是操作自动化；仅自己可见不是已证实的豁免机制。云 IP、指纹或内容同质化的具体因果均未被证实。 |
| [#715](https://github.com/xpzouying/xiaohongshu-mcp/issues/715) | 2026-06-15；closed / completed，2026-07-27 关闭 | 主帖无正文。[6 月 15 日跟帖](https://github.com/xpzouying/xiaohongshu-mcp/issues/715#issuecomment-4703931753)称发布没问题，两次让 AI 检查结果后先警告、再限制七天。OWNER 后回复建议更新到 v2.1.1。 | 有自述但操作日志与平台判定详情缺失；不能据此认定一定是发布后检查触发。 |
| [#728](https://github.com/xpzouying/xiaohongshu-mcp/issues/728) | 2026-07-10；closed / completed，2026-07-27 关闭 | 主帖称采集两次后被封；[附图](https://github.com/user-attachments/assets/883693bc-2ff5-4346-806e-77f1096f924d)确有操作自动化的违规提示与预警。OWNER [7 月 27 日回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/728#issuecomment-5092343683)称已优化，建议 v2.1.1。 | 图片中处置时间被遮挡，不能从标题或截图确认永久封号、具体限制时长及触发次数。 |
| [#777](https://github.com/xpzouying/xiaohongshu-mcp/issues/777) | 2026-07-28；closed / completed，2026-08-02 关闭 | 提问者 [7 月 29 日补充](https://github.com/xpzouying/xiaohongshu-mcp/issues/777#issuecomment-5112292493)：7 月 27 日安装，记忆中应为 v2.1.0，家庭网络，取消收藏及搜索后次日预警；[附图](https://github.com/user-attachments/assets/9c0fb425-3899-42dd-8416-0b95baa0afc9)与 #680 模板相近。 | 时间晚于部分改进，但报告版本不确定，不能绑定今日 HEAD。OWNER [8 月 2 日](https://github.com/xpzouying/xiaohongshu-mcp/issues/777#issuecomment-5154910486)也给出反方向观察：自己试发帖、点赞、收藏、搜索，隔日正常。 |
| [#573](https://github.com/xpzouying/xiaohongshu-mcp/issues/573) | 2026-03-11；closed / not_planned，2026-08-02 关闭 | 标题称发布后只有本人可见，主帖无正文。OWNER [8 月 2 日回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/573#issuecomment-5159292027)提出检查 visibility 参数与发布审核等待。 | 可见性异常不等于已证实风控。本例不能当作确定的处罚案例。 |
| [#131](https://github.com/xpzouying/xiaohongshu-mcp/issues/131) | 2025-09-22；closed / completed，2025-09-23 关闭 | OWNER [9 月 22 日回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/131#issuecomment-3316790900)已经警示异常行为可能被监测，反对频繁抓取。 | 项目方从早期就有风险警告；其中法律判断仅是作者说法，本报告不作为法律结论采纳。 |
| [#500](https://github.com/xpzouying/xiaohongshu-mcp/issues/500) | 2026-03-04；closed / completed，2026-07-28 关闭 | 主帖无正文。OWNER [7 月 28 日回复](https://github.com/xpzouying/xiaohongshu-mcp/issues/500#issuecomment-5100848228)再次承认风险存在，并介绍改进和短期测试。 | 是维护者表态，不是新的独立受影响账号样本。 |

`closed` 只表示 GitHub issue 的处理状态。`completed` 是关闭原因字段，不等于经平台确认问题已修复；`not_planned` 更不等于没有风险。大量回复集中在 2026-07-27 至 2026-08-02，阅读时必须同时看投诉日期、实际版本和回复日期。

## 有利于项目及不能当反例的材料

为避免只挑负面证据，本报告保留维护者在 #674、#728 的多日测试正常声明，以及 #777 的隔日正常测试。没有账号级样本数量、完整操作记录和长期随访，无法把这些换算成发生率，但也不能忽略。

检索还命中了 [#780](https://github.com/xpzouying/xiaohongshu-mcp/issues/780)（2026-07-29 创建，closed）：主要是 v2.2.5/v2.2.6 编辑框与上传错误，OWNER [8 月 2 日解释定位与等待问题](https://github.com/xpzouying/xiaohongshu-mcp/issues/780#issuecomment-5158627908)，不是已证实处罚。较新的 open 条目 [#799](https://github.com/xpzouying/xiaohongshu-mcp/issues/799)（2026-08-07）涉及登录设备安全验证，[#813](https://github.com/xpzouying/xiaohongshu-mcp/issues/813)（2026-08-14）及 [#866](https://github.com/xpzouying/xiaohongshu-mcp/issues/866)（2026-10-05）主要是超时，[#829](https://github.com/xpzouying/xiaohongshu-mcp/issues/829)（2026-08-27）是合作讨论；均未作为“最新版封号”证据。

本次关键词检索没有找到能够同时给出今日固定提交、完整复现过程和相同处罚通知的公开报告。这是检索边界，不是不存在风险的证明。

## 官方规则与公告的核实边界

访问了[小红书官方用户服务协议页面](https://agree.xiaohongshu.com/h5/terms/ZXXY20220331001/-1)，公开 HTML 为动态加载页面，网页提取未获得协议正文；通过页面公开加载路径读取正文的尝试也未成功。因此本报告不凭记忆引用其中的自动化、脚本或机器人条款，也不把第三方存档当作本次已读的官方正文。

检索发现 2026-03-10《关于打击 AI 托管运营账号的治理公告》的多份转载。其中[澎湃页面](https://www.thepaper.cn/newsDetail_forward_32741496)明确标注来源为小红书官方账号薯管家，内容涉及 AI 托管代发及互动的梯度处理。但本次没有成功读取小红书原始笔记，故此处只留下追溯线索，**不把这份转载提升为已直接核验的官方原始来源**。本报告关于项目风险的核心结论独立建立在 README、维护者回复及问题区自述上。

公开商家、广告、蒲公英或小程序规则有各自适用范围，不能直接套用为普通创作者的视频发布规则。本次没有找到小红书官方给 `xiaohongshu-mcp` 的授权、安全认证或每天 50 篇的安全阈值。

## 对当前事件的解释边界

用户提供的是关于托管发文/互动和真人操作习惯的通知。原创拍摄、没有 AI 生成内容，与操作流程是否被平台判为自动化是两个可同时存在的事实；不能因前者就排除后者，也不能只因使用自动化就断言知道平台内部算法。

从公共材料可借鉴的是对浏览器环境、会话连续性、异常处理和调用节奏的工程审查，以及把发布完成、审核状态、公开可见和账号处罚分别核对。具体改进是否已实现、是否适合本机 CLI，由主研究的源码和运行记录报告回答。本报告不建议以更换工具、隐私发布、住宅网络或某个间隔作为安全保证，也不把维护者提出的优化当作平台批准。

## 查证方法与局限

- 使用 GitHub 公开 REST Search Issues API，检索 `封号`、`风控`、`违规`、`仅自己`、`限流`、`屏蔽`、`风险`、`AI托管`、`真人操作`、`AI`、`托管`；同时查询 open 和 closed，并检查 2026-07-20 之后的结果。中文分词会带来无关命中，未把命中数量当事件数量。
- 读取表中核心条目的正文、评论、创建/关闭日期、`state_reason` 和 `author_association`；OWNER 标识用于区分仓库所有者与普通评论者。阅读了 #680、#728、#777 的原始图片，没有仅按标题归因。
- 未完整遍历仓库每条 issue 的所有历史版本、删除评论、所有附件或所有讨论区；长串聚合问答也未逐条审计。公开投诉有选择偏差、重复报告和缺失信息，不代表所有用户。
- 账号实名、内容、云端/本地、工具版本、访问频率和平台规则都可能变化。没有比较 social-auto-upload 与该项目的封禁率，也没有推导具体检测算法。
- 报告仅保存必要的来源与概述，不保存 Cookie、凭证、小红书账号 ID 或用户发文数据。直接引文限于短片段。
