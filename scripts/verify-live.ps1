param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")))

$ErrorActionPreference = "Stop"
$webEnv = Join-Path $ProjectRoot ".env.local"
$python = Join-Path $ProjectRoot "services\venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $webEnv)) { throw "Missing .env.local." }
if (-not (Test-Path -LiteralPath $python)) { throw "Missing services\venv. Create the backend virtual environment first." }

function Read-EnvValue([string]$Path, [string]$Name) {
  $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
  if (-not $line) { throw "Missing $Name in $Path." }
  return ($line -replace "^$Name=", "").Trim()
}

$env:RUN_LIVE_SUPABASE_TESTS = "1"
$env:SUPABASE_URL = Read-EnvValue $webEnv "NEXT_PUBLIC_SUPABASE_URL"
$env:SUPABASE_SERVICE_ROLE_KEY = Read-EnvValue $webEnv "SUPABASE_SERVICE_ROLE_KEY"
$env:SUPABASE_ANON_KEY = Read-EnvValue $webEnv "NEXT_PUBLIC_SUPABASE_ANON_KEY"

& $python -c "import pytest" 2>$null
if ($LASTEXITCODE -ne 0) { throw "pytest is missing from services\venv. Run: .\services\venv\Scripts\python.exe -m pip install pytest" }
& $python -c "from supabase import create_client; from postgrest.exceptions import APIError" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Worker dependencies are missing from services\venv. Run: .\services\venv\Scripts\python.exe -m pip install -e .\services\analytics-worker" }

Push-Location (Join-Path $ProjectRoot "services\analytics-worker")
try {
  & $python -m pytest ".\tests\test_live_supabase.py" -m live -q
} finally {
  Pop-Location
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
