# Registers the SubtleSpawns tasks in Windows Task Scheduler:
#   - a new Short every day at 3 PM (and 9 AM too with -TwiceDaily)
#   - analytics, first comments and reply drafts every day at 2 PM
#   - the weekly recap every Sunday at 6 PM
# Run once:  powershell -ExecutionPolicy Bypass -File schedule_daily.ps1 [-TwiceDaily]
param([switch]$TwiceDaily)
$dir = $PSScriptRoot
$py = (Get-Command python).Source
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$triggers = @(New-ScheduledTaskTrigger -Daily -At 3pm)
if ($TwiceDaily) { $triggers += New-ScheduledTaskTrigger -Daily -At 9am }
Register-ScheduledTask -TaskName "SubtleSpawns Daily Short" -Trigger $triggers -Settings $settings -Force `
    -Action (New-ScheduledTaskAction -Execute $py -Argument "make_short.py --upload" -WorkingDirectory $dir) | Out-Null

Register-ScheduledTask -TaskName "SubtleSpawns Daily Stats" -Trigger (New-ScheduledTaskTrigger -Daily -At 2pm) -Settings $settings -Force `
    -Action (New-ScheduledTaskAction -Execute $py -Argument "stats.py" -WorkingDirectory $dir) | Out-Null

Register-ScheduledTask -TaskName "SubtleSpawns Weekly Recap" -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 6pm) -Settings $settings -Force `
    -Action (New-ScheduledTaskAction -Execute $py -Argument "recap.py --upload" -WorkingDirectory $dir) | Out-Null

Write-Host "Scheduled: daily Short at 3 PM$(if ($TwiceDaily) {' and 9 AM'}), stats at 2 PM, weekly recap Sundays at 6 PM."
