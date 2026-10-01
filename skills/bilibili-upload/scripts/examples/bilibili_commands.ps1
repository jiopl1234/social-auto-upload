# Single-operation preview. Bilibili login execution needs a local interactive terminal.
param(
    [ValidateSet('login','check','upload-video')][string]$Action = 'check',
    [Parameter(Mandatory=$true)][string]$Account,
    [string]$File, [string[]]$Images, [string]$Title, [string]$Desc,
    [string]$Note, [Nullable[int]]$Tid, [string]$Tags, [string]$Schedule,
    [switch]$Headed, [switch]$Execute
)
$scriptArgs = @($Action, '--account', $Account)
if ($File) { $scriptArgs += @('--file', $File) }
if ($Title) { $scriptArgs += @('--title', $Title) }
if ($PSBoundParameters.ContainsKey('Desc')) { $scriptArgs += @('--desc', $Desc) }
if ($Note) { $scriptArgs += @('--note', $Note) }
if ($null -ne $Tid) { $scriptArgs += @('--tid', [string]$Tid) }
if ($Tags) { $scriptArgs += @('--tags', $Tags) }
if ($Schedule) { $scriptArgs += @('--schedule', $Schedule) }
if ($Images) { $scriptArgs += '--images'; $scriptArgs += $Images }
if ($Headed) { $scriptArgs += '--headed' }
if ($Execute) { $scriptArgs += '--execute' }
& uv run --frozen --offline --no-sync python (Join-Path $PSScriptRoot 'bilibili_cli_template.py') @scriptArgs
exit $LASTEXITCODE
