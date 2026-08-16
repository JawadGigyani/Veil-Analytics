param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
)

$ErrorActionPreference = "Stop"
$nextEnv = Join-Path $ProjectRoot ".env.local"
$workerDir = Join-Path $ProjectRoot "services\analytics-worker"
$workerEnv = Join-Path $workerDir ".env"

if (-not (Test-Path -LiteralPath $nextEnv)) {
  throw "Missing .env.local. Create it with NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY first."
}

function Read-EnvValue([string]$Path, [string]$Name) {
  $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
  if (-not $line) { return $null }
  return ($line -replace "^$Name=", "").Trim()
}

$supabaseUrl = Read-EnvValue $nextEnv "NEXT_PUBLIC_SUPABASE_URL"
$serviceKey = Read-EnvValue $nextEnv "SUPABASE_SERVICE_ROLE_KEY"
if ([string]::IsNullOrWhiteSpace($supabaseUrl) -or [string]::IsNullOrWhiteSpace($serviceKey)) {
  throw "NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in .env.local."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python is required. Install Python 3.12+ and retry." }
$fernetKey = (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($fernetKey)) { throw "Could not generate ENCRYPTION_KEY. Run: pip install cryptography" }
$workerToken = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 })) -replace "\+","-" -replace "/","_" -replace "=",""

$workerLines = @(
  "ENCRYPTION_KEY=$fernetKey",
  "STORAGE_ROOT=.veil-storage",
  "MAX_UPLOAD_BYTES=25000000",
  "WORKER_TOKEN=$workerToken",
  "SUPABASE_URL=$supabaseUrl",
  "SUPABASE_SERVICE_ROLE_KEY=$serviceKey",
  "STORAGE_BUCKET=protected-datasets"
)
[System.IO.File]::WriteAllLines($workerEnv, $workerLines, (New-Object System.Text.UTF8Encoding($false)))

$nextLines = Get-Content -LiteralPath $nextEnv
$nextLines = @($nextLines | Where-Object { $_ -notmatch "^ANALYTICS_WORKER_URL=" -and $_ -notmatch "^ANALYTICS_WORKER_TOKEN=" })
$nextLines += "ANALYTICS_WORKER_URL=http://localhost:8080"
$nextLines += "ANALYTICS_WORKER_TOKEN=$workerToken"
[System.IO.File]::WriteAllLines($nextEnv, $nextLines, (New-Object System.Text.UTF8Encoding($false)))

Write-Output "Local configuration created:"
Write-Output "- $workerEnv"
Write-Output "- $nextEnv updated with ANALYTICS_WORKER_URL and a matching token"
Write-Output "Secrets were not printed. Start the worker and web app from the runbook."
