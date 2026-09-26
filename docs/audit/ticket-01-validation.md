# 01 号票验证记录

日期：2026-09-26。范围：干净安装、CLI 启动、旧参数兼容、现有测试基线。

## 实现

- CLI 在执行对应命令时才加载上传器，帮助和不存在账号的检查不依赖浏览器上传模块。所有 10 个 CLI 平台的这两类命令均有子进程验证。
- 文档要求在 editable 安装前初始化 `conf.py`，已有配置不覆盖。修正不存在的 `cli_main` 打包项；Playwright 兼容入口通过 `legacy` extra 安装。
- 恢复 `DouYinVideo` 原位置参数顺序，`collection_name` 只能通过关键字传入。
- 更新过时的 CLI Namespace 和异步替身；它们暴露的抖音异常路径资源泄漏通过 `finally` 修复。关闭资源失败时继续清理其他资源，并保留原业务异常。
- 小红书话题测试通过本地 HTML 检查正文保留和话题点击结果：前置相似但错误的候选，把正确目标放在第二项。上传器按候选文字精确匹配，替代无条件点击首项。视频号二维码测试等待实际图片生成再模拟关闭弹窗，不再以固定 150 ms 猜测截图完成时间。

## 干净环境安装

新建临时源码副本和独立虚拟环境，不复制现有 `.venv`、账号文件或 `conf.py`。从 `conf.example.py` 初始化配置后按文档 editable 安装；随后从源码目录以外调用安装生成的 `sau.exe`。

实际执行的关键命令（`<...>` 表示每次运行生成的临时路径）：

```text
uv venv <temp-venv>
uv pip install --python <temp-venv>/Scripts/python.exe --editable <temp-checkout>
<temp-venv>/Scripts/sau.exe <platform> --help
<temp-venv>/Scripts/sau.exe <platform> check --account missing
<temp-venv>/Scripts/sau.exe <platform> upload-video --account missing --file <temp-video.mp4> --title test --desc test
```

`<platform>` 分别为 `douyin`、`xiaohongshu`、`tencent`。共 9 项检查通过：帮助退出码为 0；不存在账号的检查退出码为 1、输出 `invalid`；上传退出码为 1，并提示 cookie 缺失或过期。没有进入交互式登录。该独立环境确认未安装 `playwright`，只使用声明的主线依赖。

原始结果：[ticket-01-clean-install.json](ticket-01-clean-install.json)。

## 测试及静态检查

```text
uv run --frozen --with pytest --with pytest-timeout python -m pytest tests -q --timeout=30 --tb=short --junitxml=docs/audit/ticket-01-pytest.xml
uv run --frozen --with pyright pyright sau_cli.py tests/test_cli_startup.py tests/test_tencent_verification_dialog.py
git diff --check
```

- 完整测试：**50 passed, 20 subtests passed**，10.15 秒。原有测试未删除；新增 3 个测试。
- 定向 Pyright：**0 errors, 0 warnings**。这是上述三个文件的检查，不是整个历史仓库的类型检查。
- `git diff --check`：通过。
- 完整测试首次运行出现视频号测试截图/弹窗关闭竞态（49 通过、1 失败）；修正测试时序后，单文件及全套均通过。
- Spec 审查发现只有一个话题候选的测试不能验证准确选择。补上前置干扰候选后，原实现出现 1 失败、9 通过；修复精确匹配后，该文件 10 通过，并重新通过全套测试。
- 视频号夹具使用事件循环时钟限制等待，兼容项目声明的 Python 3.10；本次实际运行环境为 Python 3.12。

原始结果：[文本](ticket-01-pytest.txt)、[JUnit XML](ticket-01-pytest.xml)。测试使用本机已安装的 Chrome 加载本地 HTML，没有访问平台发布页面。

## 边界

本记录仅验收 01 号票。持久任务、幂等提交、崩溃恢复、账号串行、内容事实校验和各平台发布流程仍由 02–15 号票负责。这里的安装及离线测试不等于真实账号发布验证，也不代表已适合无人值守生产运行。
