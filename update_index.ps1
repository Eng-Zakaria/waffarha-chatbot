# Waffarha chatbot -- fetch + rebuild the vector index (Windows PowerShell).
#
#   .\update_index.ps1 -Plan                  # dry-run lineage report (no ClickHouse, no writes)
#   .\update_index.ps1                        # full: fetch from ClickHouse + rebuild (default backend/model from .env)
#   .\update_index.ps1 -SkipFetch             # rebuild from existing data/ files (no ClickHouse)
#   .\update_index.ps1 -Steps "sources build" # run selected steps only
#
# Stop the chat servers (:8000/:8001) first -- the Qdrant index folder can't be
# rebuilt while a server holds its folder lock.
param(
  [ValidateSet("venv", "system")][string]$Mode = "venv",
  [switch]$Plan,
  [switch]$SkipFetch,
  [string]$Backend = "",
  [string]$Model = "",
  [string]$Steps = ""
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\ingestion\refresh.py")) { Write-Error "Run this from the project root." }
$PY = if ($Mode -eq "system") { "python" } else { ".\venv\Scripts\python" }
if (($Mode -eq "venv") -and (-not (Test-Path ".\venv\Scripts\python.exe"))) {
  Write-Error "venv not found. Run .\setup_windows.ps1 first (or use -Mode system)."
}

$extra = @()
if ($Backend) { $extra += @("--backend", $Backend) }
if ($Model) { $extra += @("--embedding-model", $Model) }

if ($Plan) {
  Write-Host "==> Index plan (dry-run)" -ForegroundColor Cyan
  & $PY ingestion/refresh.py --plan @extra
  exit $LASTEXITCODE
}

# Warn if servers hold the Qdrant lock
foreach ($port in @(8000, 8001)) {
  $tcp = New-Object Net.Sockets.TcpClient
  try { $tcp.Connect("127.0.0.1", $port); $tcp.Close(); Write-Warning "Port $port is BUSY -- stop run_servers.py first or the rebuild may fail on the Qdrant folder lock." }
  catch { }
}

if (-not $SkipFetch) {
  if (-not (Test-Path ".\.env")) { Write-Error ".env missing. Run .\setup_windows.ps1 first, then fill CLICKHOUSE_*." }
  $envText = Get-Content ".\.env" -Raw
  if ($envText -notmatch "(?m)^CLICKHOUSE_HOST\s*=\s*\S+") { Write-Error ".env has no CLICKHOUSE_HOST -- fetching is impossible. Use -SkipFetch to build from existing data/." }
  if ($envText -notmatch "(?m)^CLICKHOUSE_PASSWORD\s*=\s*\S+") { Write-Warning "CLICKHOUSE_PASSWORD empty in .env -- fetch will fail unless your ClickHouse user has no password." }
}

$cmd = @("ingestion/refresh.py")
if ($SkipFetch) { $cmd += "--skip-fetch" }
if ($Steps) { $cmd += @("--steps") + ($Steps -split "\s+") }
Write-Host "==> Updating index: $PY $($cmd + $extra -join ' ')" -ForegroundColor Cyan
& $PY @cmd @extra
