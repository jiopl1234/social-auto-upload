# 五路径联合验收

日期：2026-09-26；固定基线 `88583d8cd778a148208abca96edd9ae56b3f8875`。01–14 已完成后开始本票。

## 当前能力门槛

| 路径 | 本地 DOM 与任务协议 | 真实稳定账号 ID | 真实处理完成 / 作品读回 | 生产执行 |
|---|---|---|---|---|
| 抖音视频 | 已验证 | 未验证 | 未验证 | restricted |
| 抖音图文 | 已验证 | 未验证 | 未验证 | restricted |
| 小红书视频 | 已验证 | 未验证 | 未验证 | restricted |
| 小红书图文 | 已验证 | 未验证 | 未验证 | restricted |
| 视频号视频 | 已验证 | 未验证 | 未验证 | restricted |

`jobs capabilities` 逐路径提供 offline_verified、offline_evidence、production_verified、missing_evidence。编辑器候选来自先前审计的旧 uploader；见 `orchestrator/platforms/profiles.py` 及 07/09/11、08/10 验证记录。真实页面稳定账号 ID、逐素材状态和作品字段没有有效会话与明确探测授权，保持缺失，不从昵称、跳转或合成属性推断。没有开启真实发布的命令行开关。

## 可复现入口与证据来源

正式开发依赖写入 pyproject.toml 的 dev 组和 uv.lock。初始化配置、`uv sync --frozen --group dev` 后执行 `uv run --frozen --offline --no-sync python scripts/check_offline.py --report-dir <目录>`。入口先确认本机浏览器，再运行全部 tests 与核心类型检查；默认 pytest 仅收集 tests，排除 examples 中的真实平台示例。详情见 docs/install.md。

本地页面是本仓库编写的 `tests/fixtures/creator_contract.html`，HTTPServer 只绑定 127.0.0.1；浏览器读取实际选中的测试文件，按页面实际 POST 内容生成独立作品记录。输入素材为微型 PNG 与用于文件类型测试的 MP4 头，不能证明真实平台转码或审核。所有会话为合成空 Cookie/来源；无真实账号发布或后台访问。

崩溃验收使用真实 SQLite、被强制终止的 Python worker 和独立 JSONL 作品存储，模拟远端已经接收。它验证任务持久性与只读恢复，不宣称已杀死真实平台浏览器并自动解除安全阻断。另有浏览器实际资源清理与未确认资源禁止接管测试。

## 五条路径的场景覆盖

| 场景 | 对应公共验收 |
|---|---|
| 正常 / 元数据与素材失败 / 挑战停止 | tests/test_platform_tasks.py 的三视频路径；tests/test_note_tasks.py 的两图文路径 |
| 响应延迟且远端已经接收 | tests/test_release_acceptance.py，五路径逐一暂停回执后释放，始终一次最终动作 |
| 提交后取消与只读恢复 | release_acceptance 的三视频路径；note_tasks 的两图文路径 |
| 准备时强杀、提交边界后强杀、远端写入后回执前强杀 | tests/test_delivery_processes.py，三种故障均覆盖五路径 |
| 定时分发、部分成功、认证暂停、提交不明、重启核对与重复入队 | release_acceptance 的三平台组合：最终提交数为 1 / 0 / 1 |
| 资源、诊断、权限、会话保存、锁冲突、时间与批次 | 全部先前票的公共接口及必要本地资源故障测试随统一入口运行 |

能力 JSON 的离线/生产区分先因缺失字段失败，实现后通过。首轮新增联合验收 26 项通过、1 项 Windows 握手文件替换瞬间 PermissionError；有界握手读取现同时等待暂时缺失与临时共享拒绝，保留原 10 秒期限，持久权限错误仍失败。没有改变交付判定。

## 最终验证结果

固定实现提交 `bb09166fd5b80b543468d300d9fb8580f9aa38a4` 通过 git archive 导出到新的临时目录；使用独立虚拟环境，从 conf.example.py 创建配置，没有复制用户配置或会话。`uv sync --frozen --offline --group dev` 安装 27 个锁定依赖，随后统一入口退出码为 0。

- 环境：Windows、Python 3.12.10、Patchright 1.58.2、Chrome 152.0.7977.83、pytest 9.1.1、pytest-timeout 2.4.0、Pyright 1.1.414。
- 完整测试：342 passed、20 subtests passed，301.08 秒；0 failures、0 errors、0 skipped。JUnit 将子测试一起计数为 362，不是额外 362 项主测试。
- 核心与 CLI 类型检查：0 errors、0 warnings、0 informations。
- 原始证据：[环境](ticket-15-clean/environment.txt)、[测试输出](ticket-15-clean/offline-pytest.txt)、[JUnit](ticket-15-clean/offline-pytest.xml)、[类型检查](ticket-15-clean/offline-pyright.txt)。

独立审查后，`ddb03e7f7bd8700febefd266210f0c3b5da82c37` 仅将重复的五路径预期提取为测试支持常量，保持独立于生产能力列表。相关发布整合与进程恢复 27 项再次通过，58.53 秒；核心、CLI、验收脚本和三个测试支持文件的类型检查为 0 errors。该差异不修改生产代码或测试场景，未重复运行完整套件；[修正后 JUnit](ticket-15-clean/review-fix-pytest.xml) 保留复验结果。

代码图按 Tier 2 核验，项目 `C-Users-Chinese-social-auto-upload`，generation `2026-09-26T17:25:31Z`。新增整合测试检索 3/3、能力查询检索 1/1；分发整合测试双向一层调用追踪无截断。coverage 对代码报告 metadata_changed，对 scripts、tests/fixtures 和文档报告 excluded，已直接读取当前源文件兜底；这不是图完整性的证明。

Standards 0 项硬违反、1 项维护性建议已修复并复核关闭；Spec 0 项发现。详情见 [双轴审查](ticket-15-review.md)。01–15 的离线实施与验收已经完成。

已知限制持续保留：没有零风控保证，没有远端 exactly-once 证明，挑战后可以持续暂停；离线通过不等于可用于真实无人值守生产。五条路径的真实平台账号身份、逐素材处理与作品读回仍未验证，生产能力保持 restricted；没有执行真实只读探测或发布，也没有勾选生产验证完成。
