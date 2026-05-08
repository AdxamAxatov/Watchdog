# Drop Stats Weekly Automation

Automates the click-sequence: **Drop Stats → pick week → Generate Report**, using mouse coordinates stored in `config.yaml`.

## Files

- `config.yaml` – mouse coordinates per step (starts empty; populated by `calibrate.py`).
- `calibrate.py` – capture utility. Hover the mouse over each target, press ENTER.
- `run_report.py` – the runner. Reads `config.yaml` and clicks through the steps.
- `run_weekly.bat` – wrapper for Windows Task Scheduler.
- `run_report.log` – created on first run; rolling log of every execution.

## One-time setup

1. Install Python 3.x and the two dependencies:

   ```
   pip install pyautogui pyyaml
   ```

2. Open the target application. Make sure window position / DPI is what it will be at run time (Task Scheduler runs in your normal session, so leaving the app pinned/maximized works best).

3. Calibrate:

   ```
   python calibrate.py
   ```

   For `week_option`, open the dropdown manually before pressing ENTER so the option is visible and stable.

4. Test:

   ```
   python run_report.py
   ```

5. Re-run `calibrate.py` any time the window moves, the layout changes, or you switch monitors / resolution.

## Schedule it (taskschd.msc)

1. `Win + R` → `taskschd.msc`.
2. **Create Task…** (not "Create Basic Task" – we need the extra options).
3. **General**:
   - Name: `Drop Stats Weekly Report`
   - Select **Run only when user is logged on** (mouse automation needs an interactive session).
   - Check **Run with highest privileges** if the target app needs it.
4. **Triggers** → New:
   - Begin: **On a schedule**, **Weekly**, pick day + time (e.g. Monday 09:00).
5. **Actions** → New:
   - Action: **Start a program**
   - Program/script: `C:\Users\user\Documents\drop-stats-automation\run_weekly.bat`
   - Start in: `C:\Users\user\Documents\drop-stats-automation`
6. **Conditions**:
   - Uncheck **Start the task only if the computer is on AC power** if it's a laptop you want to fire on battery too.
7. **Settings**:
   - Check **Run task as soon as possible after a scheduled start is missed**.
8. OK. Right-click the task → **Run** to verify.

## Notes / troubleshooting

- **Fail-safe:** slam the mouse into a screen corner during a run to abort.
- **Wrong clicks:** the most common cause is the target window having moved or a different DPI scale at run time. Re-run `calibrate.py` from the same logged-in session that Task Scheduler will use.
- **Logs:** check `run_report.log` next to the script.
- **Week selection:** `week_selection: previous` in `config.yaml` is informational – the click goes to whatever option is at `week_option` coordinates, so calibrate against the correct week (typically the previous one). If the dropdown reorders weekly, prefer keyboard navigation or anchor on a "Last week" item.
