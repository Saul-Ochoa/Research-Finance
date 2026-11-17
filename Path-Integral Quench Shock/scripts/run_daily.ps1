<#
.SYNOPSIS
    Daily shadow pipeline: fetch today's 1-minute bars, gate on data, run the frozen forecast.

.DESCRIPTION
    Orchestrator for Windows Task Scheduler (called through scripts\run_daily.bat), Mon-Fri at
    night, Peru time (D-026). Steps:

      Gate 1  weekend in Peru time            -> exit 0 (before touching the venv)
      Guard   Peru date must equal ET date    -> exit 2 (schedule crosses midnight ET)
      Step 1  scripts\fetch_today.py --date <today>   (its failure is a WARNING; the gate decides)
      Gate 2  every ticker's CSV for today exists with >= 31 lines (header + 30 bars)
              otherwise -> "holiday or market closed", exit 0   (bypass with -Force)
      Step 2  scripts\shadow_predict.py --mode daily_close_overnight --tickers ...

    Every line printed by the Python scripts is copied into logs\run_daily_YYYY-MM-DD.log, so the
    orchestrator log is complete even when no console is attached. Logs are never deleted here.

.PARAMETER DryRun
    Pass --dry-run to shadow_predict.py (no JSONL written). fetch_today.py still saves today's CSV.

.PARAMETER Force
    Skip Gate 2 (no-data gate). Never skips the weekend gate or the date guard.

.PARAMETER Tickers
    Tickers to run (default NVDA, SPY). From the .bat use a comma list: -Tickers NVDA,SPY

.PARAMETER SimulateNowUtc
    TEST ONLY. Pretend the run happens at this UTC instant (ISO-8601, e.g. 2026-11-14T04:00:00Z).
    Implies -DryRun, skips the fetch (never re-downloads archived days) and passes the same
    instant to shadow_predict.py as --timestamp.

.NOTES
    Exit codes (aligned with shadow_predict.py):
      0  completed, already logged, or skipped (weekend, holiday, market closed)
      1  shadow_predict.py reported a failed ticker, or an unexpected error
      2  fatal: venv missing, bad arguments, unknown time zone, schedule crosses midnight ET,
         or shadow_predict.py exit 2 (tampered parameters, invalid config)

    Accepts both PowerShell (-DryRun, -Force) and GNU-style (--DryRun, --dry-run, --Force) flags,
    so `run_daily.bat --DryRun` works.
    Compatible with Windows PowerShell 5.1 and PowerShell 7.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$DryRun,
    [switch]$Force,
    [string[]]$Tickers = @("NVDA", "SPY"),
    [string]$SimulateNowUtc = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"
$MinCsvLines = 31   # header + 30 barras de 1 minuto

# --- Rutas -----------------------------------------------------------------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LogDir      = Join-Path $ProjectRoot "logs"
$Python      = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$script:LogFile = $null
$script:PeruTz  = $null

function Write-Log([string]$Level, [string]$Message) {
    # Timestamp real (no simulado) en hora Peru con offset
    $u = [DateTime]::UtcNow
    $ts = $u.ToString("yyyy-MM-ddTHH:mm:ss") + "Z"
    if ($script:PeruTz) {
        $ts = ([DateTimeOffset]$u).ToOffset($script:PeruTz.GetUtcOffset($u)).ToString("yyyy-MM-ddTHH:mm:sszzz")
    }
    $line = "$ts [$Level] $Message"
    Write-Host $line
    if ($script:LogFile) {
        try { Add-Content -Path $script:LogFile -Value $line -Encoding UTF8 } catch { Write-Host "(cannot write log: $_)" }
    }
}

function Invoke-Python([string[]]$Arguments) {
    # Corre python del venv; copia stdout+stderr a consola y al log. Devuelve el exit code.
    # EAP=Continue: en PS 5.1 el stderr de un nativo con 2>&1 llega como ErrorRecord y con
    # EAP=Stop abortaria el script (Python escribe sus logs en stderr).
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $script:Python @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $text = $_.Exception.Message } else { $text = "$_" }
            Write-Host $text
            if ($script:LogFile) { Add-Content -Path $script:LogFile -Value "    | $text" -Encoding UTF8 }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

trap {
    Write-Log "ERROR" "Unhandled error: $_"
    exit 1
}

# --- Zona horaria y fecha (Peru) --------------------------------------------
try {
    $script:PeruTz = [TimeZoneInfo]::FindSystemTimeZoneById("SA Pacific Standard Time")
    $EtTz = [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
} catch {
    Write-Log "ERROR" "Time zone not found on this host: $_. Exiting 2."
    exit 2
}

if ($SimulateNowUtc) {
    $styles = [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal
    $NowUtc = [DateTime]::Parse($SimulateNowUtc, [Globalization.CultureInfo]::InvariantCulture, $styles)
} else {
    $NowUtc = [DateTime]::UtcNow
}
$NowPeru   = [TimeZoneInfo]::ConvertTimeFromUtc($NowUtc, $script:PeruTz)
$NowEt     = [TimeZoneInfo]::ConvertTimeFromUtc($NowUtc, $EtTz)
$TodayPeru = $NowPeru.ToString("yyyy-MM-dd")
$Dow       = $NowPeru.DayOfWeek

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$suffix = if ($SimulateNowUtc) { "_simulated" } else { "" }   # las simulaciones no se mezclan con logs reales
$script:LogFile = Join-Path $LogDir "run_daily_$TodayPeru$suffix.log"

# --- Argumentos estilo GNU (desde el .bat) ----------------------------------
foreach ($a in $ExtraArgs) {
    switch -Regex ($a) {
        '^--?dry-?run$' { $DryRun = $true; continue }
        '^--?force$'    { $Force = $true; continue }
        default {
            Write-Log "ERROR" "Unknown argument '$a'. Valid: -DryRun, -Force, -Tickers NVDA,SPY. Exiting 2."
            exit 2
        }
    }
}
# "-Tickers NVDA,SPY" llega como un solo string cuando se usa -File
$Tickers = @($Tickers | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim().ToUpper() } | Where-Object { $_ })
if ($SimulateNowUtc) { $DryRun = $true }

Write-Log "INFO" ("run_daily.ps1 start: date_peru=$TodayPeru ($Dow) now_peru=" + $NowPeru.ToString("HH:mm") +
                  " now_et=" + $NowEt.ToString("yyyy-MM-dd HH:mm") + " tickers=" + ($Tickers -join ",") +
                  " dry_run=$DryRun force=$Force" + $(if ($SimulateNowUtc) { " SIMULATED_NOW_UTC=$SimulateNowUtc" } else { "" }))

# --- Gate 1: fin de semana (antes de tocar el venv) -------------------------
if ($Dow -eq [DayOfWeek]::Saturday -or $Dow -eq [DayOfWeek]::Sunday) {
    Write-Log "INFO" "Weekend in Peru ($Dow). Exiting 0."
    exit 0
}

# --- Guard: la fecha Peru debe ser la fecha ET ------------------------------
# Con horario de verano en EE.UU. (mar-nov), ET = UTC-4 va 1 h por delante de Peru (UTC-5):
# 23:00 Peru = 00:00 ET del dia siguiente, y shadow_predict tomaria ese dia como fecha de
# prediccion (skip silencioso). Se falla en voz alta en vez de saltar en silencio.
if ($NowEt.Date -ne $NowPeru.Date) {
    Write-Log "ERROR" ("Peru date $TodayPeru differs from New York date " + $NowEt.ToString("yyyy-MM-dd") +
                       " (now " + $NowEt.ToString("HH:mm") + " ET). The prediction date would be wrong. " +
                       "Schedule the task before 23:00 Peru (e.g. 22:00) so it never crosses midnight ET. Exiting 2.")
    exit 2
}

# --- venv --------------------------------------------------------------------
if (-not (Test-Path $Python)) {
    Write-Log "ERROR" "venv not found: $Python. Create it (python -m venv .venv; pip install -r requirements.txt). Exiting 2."
    exit 2
}
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }   # sin consola (scheduler): ignorar

Push-Location $ProjectRoot
try {
    # --- Paso 1: fetch -------------------------------------------------------
    if ($SimulateNowUtc) {
        Write-Log "INFO" "Simulation: fetch skipped (archived CSVs are never re-downloaded)."
    } else {
        Write-Log "INFO" "Step 1: fetch_today.py --date $TodayPeru"
        $fetchExit = Invoke-Python (@("scripts\fetch_today.py", "--date", $TodayPeru, "--tickers") + $Tickers)
        if ($fetchExit -ne 0) {
            # fetch_today devuelve 1 cuando ningun ticker tiene datos (feriado): decide el gate
            Write-Log "WARNING" "fetch_today.py exit $fetchExit (no data saved for at least one ticker); the data gate decides."
        }
    }

    # --- Gate 2: datos del dia, por ticker -----------------------------------
    $missing = @()
    foreach ($t in $Tickers) {
        $csv = Join-Path $ProjectRoot "data\raw\yahoo_1m\$t\$TodayPeru.csv"
        if (-not (Test-Path $csv)) {
            $missing += "$t (no CSV)"
        } else {
            $lines = (Get-Content $csv | Measure-Object -Line).Lines
            if ($lines -lt $MinCsvLines) { $missing += "$t ($lines lines)" }
        }
    }
    if ($missing.Count -gt 0) {
        if (-not $Force) {
            Write-Log "INFO" ("No valid data for $TodayPeru (missing: " + ($missing -join ", ") +
                              "). Holiday or market closed. Skipping model. Exit 0.")
            exit 0
        }
        Write-Log "WARNING" ("Gate bypassed by -Force (missing: " + ($missing -join ", ") + ").")
    } else {
        Write-Log "INFO" ("Data gate passed for $TodayPeru (" + ($Tickers -join ",") + ").")
    }

    # --- Paso 2: predecir ----------------------------------------------------
    $predictArgs = @("scripts\shadow_predict.py", "--mode", "daily_close_overnight", "--tickers") + $Tickers
    if ($DryRun) { $predictArgs += "--dry-run" }
    if ($SimulateNowUtc) {
        # ojo: en PowerShell la coma liga mas fuerte que '+'; construir el string antes
        $tsArg = $NowUtc.ToString("yyyy-MM-ddTHH:mm:ss") + "+00:00"
        $predictArgs += @("--timestamp", $tsArg)
    }
    Write-Log "INFO" ("Step 2: " + ($predictArgs -join " "))
    $predictExit = Invoke-Python $predictArgs
} finally {
    Pop-Location
}

switch ($predictExit) {
    0 {
        $what = if ($DryRun) { "dry run, nothing written" } else { "written, already logged or skipped per ticker" }
        Write-Log "INFO" "run_daily.ps1 completed successfully for $TodayPeru ($what; see logs\shadow_predict_*.log)."
        exit 0
    }
    2 {
        Write-Log "ERROR" "shadow_predict.py fatal error (exit 2): nothing was written. See logs\shadow_predict_*.log."
        exit 2
    }
    default {
        Write-Log "ERROR" "shadow_predict.py exit ${predictExit}: at least one ticker failed. See logs\shadow_predict_*.log."
        exit 1
    }
}
