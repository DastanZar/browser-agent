# Launch a separate Chrome window that an agent can drive over CDP (port 9222). Windows version.
# It uses its own profile in %USERPROFILE%\.agent-chrome: log in to Google there once and it stays logged in.
# (Chrome 136+ refuses remote debugging on your default profile, so a separate one is required.)
param([int]$Port = 9222, [string]$Profile = "$env:USERPROFILE\.agent-chrome")

$chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "Chrome not found" }

New-Item -ItemType Directory -Force -Path $Profile | Out-Null
Write-Host "Chrome on http://127.0.0.1:$Port  (profile: $Profile)"
& $chrome "--remote-debugging-port=$Port" "--remote-debugging-address=127.0.0.1" "--user-data-dir=$Profile" --no-first-run --no-default-browser-check
