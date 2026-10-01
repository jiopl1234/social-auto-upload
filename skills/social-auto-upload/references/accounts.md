# 扫码、在线身份与绑定

登录会话和任务绑定是两步。旧账号看起来只需用户扫码，是 agent 随后识别了在线 ID 并代为绑定；当前 login 命令本身没有自动完成绑定。应承担这一步，不把手工查 ID 作为正常用户前置条件。

## 新账号

为不同真实账号使用不同别名，例如 `channels_second`。同平台同别名已经绑定另一个 ID 时会返回 CONFLICT，不能覆盖记录或删除历史任务来换账号。已有账号刷新登录仍使用原别名。

以下 PowerShell 示例从仓库根目录运行；`$taskDb` 必须指向当前使用的固定数据库，平台和别名随目标调整：

```powershell
$taskDb = 'C:/Users/Chinese/.sau/tasks.sqlite3'
$platform = 'tencent'
$account = 'channels_second'
uv run --frozen --offline --no-sync sau $platform login --account $account --headed
uv run --frozen --offline --no-sync sau $platform check --account $account
```

在已有用户扫码授权下，使用只读 helper 取得在线稳定 ID。该脚本复用正式浏览器、平台账号检查、累计预算、账号锁和资源清理，不创建发布任务、不上传、不发表、不绑定，也不保存原始认证响应：

```powershell
$source = "cookies/${platform}_${account}.json"
$probeArgs = @('--db', $taskDb, '--platform', $platform, '--account', $account, '--session-source', $source, '--headed')
$identity = uv run --frozen --offline --no-sync python skills/social-auto-upload/scripts/identify_account.py @probeArgs | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $identity.status -ne 'IDENTIFIED') { throw '在线账号识别未完成；先处理报告的问题' }
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json bind-account --platform $platform --account $account --expected-id $identity.account_id --session-source $source
uv run --frozen --offline --no-sync sau jobs --db $taskDb --json accounts
```

若使用自定义 `--runtime-dir`，probe、bind 和后续任务命令必须使用相同值。默认运行目录由项目决定，不能为绕过锁而随意更换。脚本缺失或与当前代码不兼容时，按项目的只读身份检查流程处理；不要猜 ID 或降级为昵称。

| 平台 | 在线身份来源 |
|---|---|
| douyin | 正式创作者页面观察到的 `user/info` 中 `user.uid` |
| xiaohongshu | 正式创作者页面 `galaxy/user/info` 的稳定 `data.userId` |
| tencent | `auth_data` 中 `data.finderUser.finderUsername`，形如 `v2_…@finder` |

仅返回允许的 ID、平台、别名和时间；不输出 payload 或 Cookie。扫码应由用户在可见页面或实际生成的二维码上完成。只有确实出现本人验证时才请用户介入。

## 已绑定账号与恢复

绑定记录中的 ID 是预期身份，实际发布仍独立检查当前在线账号。新增数据库需要重新登记绑定；换数据库不会转移旧任务的幂等历史。换账号也不能修改已入队任务的不可变清单；新意图另建任务，旧任务仍归原账号。

刷新会话后，只对 `NOT_SUBMITTED` 的受阻任务使用 `resume` 再 run。已经 `MAY_HAVE_SUBMITTED` 的任务保持原 job/attempt，普通 run 进入只读恢复；不能新建交付键重发。
