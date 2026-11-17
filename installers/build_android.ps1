# Builds dist\DataArchitectMentor.apk (signed, ready to share) using Android Studio's Java + the cached Gradle.
# Re-run after changing the site address (NGROK_DOMAIN in .env). Bump versionCode in app\build.gradle for updates.
$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$proj    = Join-Path $root "installers\android"
$dist    = Join-Path $root "dist"
$runtime = Join-Path $env:USERPROFILE ".data-mentor"
$sdk     = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$env:JAVA_HOME    = "C:\Program Files\Android\Android Studio\jbr"
$env:ANDROID_HOME = $sdk
New-Item -ItemType Directory -Force $dist | Out-Null

$domain = (Select-String -Path (Join-Path $root ".env") -Pattern '^NGROK_DOMAIN=(.+)$').Matches[0].Groups[1].Value.Trim()
if (-not $domain) { throw "NGROK_DOMAIN is empty in .env" }

& (Join-Path $runtime "venv\Scripts\python.exe") (Join-Path $proj "make_icons.py")
"sdk.dir=$($sdk -replace '\\','/')" | Set-Content (Join-Path $proj "local.properties") -Encoding ascii

$gradle = Get-ChildItem (Join-Path $env:USERPROFILE ".gradle\wrapper\dists\gradle-8.13-bin") -Recurse -Filter gradle.bat |
          Where-Object { $_.FullName -match '\\bin\\gradle\.bat$' } | Select-Object -First 1
if (-not $gradle) { throw "Gradle 8.13 not found in ~/.gradle" }

& $gradle.FullName -p $proj --no-daemon --console=plain "--project-cache-dir=$runtime\build\android-gradle-cache" `
    assembleRelease "-PmentorHost=$domain" "-PkeystoreProps=$runtime\android\keystore.properties"
if ($LASTEXITCODE -ne 0) { throw "Gradle build failed" }

$apk = Join-Path $runtime "build\android-app\outputs\apk\release\app-release.apk"
Copy-Item $apk (Join-Path $dist "DataArchitectMentor.apk") -Force
$bt = Get-ChildItem (Join-Path $sdk "build-tools") | Sort-Object Name -Descending | Select-Object -First 1
& (Join-Path $bt.FullName "apksigner.bat") verify --print-certs (Join-Path $dist "DataArchitectMentor.apk") | Select-String "SHA-256"
"Built dist\DataArchitectMentor.apk ({0:N1} MB) for https://$domain" -f ((Get-Item (Join-Path $dist "DataArchitectMentor.apk")).Length / 1MB)
