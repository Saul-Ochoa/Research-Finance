@echo off
REM scripts\run_daily.bat
REM Thin wrapper for run_daily.ps1, called by Windows Task Scheduler (D-026).
REM Usage: run_daily.bat [--DryRun] [--Force] [-Tickers NVDA,SPY]
REM Propagates the exit code of run_daily.ps1 (0 ok/skip, 1 failure, 2 fatal).

setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] venv not found at .venv\Scripts\activate.bat
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\run_daily.ps1" %*

exit /b %ERRORLEVEL%
