# Run from the repository root. Default is command preview, not publication.
param(
    [ValidateSet('capabilities','accounts','list','enqueue','run','status','attempts')][string]$Action = 'capabilities',
    [Parameter(Mandatory=$true)][string]$Db,
    [string]$Job, [string]$Manifest, [string]$MediaRoot,
    [switch]$Headed, [switch]$PrepareOnly, [switch]$Execute
)
$scriptArgs = @($Action, '--db', $Db)
if ($Job) { $scriptArgs += @('--job', $Job) }
if ($Manifest) { $scriptArgs += @('--manifest', $Manifest) }
if ($MediaRoot) { $scriptArgs += @('--media-root', $MediaRoot) }
if ($Headed) { $scriptArgs += '--headed' }
if ($PrepareOnly) { $scriptArgs += '--prepare-only' }
if ($Execute) { $scriptArgs += '--execute' }
& uv run --frozen --offline --no-sync python (Join-Path $PSScriptRoot 'tencent_cli_template.py') @scriptArgs
exit $LASTEXITCODE
