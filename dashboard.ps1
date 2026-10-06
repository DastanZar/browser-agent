# Browser Agent dashboard launcher (Windows). Easiest: double-click "Start Browser Agent.cmd".
# Or from PowerShell:  powershell -ExecutionPolicy Bypass -File dashboard.ps1
# First run creates .venv and installs everything (a few minutes). Needs Python 3.11 or newer.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"              # Browser Use logs emoji; avoid cp1252 console errors
$env:ANONYMIZED_TELEMETRY = "false"

function Fail($msg) {
  Write-Host ""
  Write-Host "ERROR: $msg" -ForegroundColor Red
  Write-Host ""
  Read-Host "Press Enter to close"
  exit 1
}

function Find-Python {
  # Prefer the py launcher with a known-good version, then python on PATH.
  foreach ($v in @("3.13", "3.12", "3.11", "3.14")) {
    try { $out = & py "-$v" -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $out) { return $out.Trim() } } catch {}
  }
  foreach ($name in @("python", "python3")) {
    try {
      $ok = & $name -c "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')" 2>$null
      if ($LASTEXITCODE -eq 0 -and $ok -and $ok.Trim()) { return $ok.Trim() }
    } catch {}
  }
  return $null
}

try {
  if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $py = Find-Python
    if (-not $py -and (Get-Command winget -ErrorAction SilentlyContinue)) {
      Write-Host "Python 3.11+ not found. Installing Python 3.12 for your user account with winget (one-time)..."
      & winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
      $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
      if (Test-Path $candidate) { $py = $candidate } else { $py = Find-Python }
    }
    if (-not $py) {
      Fail "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
    }
    Write-Host "First run: creating .venv with $py and installing (one-time, a few minutes)..."
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
    & .venv\Scripts\python.exe -m pip install --upgrade pip
    & .venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
      Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
      Fail "Installing the requirements failed (see the messages above). Fix that, then run this again."
    }
  }
  Write-Host "Starting the dashboard... it opens http://127.0.0.1:8770 in your browser."
  & .venv\Scripts\python.exe dashboard.py
  if ($LASTEXITCODE -ne 0) { Fail "The dashboard stopped with an error (see above)." }
} catch {
  Fail $_.Exception.Message
}
