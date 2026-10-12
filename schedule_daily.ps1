# Registers a Windows task that makes and uploads one Short every day at 3 PM.
# Run once:  powershell -ExecutionPolicy Bypass -File schedule_daily.ps1
$dir = $PSScriptRoot
$py = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $py -Argument "make_short.py --upload" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 3pm
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "SubtleSpawns Daily Short" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Scheduled. Change the time in Task Scheduler if you like."
