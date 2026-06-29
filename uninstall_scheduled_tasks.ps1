param(
    [string]$StartTaskName = "BilibiliFeishuWatcherStart",
    [string]$StopTaskName = "BilibiliFeishuWatcherStop"
)

$ErrorActionPreference = "Stop"

foreach ($taskName in @($StartTaskName, $StopTaskName)) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed scheduled task: $taskName"
    } else {
        Write-Host "Scheduled task not found: $taskName"
    }
}
