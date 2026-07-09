param(
  [string]$Time = "",
  [string]$TaskName = "YouTube Daily Watchlist"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ProjectDir "youtube_channels.json"

if (-not $Time -and (Test-Path $ConfigPath)) {
  $config = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $Time = [string]$config.schedule_time
}
if (-not $Time) {
  $Time = "08:30"
}

$Python = (Get-Command python -ErrorAction Stop).Source
$Script = Join-Path $ProjectDir "youtube_watchlist.py"
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -Compatibility Win8

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "每天檢查指定 YouTuber 是否有新上傳影片" -Force | Out-Null

Write-Host "已建立每日排程：$TaskName"
Write-Host "執行時間：$Time"
Write-Host "專案位置：$ProjectDir"
Write-Host "可用工作排程器或 PowerShell 查看：Get-ScheduledTask -TaskName '$TaskName'"
