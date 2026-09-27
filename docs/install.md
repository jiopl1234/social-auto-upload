# 安装说明

这个文档分成两部分：

- `For Humans`：给正常使用仓库的开发者、创作者、CLI 用户看
- `For AI Agents`：给 OpenClaw、Codex、Claude Code 一类 agent 看

如果你是“正在使用 agent 客户端的人”，想先给 agent 一段启动提示词，而不是直接阅读下面的执行细节，先看：

- [Agent Bootstrap Prompt](./agent-bootstrap.md)

## For Humans

本分支保留抖音、小红书的 `upload-video` / `upload-note` 和视频号的 `upload-video` 命令参数兼容，但有效参数调用会返回 `UNSUPPORTED` 迁移错误，不会执行旧发布流程。实际发布入口是 [持久发布任务](tasks.md)（`sau jobs`）；目前缺少真实平台的身份与工作流验证证据，生产发布能力仍受限制。安装与离线验收通过不代表可以真实发布。独立的手动登录、账号检查命令仍可使用。

### 1. 克隆项目

```bash
git clone https://github.com/dreammis/social-auto-upload.git
cd social-auto-upload
```

### 2. 创建虚拟环境

推荐使用 `uv`：

Windows PowerShell：

```powershell
uv venv
.venv\Scripts\activate
```

Linux / macOS：

```bash
uv venv
source .venv/bin/activate
```

### 3. 初始化本地配置

首次安装前，从示例创建本地配置；已有 `conf.py` 时保留自己的配置。

Windows PowerShell：

```powershell
if (-not (Test-Path conf.py)) { Copy-Item conf.example.py conf.py }
```

Linux / macOS：

```bash
test -f conf.py || cp conf.example.py conf.py
```

配置先于 editable 安装创建，确保在仓库外使用 `sau` 时也能解析本地配置。常用配置项为 `LOCAL_CHROME_PATH`、`LOCAL_CHROME_HEADLESS` 和 `DEBUG_MODE`；`XHS_SERVER` 只用于旧小红书流程。

### 4. 安装主线依赖

当前主线依赖已经放到 `pyproject.toml`，推荐直接执行：

```bash
uv pip install -e .
```

安装完成后，会注册 `sau` 命令。

抖音、小红书浏览器版、视频号、快手和 YouTube 使用主线 Patchright。CLI 按实际命令加载对应上传器；帮助和不存在账号的检查不启动浏览器。

百家号、支付宝、微博、虎扑等仍依赖 Playwright 的入口，额外安装兼容依赖：

```bash
uv pip install -e ".[legacy]"
```

`legacy` 采用历史兼容路径的 Playwright 版本，不改变主线 Patchright 依赖。实际使用这些入口时还需按其平台说明准备对应浏览器。

### 5. 安装 patchright Chromium

当前主线使用 `patchright` 驱动浏览器。安装好上一步的项目依赖后，在仓库目录执行（Windows PowerShell、Linux / macOS 相同）：

```bash
uv run --frozen --offline --no-sync python -m patchright install chromium
```

该命令使用项目虚拟环境，无需把 `playwright` 或 `patchright` 添加到系统 PATH。这里的 `--offline --no-sync` 只约束 uv 的 Python 依赖处理；浏览器安装器仍需联网下载。

若提示 `Executable doesn't exist`，但安装命令立即结束，缓存可能保留了安装完成标记而丢失可执行文件。强制重新安装：

```bash
uv run --frozen --offline --no-sync python -m patchright install --force chromium
```

抖音 `login` 和已有会话的 `check` 会在启动账号浏览器前检查 bundled Chromium；文件缺失时给出上述修复命令，修复后可直接重试。不存在账号的 `check` 仍直接返回 `invalid`。这两个维护入口使用 `channel="chromium"`，不会因设置 `LOCAL_CHROME_PATH` 而改用系统 Chrome。

如果旧版本已留下 `The previous browser shutdown has not been confirmed`，重新安装浏览器不会自动清除该保护。需先核实原执行器和关联浏览器确已退出，再由维护流程持锁恢复对应的所有权记录；不要删除整个运行目录或锁文件。

### 6. 验证 CLI 是否可用

```bash
sau --help
sau douyin --help
sau kuaishou --help
sau xiaohongshu --help
sau tencent --help
sau bilibili --help
```

如果命令找不到，优先确认：

- 当前虚拟环境是否已激活
- 是否执行过 `uv pip install -e .`

### 6A. 运行离线验收

首次运行时仅在缺少 `conf.py` 时从示例创建；已有本地配置不会被覆盖。先安装锁定的开发依赖，再用同一个入口运行 `tests/` 全部离线用例和 `orchestrator/`、`sau_cli.py` 类型检查：

Windows PowerShell：

```powershell
if (-not (Test-Path conf.py)) { Copy-Item conf.example.py conf.py }
uv sync --frozen --group dev
uv run --frozen --offline --no-sync python scripts/check_offline.py --report-dir .scratch/offline-acceptance
```

Linux / macOS：

```bash
test -f conf.py || cp conf.example.py conf.py
uv sync --frozen --group dev
uv run --frozen --offline --no-sync python scripts/check_offline.py --report-dir .scratch/offline-acceptance
```

脚本只使用本地测试夹具和临时运行目录，不读取真实 Cookie、不访问平台后台，也不执行真实发布。它会先按测试使用的规则检查本机 Chrome、Chromium 或 Edge 可执行文件；缺少浏览器时直接失败，不把浏览器测试的跳过记作通过。Windows 检查常规安装路径，Linux/macOS 检查 `PATH`；单独安装 Patchright 浏览器包不一定满足这项检查。`--report-dir` 可省略；指定时固定写入 `offline-pytest.txt`、`offline-pytest.xml` 和 `offline-pyright.txt`。只检查环境或测试收集可分别加 `--check-browser`、`--collect-only`。默认 pytest 只收集 `tests/`，不会运行 `examples/test_alipay_upload.py` 这个真实平台示例。

### 7. 抖音旧命令参数示例

以下 `login`、`check` 用于账号维护；`upload-video`、`upload-note` 仅展示兼容参数，调用会返回迁移错误。发布任务请见 [任务命令](tasks.md)。

```bash
sau douyin login --account <account_name>
sau douyin check --account <account_name>
sau douyin upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介"
图文正文1:
$noteText = @"图文正文"@    
sau douyin upload-note --account <account_name> --images videos/demo1.png videos/demo2.png --title "图文标题" --note $noteText --tags 'tag1,tag2'
图文正文2:
sau douyin upload-note --account <account_name> --images videos/demo1.png videos/demo2.png --title "图文标题" --notef '图文文件路径' --tags 'tag1,tag2'
添加 BGM（可选）:
sau douyin upload-note --account <account_name> --images videos/demo1.png videos/demo2.png --title "图文标题" --note $noteText --tags 'tag1,tag2' --bgm '音乐名称'
``` 

任务发布遇到短信或其他身份挑战会暂停，需人工处理账号状态后再按 [任务命令](tasks.md) 查看和处理任务；不会读取 `verify_code.txt` 或在旧上传命令中继续提交。

抖音卡login手动获取cookie:

- 目标服务器使用vnc
- 浏览器登录抖音创作者中心https://creator.douyin.com/
- 执行`bash export_douyin_cookie.sh --account <account_name>`
- 检查cookie可用性,执行`sau douyin check --account <account_name>`

### 8. 快手主线示例

```bash
sau kuaishou login --account <account_name>
sau kuaishou check --account <account_name>
sau kuaishou upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介"
sau kuaishou upload-note --account <account_name> --images videos/demo1.png videos/demo2.png videos/demo.png --title "图文标题" --note "图文正文"
```

### 9. 小红书旧命令参数示例

以下 `login`、`check` 用于账号维护；`upload-video`、`upload-note` 仅展示兼容参数，调用会返回迁移错误。发布任务请见 [任务命令](tasks.md)。视频号 `upload-video` 也遵循同一迁移约定。

```bash
sau xiaohongshu login --account <account_name>
sau xiaohongshu check --account <account_name>
sau xiaohongshu upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介"
sau xiaohongshu upload-note --account <account_name> --images videos/demo1.png videos/demo2.png videos/demo.png --title "图文标题" --note "图文正文"
```

### 10. Bilibili 主线示例

```bash
sau bilibili login --account <account_name>
sau bilibili check --account <account_name>
sau bilibili upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介" --tid 249
```

补充说明：

- `creator` 之类的名字只是示例值，真正传的是用户自定义的 `account_name`
- 一个 `account_name` 对应一个账号文件，可以准备多个账号并发使用
- 浏览器平台统一元数据约定：
- 视频使用 `title + desc + tags`
- 图文使用 `title + note + tags`
- 用户不需要手动安装 `biliup`
- 首次运行 Bilibili 相关命令时，程序会自动下载 `biliup`
- 后续运行会自动检查上游 release 并自动更新
- Bilibili 登录建议由用户自己在本地真实终端里执行；如果终端里的二维码显示不完整，可直接打开当前目录下的 `qrcode.png` 扫码
- 如果国内网络访问 GitHub Release 较慢，可先用 `https://gh-proxy.com/` 或 `https://gh-proxy.org/` 辅助访问对应 release 地址排障
- 示例：
  - `https://gh-proxy.org/https://github.com/biliup/biliup/releases/download/v1.1.29/biliupR-v1.1.29-aarch64-linux.tar.xz`

## For AI Agents

如果你是一个可执行命令的 agent，请优先按下面顺序处理：

1. 先假设仓库根目录就是当前工作目录
2. 优先使用 `uv` 管理环境，不要默认回退到旧的 `requirements.txt`
3. 安装命令优先使用：

```bash
uv pip install -e .
```

4. 如需浏览器驱动，在项目虚拟环境中安装（不依赖系统 PATH）：

```bash
uv run --frozen --offline --no-sync python -m patchright install chromium
```

若可执行文件缺失但普通安装立即结束，按上文「安装 patchright Chromium」使用 `--force` 重新安装；uv 的 `--offline` 不会禁止浏览器安装器联网下载。

5. 安装完成后，优先检查：

```bash
sau --help
sau douyin --help
sau kuaishou --help
sau xiaohongshu --help
sau bilibili --help
```

6. 抖音、小红书和视频号的手动登录、账号检查仍可使用 CLI；这三个平台的发布应使用 [持久发布任务](tasks.md)（`sau jobs`），并遵守上述真实平台验证限制。快手、Bilibili 的命令按各自说明使用：

```bash
sau douyin login
sau douyin check

sau xiaohongshu login
sau xiaohongshu check

sau tencent login
sau tencent check

sau kuaishou login
sau kuaishou check
sau kuaishou upload-video
sau kuaishou upload-note

sau bilibili login
sau bilibili check
sau bilibili upload-video
```

7. 如果用户明确在使用 skill 系统，再引导其阅读：

- `skills/douyin-upload/SKILL.md`
- `skills/douyin-upload/references/cli-contract.md`
- `skills/kuaishou-upload/SKILL.md`
- `skills/kuaishou-upload/references/cli-contract.md`
- `skills/xiaohongshu-upload/SKILL.md`
- `skills/xiaohongshu-upload/references/cli-contract.md`
- `skills/bilibili-upload/SKILL.md`
- `skills/bilibili-upload/references/cli-contract.md`

### 对 agent 的额外说明

- 当登录流程生成本地二维码图片时，不要只把图片路径发给用户
- 这类二维码图片本身就是给用户扫码的，agent 应优先直接展示/发送本地图片给用户扫码
- 如果环境支持查看本地图片，优先用查看图片能力把二维码展示出来；路径只作为补充信息
- Bilibili 登录当前不建议 agent 在非交互环境里直接代跑
- 正确做法是让用户自己在本地终端执行 `sau bilibili login --account <name>`；如果二维码显示不完整，再提示用户打开 `qrcode.png`
- `requirements.txt` 目前是历史兼容文件，不是主安装入口
- `uploader/` 是核心实现目录
- `sau_cli.py` 是当前 CLI 主入口
- `docs/legacy-web.md` 是历史 Web 版本说明，不保证当前可用
- Bilibili 首次运行时可能自动下载 `biliup`
