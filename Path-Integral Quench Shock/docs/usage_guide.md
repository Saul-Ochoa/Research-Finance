# Usage guide

How to operate the project once it is installed. For the first setup, see
[`getting_started.md`](getting_started.md) *(to be written)*. For errors and exit codes, see
[`troubleshooting.md`](troubleshooting.md) *(to be written)*. For the system design, see
[`shadow_production.md`](shadow_production.md).

> **Status (2026-11-17):** §5 (Scheduling) is complete. Sections 1–4, 6 and 7 are outlined and
> will be written with the rest of the operations documentation.

## Contents

1. Overview of the operating cycle *(to be written)*
2. Daily operations: 2.1 Predict, 2.2 Evaluate (block 3), 2.3 Report (block 4) *(to be written)*
3. Maintenance: 3.1 Rebuild metrics, 3.2 Export to CSV, 3.3 Backfill (block 5) *(to be written)*
4. Re-calibration *(to be written)*
5. [Scheduling](#5-scheduling)
6. Interpreting outputs *(to be written)*
7. Common scenarios *(to be written)*

Until §2–§3 exist, the commands are in [`shadow_production.md` §4a](shadow_production.md) (predict)
and [§5a](shadow_production.md) (metrics, exports).

---

## 5. Scheduling

The daily pipeline is `scripts\run_daily.bat`. It is a thin wrapper that calls
`scripts\run_daily.ps1` with `-ExecutionPolicy Bypass` and passes on its exit code. Windows
Task Scheduler runs it **Monday to Friday at 22:00 Peru time** (D-026).

What one run does:

```text
run_daily.bat ─► run_daily.ps1
   Gate 1  weekend in Peru time?                     ─► exit 0 (the venv is never touched)
   Guard   Peru date ≠ New York date?                ─► exit 2 (wrong schedule time, see 5.1)
   Step 1  fetch_today.py --date <today>             (a failure here is only a WARNING)
   Gate 2  every ticker has today's CSV, ≥ 31 lines? ─► no: exit 0, "holiday or market closed"
   Step 2  shadow_predict.py --mode daily_close_overnight --tickers NVDA SPY
           ─► exit 0 ok / 1 a ticker failed / 2 fatal (codes passed on unchanged)
```

### 5.1 Why 22:00 Peru, and not 23:00

| | Winter (US standard time, ~Nov–Mar) | Summer (US daylight time, ~Mar–Nov) |
|---|---|---|
| Peru | UTC−5 (no daylight saving, ever) | UTC−5 |
| New York (ET) | UTC−5 | **UTC−4: one hour ahead of Peru** |
| 22:00 Peru = | 22:00 ET, same day ✅ | 23:00 ET, same day ✅ |
| 23:00 Peru = | 23:00 ET, same day | **00:00 ET, next day ❌** |

- 22:00 Peru is always on the same New York calendar day, and always at least 2 h after the
  extended-hours close (20:00 ET), so the day's data is complete.
- **23:00 Peru does not work.** During US daylight time it falls at 00:00 ET of the next day.
  `shadow_predict.py` would take that new day as the prediction date, see "before 16:05" and skip.
  On Fridays it would see a weekend. All of this happens with exit 0, every night from March to
  November, with no error.
- **Guard.** `run_daily.ps1` compares the Peru date with the New York date and exits **2** if they
  differ, with a message saying so. A wrong schedule time therefore fails loudly instead of
  skipping silently.

### 5.2 Host time zone

The script itself does not depend on the host time zone: it converts UTC to Peru and to New York
explicitly. The host time zone matters only **when the task is registered**: `-At "22:00"` is read
as the host's local time and stored as a fixed UTC instant (03:00 UTC = 22:00 Peru).

```powershell
tzutil /g                                    # must print: SA Pacific Standard Time
tzutil /s "SA Pacific Standard Time"         # change it (elevated PowerShell), then register
```

### 5.3 Register the task

Use `Register-ScheduledTask`, not `schtasks`: only the cmdlet can set `-StartWhenAvailable`. Run
this **once**, in a PowerShell opened with **Run as administrator**. Registering a task that runs
while you are logged off needs elevation. The task itself then runs without admin rights.

```powershell
$action = New-ScheduledTaskAction `
    -Execute "D:\Proyectos Quants\Research-Finance\Path-Integral Quench Shock\scripts\run_daily.bat"
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "22:00"
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Limited
Register-ScheduledTask `
    -TaskPath "\PathIntegralQuench\" `
    -TaskName "RunDaily" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal `
    -Description "NVDA/SPY daily shadow forecast (22:00 Peru)"
```

| Setting | Effect |
|---|---|
| `-DaysOfWeek Monday…Friday` | Primary weekday enforcement. `run_daily.ps1` checks the weekday again, in case the task is misfired or run by hand. |
| `-At "22:00"` | Host local time, stored as UTC (§5.2). |
| `-StartWhenAvailable` | If the 22:00 start was missed (PC off or asleep), runs as soon as possible afterwards. **Limit:** this only recovers the night's forecast if the late run starts **before midnight Peru**. After midnight the Peru date has changed, so the run targets the new day and the missed day has no forecast (see "Missed nights" below). |
| `-RunOnlyIfNetworkAvailable` | Does not start without a network connection, rather than failing on the download. |
| `-ExecutionTimeLimit 1h` | Kills a hung run. A normal run takes about 30–60 s. |
| `-LogonType S4U` | Runs whether or not you are logged on, without storing your password. S4U has no access to authenticated network shares; plain internet access to Yahoo works. |
| `-RunLevel Limited` | No elevated rights while running. |
| `-TaskPath "\PathIntegralQuench\"` | Puts the task in its own folder in Task Scheduler. `-TaskName` cannot contain a backslash; the folder goes in `-TaskPath`. |

The action needs no working directory: `run_daily.bat` changes to the project root itself.

**Missed nights.** If the PC is off from 22:00 until after midnight Peru, that day gets no
forecast, and the next run does not recover it. Recovering missed forecasts is not covered yet;
it is an open point for block 5 (backfill).

### 5.4 Check the task

```powershell
Get-ScheduledTask -TaskPath "\PathIntegralQuench\" -TaskName "RunDaily" | Format-List *
Get-ScheduledTaskInfo -TaskPath "\PathIntegralQuench\" -TaskName "RunDaily"     # LastRunTime, LastTaskResult, NextRunTime
Start-ScheduledTask -TaskPath "\PathIntegralQuench\" -TaskName "RunDaily"       # run it now, as the scheduler would
```

`LastTaskResult` is the exit code of `run_daily.bat`:

| Code | Meaning |
|---|---|
| 0 | OK, already logged, or skipped (weekend, holiday) |
| 1 | A ticker failed, or an unexpected error |
| 2 | Fatal: venv missing, bad argument, schedule crosses midnight ET, tampered parameters |

`267009` (0x41301) means the task is still running.

To remove the task:

```powershell
Unregister-ScheduledTask -TaskPath "\PathIntegralQuench\" -TaskName "RunDaily" -Confirm:$false
```

### 5.5 Logs

| File | Content |
|---|---|
| `logs\run_daily_YYYY-MM-DD.log` | The orchestrator: gates, steps and exit code. It also includes, indented with `    \|`, every line printed by `fetch_today.py` and `shadow_predict.py`. |
| `logs\shadow_predict_YYYY-MM-DD.log` | The predictor's own structured log (§4a of `shadow_production.md`) |

The date in each file name is the Peru date (`run_daily`) or the ET date (`shadow_predict`). At
22:00 Peru they are the same day.

```powershell
Get-Content logs\run_daily_*.log -Tail 50
Get-Content ("logs\run_daily_{0:yyyy-MM-dd}.log" -f (Get-Date)) -Tail 50     # tonight's run
```

### 5.6 Holidays and weekends

- **The scheduler fires Monday to Friday at 22:00, holidays included.**
- **US holiday:** `fetch_today.py` finds no bars and exits 1, which is logged as a WARNING (D-028).
  The data gate then logs `No valid data for YYYY-MM-DD (missing: …). Holiday or market closed.
  Skipping model. Exit 0.` **This is correct behavior, not a failure** (D-027).
- **Weekend** (manual run or a misfire): `Weekend in Peru (Saturday). Exiting 0.`, before the
  venv is touched.
- **A Peru holiday that is not a US holiday:** the US market is open, so there is data and the
  model **runs**. No holiday calendar of either country is maintained; the data gate handles both.
- **One ticker missing:** if any ticker lacks valid data, the whole run is skipped with exit 0.
  This is a deliberate simplification: one rule for the whole run.
- **The same rule covers Yahoo outages.** A trading day on which Yahoo returns nothing is skipped
  like a holiday. The `fetch_today.py` WARNING lines in the log are how you tell the two apart.

### 5.7 Manual runs

From the project root, in PowerShell or `cmd`:

```powershell
scripts\run_daily.bat                         # full pipeline: fetch + predict (writes)
scripts\run_daily.bat --DryRun                # full pipeline, writes no predictions (today's CSV is still saved)
scripts\run_daily.bat --Force                 # bypass the data gate, for debugging (never the weekend gate)
scripts\run_daily.bat -Tickers NVDA           # one ticker (comma list for several: -Tickers NVDA,SPY)
```

Running the `.ps1` directly works too, but needs the execution-policy flag:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daily.ps1 -DryRun
```

Before 16:05 ET a manual run fetches the partial day and the predictor skips with "before the
16:05 ET trigger" (exit 0).

### 5.8 Testing without waiting for the scheduler

`-SimulateNowUtc` pretends the run happens at any instant, given in ISO-8601 with an offset. It
always implies `-DryRun`. It skips the download, so archived CSVs are never re-downloaded. It
passes the same instant to `shadow_predict.py --timestamp`. Logs go to
`logs\run_daily_<date>_simulated.log`.

```powershell
# US daylight time, 22:00 Peru = 23:00 ET (same day, passes the guard)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daily.ps1 -SimulateNowUtc "2027-07-12T22:00:00-05:00" -DryRun
# A day with archived data: Monday 16-Nov-2026 at 22:00 Peru
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daily.ps1 -SimulateNowUtc "2026-11-16T22:00:00-05:00"
# The rejected 23:00 in US daylight time: the guard fires, exit 2
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daily.ps1 -SimulateNowUtc "2027-07-12T23:00:00-05:00"
```

### 5.9 Log rotation

- A real run writes about 10–15 KB per day (2 tickers, embedded Python output included).
- **`run_daily.ps1` never deletes logs.** Clean up by hand about once a quarter, for example:

  ```powershell
  Get-ChildItem logs\*.log | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } | Remove-Item
  ```

- `*_simulated.log` files are test artifacts; delete them whenever you like. Logs are not
  evidence: the records in `data/` are.

### 5.10 What `run_daily.bat` does not do yet

- It does not run `shadow_evaluate.py` (block 3, pending), so forecasts are logged but not scored.
- It does not run `shadow_report.py` (block 4, pending).
- It does not run `aggregate_metrics.py`. `shadow_evaluate.py` will rebuild the metrics itself
  (D-022).

When block 3 exists, `run_daily.ps1` will call `shadow_evaluate.py` after Step 2 through the same
`Invoke-Python` helper, so its output also lands in `run_daily_*.log`.
