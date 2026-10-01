# 运行环境

优先使用当前仓库的已安装环境。确认根目录有 `pyproject.toml` / `uv.lock`，可执行：

```powershell
uv run --frozen --offline --no-sync sau jobs --json capabilities
uv run --frozen --offline --no-sync python -m patchright --version
```

`--offline --no-sync` 适用于依赖已经安装的环境，只控制 uv 的包处理；运行中的网页和浏览器安装器仍可能联网。缺环境时遵循 [docs/install.md](../../../docs/install.md) 的锁文件安装流程，不随意升级驱动。已安装的全局 sau 或 `.venv/Scripts/sau.exe` 也可用，但应确认来自该项目环境。

缺少浏览器可执行文件时，用同一 Python 环境安装驱动匹配的 Chromium：

```powershell
uv run --frozen --offline --no-sync python -m patchright install chromium
```

不要用系统 PATH 中不明来源的 playwright/patchright，也不要因异常文字写着 Playwright 就换驱动或手工复制其他 revision。下载受阻时先核对项目锁版本、缓存路径与网络，再按用户环境配置可信下载源；不默认改全局镜像。

扫码和首轮实测优先 `--headed`；明确要求后台运行时使用 CLI 的默认无头模式。`jobs run` 没有 `--headless` 参数，登录旧子命令才有该参数。会出现窗口的是 CLI 打开的浏览器，不是输出中的日志或手机推送。

保存 UTF-8 JSON。捕获 CLI 时分别保存 stdout JSON 与 stderr 通知；Python 子进程可设置 `PYTHONUTF8=1` 或使用明确 UTF-8 解码。不要把损坏的中文文本重新提交。
