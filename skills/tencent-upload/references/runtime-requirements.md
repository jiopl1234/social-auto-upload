# 视频号运行前提

读取[共用运行环境](../../social-auto-upload/references/runtime.md)。使用同一锁定项目环境执行 CLI 和浏览器安装器；已安装依赖时优先 `uv run --frozen --offline --no-sync`。

```powershell
uv run --frozen --offline --no-sync sau tencent --help
uv run --frozen --offline --no-sync sau jobs --json capabilities
uv run --frozen --offline --no-sync python -m patchright install chromium
```

最后一条只在浏览器确实缺失时使用，下载会联网。不要要求系统 PATH 中另装 playwright，也不默认修改镜像。登录/首轮实测按需要使用 headed；jobs run 默认无头，没有 --headless 参数。
