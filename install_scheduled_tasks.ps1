param(
    [string]$StartAt = "08:00",
    [string]$StopAt = "23:30",
    [string]$StartTaskName = "BilibiliFeishuWatcherStart",
    [string]$StopTaskName = "BilibiliFeishuWatcherStop"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WatcherScript = Join-Path $ScriptDir "bilibili_feishu_watcher.py"
$ConfigFile = Join-Path $ScriptDir "config.json"
$PythonExe = (Get-Command python).Source

if (-not (Test-Path $WatcherScript)) {
    throw "Watcher script not found: $WatcherScript"
}

if (-not (Test-Path $ConfigFile)) {
    throw "Config file not found: $ConfigFile"
}

function Remove-TaskIfExists {
    param([string]$TaskName)

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}

Remove-TaskIfExists -TaskName $StartTaskName
Remove-TaskIfExists -TaskName $StopTaskName

$startArgument = "`"$WatcherScript`" --config `"$ConfigFile`" --log-level INFO"
$startAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $startArgument `
    -WorkingDirectory $ScriptDir
$startTrigger = New-ScheduledTaskTrigger -Daily -At $StartAt
$startSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $StartTaskName `
    -Action $startAction `
    -Trigger $startTrigger `
    -Settings $startSettings `
    -Description "Start Bilibili Feishu watcher every day" `
    -User $env:USERNAME | Out-Null

$stopCommand = @"
Get-CimInstance Win32_Process |
Where-Object { `$_.CommandLine -like '*bilibili_feishu_watcher.py*' } |
ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }
"@
$stopCommandEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($stopCommand))

$stopAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $stopCommandEncoded"
$stopTrigger = New-ScheduledTaskTrigger -Daily -At $StopAt
$stopSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $StopTaskName `
    -Action $stopAction `
    -Trigger $stopTrigger `
    -Settings $stopSettings `
    -Description "Stop Bilibili Feishu watcher every day" `
    -User $env:USERNAME | Out-Null

Write-Host "Scheduled tasks installed:"
Write-Host "  Start: $StartTaskName at $StartAt"
Write-Host "  Stop : $StopTaskName at $StopAt"
Write-Host ""
Write-Host "Test start:"
Write-Host "  Start-ScheduledTask -TaskName `"$StartTaskName`""
Write-Host ""
Write-Host "Test stop:"
Write-Host "  Start-ScheduledTask -TaskName `"$StopTaskName`""
