# 运行前提

这个 skill 默认假设当前环境已经具备：

- 已安装 `social-auto-upload`
- 可以调用 `sau` 命令，或至少有等效调用方式
- 已为 `patchright` 安装 Chromium

## 推荐安装方式

在项目根目录执行：

```bash
uv pip install -e .
```

## 安装 patchright 浏览器

Windows PowerShell：

```powershell
uv run --frozen --offline --no-sync python -m patchright install chromium
```

Linux / macOS（bash / zsh）：

```bash
uv run --frozen --offline --no-sync python -m patchright install chromium
```

## 常见调用方式

### 如果 `sau` 已经在 PATH 中

```bash
sau kuaishou --help
```

### 如果虚拟环境存在，但还没有激活

PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
sau kuaishou --help
```

### 如果你想直接调用可执行文件

PowerShell：

```powershell
.\.venv\Scripts\sau.exe kuaishou --help
```

### 如果你更倾向于使用 uv

```bash
uv run sau kuaishou --help
```

## 无头和有头模式

- 使用 `--headless` 表示无头模式
- 使用 `--headed` 表示有头模式
- 快手 CLI 默认按无头模式运行
- 如果用户明确要求可见浏览器窗口，或二维码展示确实有问题，再切到 `--headed`
- 如果登录过程中已经生成了本地二维码图片，agent 应优先直接把图片展示/发送给用户扫码，不要只告诉用户图片路径

优先使用当前项目环境的 `uv run --frozen --offline --no-sync sau`；这要求依赖已经安装，不阻止浏览器/上传器联网。本平台没有新版任务的防重发与作品确认保证。
