# TradingAgents — Windows Scheduled Task installer
#
# Sets up a Windows Scheduled Task that runs the `claude` CLI in headless
# mode every 30 minutes to drain the run-queue. The CLI uses your Claude
# Max subscription (NOT API tokens), so this is free as long as you're
# logged in.
#
# Requires:
#   - Claude Code installed (`claude --version` should print something)
#   - You're logged into Claude Code under your Max account
#   - Network access to the NAS api at http://192.168.2.34:8001
#
# Usage (run as administrator, from PowerShell):
#   .\scripts\install-windows-drain-task.ps1
#
# Uninstall:
#   Unregister-ScheduledTask -TaskName "TradingAgents Drain Queue" -Confirm:$false

$ErrorActionPreference = "Stop"

$TaskName = "TradingAgents Drain Queue"
$ApiUrl   = "http://192.168.2.34:8001"

# The prompt invokes the tradingagents-analyze skill via its "process the
# queue" trigger. The skill walks /run-queue/pending and routes each item
# by mode. See ~/.claude/skills/tradingagents-analyze/SKILL.md for the
# full dispatch table.
$Prompt = "Process the queue at $ApiUrl/run-queue/pending. Drain everything."

# Find the claude CLI. Tries the default install path, then PATH.
$ClaudeExe = $null
$ClaudeCandidates = @(
    "$env:LOCALAPPDATA\Programs\claude\claude.exe",
    "$env:APPDATA\npm\claude.cmd",
    "$env:LOCALAPPDATA\Anthropic\Claude\claude.exe"
)
foreach ($c in $ClaudeCandidates) {
    if (Test-Path $c) { $ClaudeExe = $c; break }
}
if (-not $ClaudeExe) {
    $found = Get-Command claude -ErrorAction SilentlyContinue
    if ($found) { $ClaudeExe = $found.Source }
}
if (-not $ClaudeExe) {
    Write-Host "ERROR: Could not find claude CLI. Install Claude Code first." -ForegroundColor Red
    Write-Host "Then re-run this script."
    exit 1
}
Write-Host "Found claude CLI at: $ClaudeExe" -ForegroundColor Green

# Build the scheduled task
$Action  = New-ScheduledTaskAction `
    -Execute $ClaudeExe `
    -Argument "-p `"$Prompt`""

# Repeat every 30 min, indefinitely, starting at the next half-hour
$Now = Get-Date
$StartAt = $Now.AddMinutes(2)
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $StartAt `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
# 2-hour limit (was 25-min): a single drain may need to process 6+ heavy
# `analyze` items at 3-5 min each. 25 min was too tight and risked the
# task being killed mid-drain. MultipleInstances=IgnoreNew prevents
# overlapping runs, so a long drain just skips the next 30-min tick.

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# Register (replace if exists)
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Drains the TradingAgents queue via 'claude -p' every 30 min. Uses your Max subscription, not API tokens."

Write-Host ""
Write-Host "Installed scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "First run: $StartAt" -ForegroundColor Green
Write-Host "Interval:  every 30 minutes"
Write-Host ""
Write-Host "To verify it works right now, run:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host ""
Write-Host "To watch it run:"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host ""
Write-Host "To uninstall:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
