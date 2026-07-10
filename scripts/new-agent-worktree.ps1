<#
.SYNOPSIS
  Create a fully-isolated git worktree for a parallel Claude Code agent.

.DESCRIPTION
  Stamps out a sibling worktree on its own branch and wires up everything that
  is NOT shared through git so a second agent can run the full app without
  colliding with the others:

    * copies .env (gitignored, holds secrets)
    * gives the worktree its own data dir + SQLite DB (ART_DATA_DIR / DATABASE_URL)
      so running the app never mutates the shared Postgres data
    * assigns distinct backend + frontend ports
    * reuses the repo-root .venv (no per-worktree venv needed)
    * optionally installs web/frontend node_modules (needed to run Vite)

.PARAMETER Name
  Short slug for the worktree, e.g. "auth". Folder becomes ../art-auth.

.PARAMETER Index
  Port offset (1..N). Backend = 8000+Index, Frontend = 5173+Index.
  Defaults to (existing worktree count) so ports don't collide.

.PARAMETER Branch
  Branch to create. Default: agent/<Name>.

.PARAMETER BaseRef
  Ref to branch from. Default: main.

.PARAMETER Frontend
  Run `npm install` in the worktree so you can run the Vite dev server.

.PARAMETER SharedDb
  Skip DB isolation - use the DATABASE_URL from .env (shared Postgres) instead
  of a private SQLite file. Use when the agent needs the real seeded data and
  you accept that concurrent app runs share one database.

.PARAMETER Launch
  After setup, open a new Windows Terminal tab in the worktree already running
  `claude`. Falls back to printing the command if wt.exe is unavailable.

.EXAMPLE
  ./scripts/new-agent-worktree.ps1 -Name auth -Frontend

.EXAMPLE
  ./scripts/new-agent-worktree.ps1 -Name issue-95 -Launch
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Name,
  [int]$Index,
  [string]$Branch,
  [string]$BaseRef = "main",
  [switch]$Frontend,
  [switch]$SharedDb,
  [switch]$Launch
)

$ErrorActionPreference = "Stop"

# Run a native exe without letting its (often informational) stderr abort the
# script under `$ErrorActionPreference = 'Stop'` in Windows PowerShell 5.1.
function Invoke-Native {
  param([Parameter(Mandatory)][string]$Exe,
        [Parameter(ValueFromRemainingArguments)]$Rest)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $Exe @Rest 2>&1 | ForEach-Object { Write-Host ([string]$_) }
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($code -ne 0) { throw "$Exe $($Rest -join ' ') failed (exit $code)" }
}

# --- locate repo root (this script lives in <root>/scripts) ---
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path (Join-Path $root "config.py"))) {
  throw "Could not find repo root (no config.py next to scripts/). Run from the repo."
}

if (-not $Branch) { $Branch = "agent/$Name" }
if (-not $Index) {
  # default index = number of existing worktrees (main counts as 0-ish offset)
  $Index = (git worktree list | Measure-Object -Line).Lines
}

$backendPort  = 8000 + $Index
$frontendPort = 5173 + $Index
$wtPath = (Join-Path (Split-Path $root -Parent) "art-$Name")

if (Test-Path $wtPath) { throw "Worktree path already exists: $wtPath" }

Write-Host "==> Creating worktree" -ForegroundColor Cyan
Write-Host "    path:     $wtPath"
Write-Host "    branch:   $Branch  (from $BaseRef)"
Write-Host "    backend:  http://localhost:$backendPort"
Write-Host "    frontend: http://localhost:$frontendPort"

Invoke-Native git -C $root worktree add -b $Branch $wtPath $BaseRef

# --- .env: copy + append per-worktree overrides (last line wins in dotenv) ---
$srcEnv = Join-Path $root ".env"
$dstEnv = Join-Path $wtPath ".env"
if (Test-Path $srcEnv) {
  Copy-Item $srcEnv $dstEnv
} else {
  Write-Warning "No .env at repo root; the worktree will have none."
  New-Item -ItemType File $dstEnv | Out-Null
}

$dataDir = Join-Path $wtPath ".artdata"
New-Item -ItemType Directory -Force $dataDir | Out-Null
# sqlite URL wants forward slashes and an absolute path
$dbUrl = "sqlite:///" + ($dataDir -replace '\\','/') + "/art.db"
$dataDirFwd = $dataDir -replace '\\','/'

$block = @"

# --- worktree isolation (added by new-agent-worktree.ps1) ---
ART_DATA_DIR=$dataDirFwd
"@
if (-not $SharedDb) {
  $block += "`nDATABASE_URL=$dbUrl`n"
  Write-Host "==> DB: private SQLite at $dbUrl (starts empty - register a local user)" -ForegroundColor Yellow
} else {
  $block += "`n# SharedDb: using DATABASE_URL from copied .env (shared Postgres)`n"
  Write-Host "==> DB: SHARED (DATABASE_URL from .env) - concurrent runs share data" -ForegroundColor Yellow
}
Add-Content -Path $dstEnv -Value $block -Encoding utf8

# --- frontend deps (only if you'll run Vite) ---
if ($Frontend) {
  Write-Host "==> npm install in web/frontend (this can take a minute)" -ForegroundColor Cyan
  Push-Location (Join-Path $wtPath "web/frontend")
  try { Invoke-Native npm install } finally { Pop-Location }
}

# --- print the run recipe ---
$venvActivate = Join-Path $root ".venv/Scripts/Activate.ps1"
Write-Host ""
Write-Host "Worktree ready. Open TWO terminals in $wtPath :" -ForegroundColor Green
Write-Host ""
Write-Host "  # Terminal 1 - backend (reuses the shared repo-root venv)" -ForegroundColor DarkGray
Write-Host "  & '$venvActivate'"
Write-Host "  cd '$wtPath'"
Write-Host "  uvicorn web.app:app --port $backendPort --reload"
Write-Host ""
Write-Host "  # Terminal 2 - frontend" -ForegroundColor DarkGray
Write-Host "  cd '$wtPath/web/frontend'"
Write-Host "  `$env:VITE_API_PORT=$backendPort; npm run dev -- --port $frontendPort"
Write-Host ""
Write-Host "  Then launch an agent:  cd '$wtPath'; claude" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Remove when done:  git worktree remove '$wtPath'" -ForegroundColor DarkGray

# --- optionally open a new Windows Terminal tab already running claude ---
if ($Launch) {
  $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
  if ($wt) {
    Write-Host ""
    Write-Host "==> Opening a new Windows Terminal tab in $wtPath ..." -ForegroundColor Cyan
    # -w 0 nt = new tab in the current window; -d sets its starting directory
    & $wt.Source -w 0 nt -d "$wtPath" powershell -NoExit -Command "claude"
  } else {
    Write-Warning "-Launch requested but wt.exe not found; open the terminal manually with the recipe above."
  }
}
