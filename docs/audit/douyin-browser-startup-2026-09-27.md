# 抖音维护命令的 Chromium 缺失与误锁修复

基线 `b101ac2`，实现提交 `10b77f6bd2d96b24895a2e2d77365cff23343392`。

## 问题和修复

用户运行 `sau douyin login --account douyin_main --headed`，首次报 `chromium-1208/chrome-win64/chrome.exe` 不存在，普通安装立即结束，再次登录报上次浏览器关闭未确认。本地检查发现该缓存仍有 `INSTALLATION_COMPLETE`，但可执行文件缺失；未推断文件丢失的原因。`run_with_lease` 在浏览器启动前写入资源标记，首次异常因此留下了保护。

在独立运行目录和不存在的 `PLAYWRIGHT_BROWSERS_PATH` 中，连续两次真实 CLI 子进程稳定复现第二次误锁。永久回归在修复前失败于 `previous browser shutdown` 断言。修复后，抖音登录及已有会话检查在持有账号锁时先验证 Patchright 公共 `chromium.executable_path`；文件缺失时给出强制安装命令，尚未标记账号浏览器启动。不存在账号的检查仍不加载驱动。其余异常、残留浏览器与关闭未确认的保护不变。

三个新增回归分别覆盖首次登录、已有会话登录和已有会话检查，每种情形连续运行两次，验证失败结果可重试、安装提示可操作、已有会话未被改写。既有伪登录用例在第三方驱动边界声明可用的可执行文件，避免测试依赖本机 Chromium 安装。没有新增通用强制解锁接口。

## 本机维护

已运行 `uv run --frozen --offline --no-sync python -m patchright install --force chromium`，重新下载固定版本的 Chromium 及配套浏览器资源。随后以 `channel="chromium"` 启动浏览器、加载内存中的本地页面并关闭，版本为 `145.0.7632.6`，关闭后连接已断开。没有登录或访问真实平台。

对本次故障的 `douyin_main` 标记，先核实原 owner 的 PID 和创建身份已终止、关联浏览器及驱动无残留；持有该平台的 OS 锁后重新核对完整旧 token/平台/账号/PID/身份和空子进程列表，备份原记录，再原子地写入 `active=false`、`resources_started=false`。没有删除锁文件、运行目录或其他平台记录，也没有终止其他浏览器。备份位于本机 `C:/Users/Chinese/.sau/runtime/recovery/douyin-missing-chromium-a320c1a9ddc54272804f2dde69827a8e.json`。后续已验证同账号所有权可正常取得和释放。

## 验证记录

新增三项回归通过；相关维护、惰性启动测试在首个回归加入后为 28 passed、20 subtests passed。核心、CLI 与修改测试的 Pyright 为 0 errors、0 warnings、0 informations。

本机现有虚拟环境、锁定依赖和隔离运行目录的完整验收结果：**371 passed、20 subtests passed、1 failed、3 warnings，448.18 秒**；核心与 CLI 类型检查仍为零错误。不是全量全绿。唯一失败为既有 `test_delayed_response_and_cancel_preserve_one_delivery[douyin-video-False]` 的 10 秒提交信号等待超时。三条警告为 Windows asyncio transport 清理诊断。

保留的测试数据库显示从 PREPARING 到 SUBMITTING 约 9.92 秒，随后测试超时取消，记录为 UNKNOWN_COMMIT。该测试经本地夹具直接构建 BrowserSession、使用系统 Chrome，没有调用本次新增的抖音维护预检。未改代码或放宽测试截止时间，单项原样复查为 **1 passed（3.88 秒）**；再运行同一验收文件的全部场景为 **10 passed（50.77 秒）**。表现符合等待边界的时间波动，但没有以重跑结果覆盖最初失败，也没有声称已确定底层性能原因。

原始记录：[完整测试输出](douyin-browser-startup-2026-09-27/offline-pytest.txt)、[JUnit](douyin-browser-startup-2026-09-27/offline-pytest.xml)、[类型检查](douyin-browser-startup-2026-09-27/offline-pyright.txt)、[单项复查](douyin-browser-startup-2026-09-27/targeted-recheck.txt)、[同组复查](douyin-browser-startup-2026-09-27/release-recheck.txt)。

完整验收命令：

```powershell
uv run --frozen --offline --no-sync python scripts/check_offline.py --report-dir .scratch/douyin-browser-startup-2026-09-27
```

## Standards

独立只读审查固定差异 `b101ac2...10b77f6`，未发现硬性规范违反或需要整改的代码异味。CLI 两处均在持锁后、资源标记前预检；缺会话检查仍先返回；新增真实子进程回归覆盖连续重试。审查者未重复运行完整测试。

## Spec

独立只读审查没有发现代码层遗漏、错误实现或范围膨胀。初审有 1 项 P3 文档发现：安装说明的 For AI Agents 段仍使用依赖 PATH 的旧命令，未引导修复损坏缓存。文档补修提交 `09416a1` 已同步该段和 CLI 说明，使用项目环境的模块命令，并明确 `--force` 与 uv `--offline` 的含义；审查者复核后确认关闭。最终未解决发现：Standards 0，Spec 0。

## 代码证据范围

按 codebase-memory 技能尝试 index_status、search_graph、check_index_coverage，均返回 `Transport closed`，没有当前图代次或覆盖结果；仓库不存在 `.codegraph/`。证据回退至固定差异以及 CLI、legacy_resources、ownership、locking、抖音维护流程和相关测试源码。维护命令修复不代表真实平台发布验收，生产发布能力保持受限。
