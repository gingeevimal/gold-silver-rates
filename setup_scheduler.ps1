# Creates (or updates) a Windows scheduled task that runs the agent
# daily at 11:30 AM and 3:00 PM (laptop's local time = IST).
# If the laptop was off/asleep at that time, it runs as soon as it is back on.

$TaskName = "Gold Silver Rates Agent"
$dir = $PSScriptRoot
$bat = Join-Path $dir "run_agent.bat"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $dir

$morning   = New-ScheduledTaskTrigger -Daily -At "11:30AM"
$afternoon = New-ScheduledTaskTrigger -Daily -At "3:00PM"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger $morning, $afternoon -Settings $settings `
    -Description "Fetches gold & silver rates into gold_rates.xlsx / silver_rates.xlsx" `
    -Force | Out-Null

Write-Host ""
Write-Host "Done! Scheduled task '$TaskName' created." -ForegroundColor Green
Write-Host "Runs daily at 11:30 AM and 3:00 PM."
Write-Host "If the laptop is off at that time, it runs as soon as you switch it on."
Write-Host ""
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Select-Object NextRunTime | Format-List
