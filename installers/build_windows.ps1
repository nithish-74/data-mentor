# Builds dist\DataArchitectMentor-Setup.exe with the C# compiler that ships with Windows (.NET Framework 4.8).
# Re-run after changing the site address (NGROK_DOMAIN in .env).
$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$win   = Join-Path $root "installers\windows"
$dist  = Join-Path $root "dist"
$build = Join-Path $env:USERPROFILE ".data-mentor\build\windows"
$py    = Join-Path $env:USERPROFILE ".data-mentor\venv\Scripts\python.exe"
$csc   = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
New-Item -ItemType Directory -Force $dist, $build | Out-Null

$domain = (Select-String -Path (Join-Path $root ".env") -Pattern '^NGROK_DOMAIN=(.+)$').Matches[0].Groups[1].Value.Trim()
if (-not $domain) { throw "NGROK_DOMAIN is empty in .env" }
$url = "https://$domain/?source=windows#home"

# icon (multi-size .ico from the app's PNG)
& $py -c "from PIL import Image; im=Image.open(r'$root\static\icons\icon-512.png'); im.save(r'$build\mentor.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

(Get-Content (Join-Path $win "Setup.cs") -Raw).Replace("__APP_URL__", $url) | Set-Content (Join-Path $build "Setup.cs") -Encoding utf8
$out = Join-Path $build "DataArchitectMentor-Setup.exe"
& $csc /nologo /target:winexe /optimize+ "/win32icon:$build\mentor.ico" "/out:$out" `
    /reference:System.Windows.Forms.dll /reference:Microsoft.CSharp.dll /reference:System.Core.dll "$build\Setup.cs"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $out)) { throw "C# compile failed" }
Copy-Item $out (Join-Path $dist "DataArchitectMentor-Setup.exe") -Force
"Built dist\DataArchitectMentor-Setup.exe ({0:N0} KB) for $url" -f ((Get-Item $out).Length / 1KB)
