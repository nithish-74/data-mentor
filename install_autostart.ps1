# Registers a Windows scheduled task that starts the mentor (website, Telegram, public link) hidden every time you log in.
# Run once:   powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# Remove:     Unregister-ScheduledTask -TaskName "DataModelMentor" -Confirm:$false
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $env:USERPROFILE ".data-mentor\venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { Write-Error "Run start_mentor.bat once first (it creates the Python environment)."; exit 1 }

$action   = New-ScheduledTaskAction -Execute $pyw -Argument "app.py" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "DataModelMentor" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Data Architect Mentor (website + Telegram)" -Force | Out-Null
Start-ScheduledTask -TaskName "DataModelMentor"
Write-Host "Installed. The mentor now starts hidden every time you log in, and is starting now. The public link appears in data\public_url.txt within a minute."
