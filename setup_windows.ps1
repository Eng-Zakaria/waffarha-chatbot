# Waffarha chatbot -- fresh-machine setup (Windows PowerShell).
#
#   Default (venv, setup only):
#     powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
#   System libs instead of venv:
#     powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 -Mode system -Yes
#   Setup + start servers (:8000 + :8001):
#     powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 -Run
#
# Modes: -Mode venv (default, isolated ./venv) | -Mode system (install into
# current python env directly, no venv). -Run starts run_servers.py after a
# successful setup. -SkipOllama skips all Ollama work. Re-running is safe:
# every step checks what already exists first and only fills the gaps.
param(
  [ValidateSet("venv", "system")][string]$Mode = "venv",
  [switch]$Run,
  [switch]$SkipOllama,
  [switch]$Yes
)
$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Found($msg) { Write-Host "  [FOUND]  $msg" -ForegroundColor Green }
function Missing($msg) { Write-Host "  [MISSING] $msg" -ForegroundColor Yellow }
function Fixed($msg) { Write-Host "  [FIXED]  $msg" -ForegroundColor Green }

# 0. Must run from project root
if (-not (Test-Path ".\requirements.txt") -and -not (Test-Path ".\docs\requirements.txt")) {
  Write-Error "Run this from the project root (requirements.txt not found)."
}
$ReqFile = if (Test-Path ".\requirements.txt") { "requirements.txt" } else { "docs/requirements.txt" }

# 1. Python version check
Step "1/7 Python"
$pyVersion = (python --version 2>&1).ToString()
python -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info < (3,14) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Error "Need Python 3.11-3.13 (got: $pyVersion). Install 3.11 from python.org." }
Found $pyVersion

# 2. Pick interpreter: venv (default) or system libs directly
Step "2/7 Python environment (mode: $Mode)"
if ($Mode -eq "system") {
  if (-not $Yes) {
    $ans = Read-Host "Mode=system installs packages into your CURRENT python (no venv). Continue? [y/N]"
    if ($ans -notin @("y", "Y", "yes", "YES")) { Write-Host "Aborted. Re-run with default -Mode venv."; exit 0 }
  }
  $PY = "python"; $PIP = "python -m pip"
  Missing "venv (skipped by design in system mode) -> using current python"
} else {
  if (Test-Path ".\venv\Scripts\python.exe") { Found "venv already exists -> reusing" }
  else { Missing "venv -> creating"; python -m venv venv; Fixed "venv created" }
  $PY = ".\venv\Scripts\python"; $PIP = ".\venv\Scripts\pip"
}

# 3. Python deps (check first, install only gaps -- pip itself is idempotent)
Step "3/7 Python packages ($ReqFile)"
& $PY -c "import fastapi, uvicorn, sentence_transformers, rank_bm25, qdrant_client, redis, clickhouse_connect, numpy" 2>$null
if ($LASTEXITCODE -eq 0) { Found "key imports already work -> refreshing from $ReqFile (fast, no-op if satisfied)" }
else { Missing "some imports failed -> installing" }
& $PY -m pip install --upgrade pip | Out-Null
Invoke-Expression "$PIP install --default-timeout=120 --retries 10 -r $ReqFile"
& $PY -c "import fastapi, uvicorn, sentence_transformers, rank_bm25, qdrant_client, redis, clickhouse_connect, numpy; print('  [OK] py deps verified')"
if ($LASTEXITCODE -ne 0) { Write-Error "pip install finished but verification failed." }

# 4. .env (never overwrite)
Step "4/7 .env"
if (Test-Path ".\.env") { Found ".env exists -> untouched" }
elseif (Test-Path ".\.env.example") { Copy-Item ".\.env.example" ".\.env"; Fixed ".env created from .env.example (edit CLICKHOUSE_* only to rebuild the index)" }
else { Missing ".env.example not found -> skipped" }

# 5. Ollama: binary -> server -> model (each checked before acting)
Step "5/7 Ollama"
$model = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { "qwen2.5:3b-instruct" }
if ($SkipOllama) { Missing "skipped via -SkipOllama (servers will fail without it)"; }
else {
  $ollamaBin = $false
  try { ollama --version | Out-Null; $ollamaBin = $true } catch { $ollamaBin = $false }
  if (-not $ollamaBin) {
    Missing "ollama binary not found -> install from https://ollama.com/download/windows, then re-run"
  } else {
    Found ((ollama --version 2>&1).ToString())
    ollama list 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
      Missing "ollama server not responding -> starting 'ollama serve' in background"
      Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
      $tries = 0
      do { Start-Sleep 1; ollama list 2>$null | Out-Null; $tries++ } while ($LASTEXITCODE -ne 0 -and $tries -lt 30)
      if ($LASTEXITCODE -eq 0) { Fixed "ollama server is up" } else { Write-Warning "ollama serve did not respond; start it manually and re-run." }
    } else { Found "ollama server responding" }
    ollama show $model 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Found "model '$model' already cached -> no download" }
    else { Missing "model '$model' not cached -> pulling (~2GB, one time)"; ollama pull $model }
  }
}

# 6. Readiness: index + ports
Step "6/7 Readiness"
$idx = & $PY -c "import pathlib; print(len(list(pathlib.Path('data/index').glob('*'))))" 2>$null
if ($idx -and [int]$idx.Trim() -gt 0) { Found "data/index present ($($idx.Trim()) entries)" }
else { Missing "data/index empty -> copy it in or run: $PY ingestion/refresh.py (needs CLICKHOUSE_* in .env)" }
foreach ($port in @(8000, 8001)) {
  $tcp = New-Object Net.Sockets.TcpClient
  try { $tcp.Connect("127.0.0.1", $port); $tcp.Close(); Missing "port $port is BUSY (server already running?)" }
  catch { Found "port $port free" }
}

# 7. Run (optional)
if ($Run) {
  Step "7/7 Starting servers"
  Write-Host "Serving :8000 (cascade) + :8001 (agent) -- Ctrl+C to stop." -ForegroundColor Green
  & $PY run_servers.py
} else {
  Step "7/7 Done (not started)"
  Write-Host "Start with: $PY run_servers.py   OR   re-run with -Run" -ForegroundColor Green
  Write-Host "Then open http://localhost:8000 and http://localhost:8001"
}
