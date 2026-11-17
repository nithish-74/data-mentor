# Moves Data Architect Mentor from this PC to the cloud server, with all progress, chats and settings.
# Usage:  powershell -ExecutionPolicy Bypass -File deploy\move_to_server.ps1 -ServerIp 1.2.3.4
param([Parameter(Mandatory = $true)][string]$ServerIp, [string]$User = "ubuntu")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$key  = Join-Path $env:USERPROFILE ".data-mentor\oracle\mentor_key"
$pkg  = Join-Path $env:USERPROFILE ".data-mentor\build\mentor.tar.gz"
$ssh  = @("-i", $key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20")

Write-Host "1/5  Checking the server answers..."
& ssh @ssh "$User@$ServerIp" "echo connected: `$(lsb_release -ds) `$(uname -m)"
if ($LASTEXITCODE -ne 0) { throw "Can't connect to $ServerIp. Check the IP and that the server is running." }

Write-Host "2/5  Stopping the mentor on this PC (so the data is final and the link can move)..."
Stop-ScheduledTask -TaskName DataModelMentor -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(pythonw|ngrok|cloudflared)\.exe$' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3

Write-Host "3/5  Packing the mentor and its data..."
New-Item -ItemType Directory -Force (Split-Path $pkg) | Out-Null
Remove-Item $pkg -ErrorAction SilentlyContinue
& tar -czf $pkg -C $root --exclude=./installers --exclude=./dist --exclude=./.venv --exclude=__pycache__ `
    --exclude=./data/mentor.log* --exclude=./data/public_url.txt .
if ($LASTEXITCODE -ne 0) { throw "Packing failed" }
"     {0:N1} MB" -f ((Get-Item $pkg).Length / 1MB)

Write-Host "4/5  Copying to the server and installing (takes 3-6 minutes)..."
& scp @ssh $pkg (Join-Path $root "deploy\setup_server.sh") "${User}@${ServerIp}:~/"
if ($LASTEXITCODE -ne 0) { throw "Copy failed" }
& ssh @ssh "$User@$ServerIp" "sed -i 's/\r$//' ~/setup_server.sh && bash ~/setup_server.sh"
if ($LASTEXITCODE -ne 0) { throw "Server setup failed (see the messages above)" }

Write-Host "5/5  Turning off the PC copy for good (the server runs it now)..."
Disable-ScheduledTask -TaskName DataModelMentor | Out-Null
Write-Host "Done. Mentor now runs on $ServerIp, around the clock, at the same link."
