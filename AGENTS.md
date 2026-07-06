# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Running in development

```bash
# Run Watchdog (main loop) — from the project root
venv\Scripts\python src\main.py

# Run Boot sequence (one-shot bootstrap: update check → MemReduct → RDP launch → exit)
venv\Scripts\python src\boot_main.py

# Run WindowChecker (continuous RDP-window black-screen / hung recovery loop)
venv\Scripts\python src\window_checker_main.py
```

## Dependencies

```bash
pip install -r requirements.txt
# Key deps: pywin32, mss, numpy, opencv-python, pytesseract, pyautogui, pyyaml, psutil, pywinauto
```

## Building executables

Releases are **automated by CI** — see [Releases (CI-on-push)](#releases-ci-on-push).
Manual builds are for local testing only:

```bash
pyinstaller Watchdog.spec
# Output: dist\Watchdog.exe

# Or build directly from the entry points (onefile, console) — the SAME five
# the CI workflow builds:
pyinstaller --noconfirm --clean --onefile --name Watchdog        .\src\watchdog.py
pyinstaller --noconfirm --clean --onefile --name Boot            .\src\boot_main.py
pyinstaller --noconfirm --clean --onefile --name WindowChecker   .\src\window_checker_main.py
pyinstaller --noconfirm --clean --onefile --name DropStats       .\src\drop_stats_main.py
pyinstaller --noconfirm --clean --onefile --name MemReductLooped .\src\memreduct_looped.py
pyinstaller --noconfirm --clean --onefile --name FarmAgent       .\src\farm_agent_main.py
```

The deploy layout is **one folder per exe** under `WatchdogDeploy\`: `Watchdog\`, `Boot\`, `WindowChecker\`, `DropStats\`, `MemReductLooped\`, `FarmAgent\` — each with its own `config\` holding only the yamls that exe reads. Copy each built exe into its folder alongside `config\` (and `third_party\` for Watchdog, which needs Tesseract; the others don't). WindowChecker.exe runs from the **main user session** (`Documents\WindowChecker\`), like Boot.

## Releases (CI-on-push)

`.github/workflows/release.yml` publishes releases automatically. A push to
`main` touching `src/**`, `config/**`, `requirements.txt`, or the workflow
itself triggers a `windows-latest` job that:

1. Builds all **five** onefile console exes above.
2. Patch-bumps the latest release tag (`v1.0.21` → `v1.0.22`; `v1.0.0` if none
   exists yet) and fails cleanly if that tag already exists.
3. Writes release notes with **one SHA256 line per asset** in this exact form:

   ```
   SHA256 (Watchdog.exe): <64-hex>
   SHA256 (Boot.exe): <64-hex>
   SHA256 (WindowChecker.exe): <64-hex>
   SHA256 (DropStats.exe): <64-hex>
   SHA256 (MemReductLooped.exe): <64-hex>
   ```

   The `(` after `SHA256` is deliberate: it does **not** match the OLD deployed
   regex `SHA256:\s*([a-fA-F0-9]{64})`, so pre-existing exes skip verification
   instead of verifying the wrong hash. New exes parse the per-asset named form
   (`auto_updater.extract_sha256_from_release`), falling back to a single bare
   `SHA256: <hash>` line for old manual releases.
4. Attaches all five exes (basenames become the asset names — a **contract**
   the deployed updaters match by exact `executable_name`; never rename them)
   and appends the commit SHA + a `git log` changelog since the previous tag.

**No manual upload.** Just push. Bump `current_version` in each app's update
config only if you want to force-skip a version locally — CI always tags higher
than the previous release, so deployed apps update on the next poll.

**One-time bootstrap for DropStats / MemReductLooped.** Their *currently
deployed* copies predate the updater wiring, so they cannot self-update yet.
The **first** `DropStats.exe` and `MemReductLooped.exe` (built with an updater)
must be installed by hand into their deploy folders alongside their new
`config/drop_stats_update_config.yaml` / `config/memreduct_update_config.yaml`.
After that one manual install they self-update from every subsequent release
like the other three.

## Diagnostic / test scripts

Located in `Test files/` (not a pytest suite — standalone scripts run manually):

| Script | What it tests |
|---|---|
| `config_validator.py` | Validates YAML config files |
| `diagnose_ocr.py` | Tests Tesseract preprocessing & OCR output |
| `test_capture.py` | Tests screenshot capture functions |
| `test_ocr.py` | Tests OCR text extraction |
| `test_panel_finder.py` | Tests window-finding logic |
| `test_scroll.py` | Tests scroll-to-top functionality |
| `test_updater.py` | Tests auto-updater logic |

Run any of them with: `venv\Scripts\python "Test files\<script>.py"`

## Architecture

The project has four main independent executables (plus DropStats/MemReductLooped utilities):

**Watchdog** (`src/main.py` → `src/watchdog.py`)
- Finds a target panel window by title substring (`config/app.yaml` → `window.title_substring`)
- Captures a screen region containing the panel's log area (`config/regions.yaml` → `log_region_pct`, as percentages of window size)
- OCR-reads the log via Tesseract (`src/ocr.py`) to extract timestamped messages in `HH:MM | message` format
- If the latest message is older than `general_timeout_minutes`, clicks the recovery button (`regions.yaml` → `button_point_pct`)
- If the window is not found, launches `panel.exe` from `regions.yaml` → `panel.dir`
- CS2 instance check runs every 5 minutes: if count != 4, OCRs the full log box (`logbox_full_pct`) for a "Launching" message. If that message is < 10 min old, skips the fix (launch in progress). Otherwise kills all CS2 and re-runs first-run clicks
- Auto-update check runs **once per hour** (gated in `watchdog.py` by `last_update_check_ts`)
- Periodic explorer restart every 30 minutes

**Boot** (`src/boot_main.py`) — **one-shot bootstrapper**
- Runs from the **main user session** (not inside RDP) — one instance per PC
- Sequence then **exits**: checks for Boot.exe update → launches MemReduct → launches RDP client → done
- No longer runs a continuous loop. The RDP-window health loop moved to **WindowChecker** (below). Because Boot exits, the heartbeat health-checker must **not** monitor Boot.exe — it monitors WindowChecker instead.
- Boot self-updates only at startup (i.e. on next launch/reboot), since it no longer loops.
- Steps live in `src/steps/` (memreduct, rdp, windows_focuser, expressvpn)

**WindowChecker** (`src/window_checker_main.py`)
- Runs continuously from the **main user session** — one instance per PC, its own Task Scheduler task, its own deploy folder (`Documents\WindowChecker\`) with its own `regions.yaml` (the RDP coords/sizes + reconnect keys) and `windowchecker_update_config.yaml` (updater + `watchdog_tasks`)
- Owns the RDP session reconnect cycle (`src/steps/windows_focuser.py` → `cycle_or_recover_rdp_windows`): every `focus_interval_minutes` it disconnects+reconnects each configured session through the RDP Session Manager UI — no hung/black detection. A renderer whose window survives its disconnect is FROZEN: force-killed, reconnected, and that user's Watchdog restarted via Task Scheduler; the host RDP stack is relaunched only if no window exists and the host process is dead
- Self-update check every 2 min (`UPDATE_CHECK_INTERVAL=120`) + at startup, via `config/windowchecker_update_config.yaml`
- Writes heartbeat `windowchecker_heartbeat_<user>.txt`; health_check.bat restarts it on hang
- Does **not** need Tesseract/OpenCV/NumPy — pure Win32 + pyautogui clicks
- Recovery hardening (R1–R6): probes every hwnd with `winops.window_responsive` before touching it (a frozen renderer deadlocked the checker on 7/4 + 7/6), repositions with `SWP_ASYNCWINDOWPOS`, confirms a disconnect by **observed effect** (success dialog or old window destroyed — `recovery_rules.disconnect_confirmed`), waits for session teardown before reopening (`regions.yaml → rdp_windows.disconnect_settle_max_s` / `reopen_settle_s`), and only ever force-kills allowlisted images (`wfreerdp.exe`; ghost hwnds resolved so dwm.exe is never targeted). Single-instance mutex; `focus_interval_minutes` floored at 10.

**FarmAgent** (`src/farm_agent_main.py`) — **box supervisor + HTTP control plane**
- Runs from the **main user session**, own Task Scheduler tasks: `FarmAgent` (at logon) + `FarmAgentKeepAlive` (every 10 min, start-if-not-running)
- Stdlib-only (no pywin32/requests): 60s loop reads WindowChecker heartbeat age, process/renderer/watchdog counts (`tasklist`), then walks a persisted escalation ladder — restart WindowChecker task → run missing Watchdog tasks → **auto-reboot** (after 3 consecutive unhealthy loops, max 1 reboot / 2h)
- HTTP API for Sherlock Homeless (config `config/farm_agent_config.yaml`: bind/port/token; header `X-Farm-Token`): `GET /status` (JSON health snapshot + recent actions), `POST /action/restart-windowchecker`, `POST /action/restart-watchdog/<user>`, `POST /action/run-health-check`, `POST /action/reboot`. Sherlock treats a `/status` timeout as the alert — the agent needs no outbound connectivity
- Self-updates via `config/farm_agent_update_config.yaml` (asset `FarmAgent.exe`)

## Key files

| File | Purpose |
|---|---|
| `config/app.yaml` | Window title, layout size, timeout values, poll interval, debug flags |
| `config/regions.yaml` | Per-machine pixel regions (log box, button, panel path, exe paths) |
| `config/update_config.yaml` | Watchdog.exe auto-updater config (GitHub repo, version, PAT token) |
| `config/boot_update_config.yaml` | Boot.exe auto-updater config + `watchdog_tasks` mapping (title_contains → username → task_name) for restarting Watchdog after hung RDP recovery |
| `config/windowchecker_update_config.yaml` | WindowChecker.exe auto-updater config |
| `src/window_checker_main.py` | WindowChecker.exe entry point: continuous RDP-window health loop + self-update + heartbeat |
| `src/utils.py` | `setup_logger()`, `load_yaml()`, `exe_dir()`, `runtime_root()` |
| `src/winops.py` | Shared Win32 primitives: `find_window()`, `find_window_by_process()`, `wait_for_window()`, `force_foreground()`, `safe_click()`, `pct_to_screen_xy()`, `set_dpi_awareness()` |
| `src/vision.py` | Screenshot helpers: `capture_window_region_pct()`, `is_ui_loaded_basic()`, `wait_for_ui_loaded()` |
| `src/auto_updater.py` | GitHub releases API checker + downloader + batch-script applier (shared by Watchdog, Boot, and WindowChecker via different config paths) |
| `src/ocr.py` | Tesseract wrapper — sets `tesseract_cmd` to bundled `third_party/Tesseract-OCR/tesseract.exe` |
| `src/layout.py` | `normalize_window_bottom_right()` — repositions window to bottom-right of workarea |
| `src/window_connector.py` | `find_hwnd_by_title_substring()` (legacy; prefer `winops.find_window()`) |
| `src/memreduct_looped.py` | Standalone script: runs MemReduct memory cleanup on a 10-minute loop |
| `src/calibration.py` | Interactive tool to calibrate point/region coordinates and output YAML for `regions.yaml` |
| `src/steps/windows_focuser.py` | RDP session reconnect cycle + frozen recovery (`cycle_or_recover_rdp_windows`, `reconnect_stuck_session`, `restart_watchdog_for_titles`). Driven by **WindowChecker** (no longer Boot) |

## Path resolution pattern

Two helpers in `src/utils.py` handle dev vs frozen (PyInstaller) paths:

- `exe_dir()` — folder containing `Watchdog.exe` when frozen; project root when running as `.py`. Use this for **editable files** (configs, logs).
- `runtime_root()` — `sys._MEIPASS` when frozen; project root otherwise. Use this for **bundled assets**.

`load_yaml(rel_path)` tries `exe_dir()/rel_path` first, then falls back to `runtime_root()/rel_path`. It handles non-UTF-8 encoded files (cp1252/cp1251) gracefully — deployed configs may be saved with Windows-native encoding via Wordpad or similar editors.

## Auto-updater

`src/auto_updater.py` is shared by all five deployed apps. Each passes its own config path:
- Watchdog: `config/update_config.yaml` (executable_name: `Watchdog.exe`)
- Boot: `config/boot_update_config.yaml` (executable_name: `Boot.exe`)
- WindowChecker: `config/windowchecker_update_config.yaml` (executable_name: `WindowChecker.exe`)
- DropStats: `config/drop_stats_update_config.yaml` (executable_name: `DropStats.exe`) — one-shot weekly job, checks once at startup
- MemReductLooped: `config/memreduct_update_config.yaml` (executable_name: `MemReductLooped.exe`) — checks at startup + once per ~10-min loop

The updater verifies a downloaded asset against the per-asset `SHA256 (<AssetName>): <hash>` line in the release notes when present (see [Releases](#releases-ci-on-push)); if no hash is found it logs a warning and proceeds without verification.

The updater:
1. Reads config for repo/version/token/executable_name
2. Watchdog gates the call to once per hour via `last_update_check_ts` (in-memory). The updater also has a file-based guard (`.last_check` in `%TEMP%\watchdog_updates\`) as a secondary throttle
3. Downloads new exe to `%TEMP%\watchdog_updates\{ExeName}_{version}_{asset}` (retries up to 3 times with 10s/20s backoff). Watchdog's first check on startup is staggered by a random 0-120s delay so dual-session users don't download simultaneously
4. Updates `current_version` in the config file (via regex) **before** exiting, so the new exe doesn't re-trigger the update on startup
5. Writes and launches `apply_update.bat` which: kills the current user's exe instances (via `taskkill /FI "USERNAME eq %USERNAME%"`) → backs up old exe → moves new exe into place → starts new exe → rolls back on any error
6. Calls `sys.exit(0)` to release the exe lock so the batch script can replace it

**Multi-user safe:** Each user has their own `Documents\Watchdog\` folder with their own exe, config, and `.update_lock` file. The batch script filters `taskkill` and `tasklist` by `%USERNAME%` so each user's update only touches their own processes.

When running as `.py` (dev), `apply_update()` returns `False` immediately — the full flow only works on the compiled exe.

## Multi-user (dual-session) deployment

Each PC runs **two Windows user sessions** simultaneously (via RDP), each running its own Watchdog + 4 CS2 instances. Boot runs once from the **main user session** (not RDP). This means the OS task manager shows 8 cs2.exe and 2 Watchdog.exe processes total. Code that enumerates or kills processes **must filter by Windows session ID** (`ProcessIdToSessionId`) or **by USERNAME** (`taskkill /FI "USERNAME eq %USERNAME%"`) to only affect the current user's processes. Examples: `count_cs2_instances()` filters by session ID, `restart_explorer()` filters by USERNAME, and the auto-updater batch script filters both `taskkill` and `tasklist` by USERNAME.

## RDP session reconnect cycle (WindowChecker)

Owned by **WindowChecker** (`cycle_or_recover_rdp_windows` in `src/steps/windows_focuser.py`), run every `focus_interval_minutes`. There is **no hung/black detection** — every cycle, each configured session is disconnected and reconnected through the RDP Session Manager UI (a server-side disconnect heals a frozen renderer and refreshes a healthy one alike).

Per cycle:
1. For each configured session (`rdp.user1_title`/`user2_title` + `user1_point_pct`/`user2_point_pct` + `disconnect_point_pct` in regions.yaml): select its entry → click Disconnect → double-click to reopen (`reconnect_stuck_session`). Each click restores+focuses the manager first, recomputes coords from the live rect, and dismisses the "Success" dialog that follows.
2. Wait `reopen_wait_seconds`, then check the pre-cycle window handles: a healthy window was destroyed by the disconnect (reopens with a new handle) — a handle that **survived a confirmed disconnect** belonged to a FROZEN renderer. Force-kill it (`taskkill /F /T /PID`), clear WerFault dialogs. The confirmed-disconnect gate means a focus failure can never ghost-kill a healthy window.
3. For frozen kills only, restart that user's Watchdog: `taskkill /F /IM Watchdog.exe /FI "USERNAME eq {username}"` then `schtasks /Run /TN {task_name}`.
4. Any expected session still missing gets one more disconnect+reconnect attempt.
5. If **no** session window exists AND neither `wfreerdp.exe` nor `RDPClient.exe` is running, relaunch the host RDP stack via `steps.rdp.run`.

If the reconnect keys aren't configured, the cycle logs an error and only the reposition + host-relaunch safety net runs.

The RDP window title → username → task name mapping lives under `watchdog_tasks:` in `config/windowchecker_update_config.yaml` (moved from `boot_update_config.yaml`; `restart_watchdog_for_titles` reads WindowChecker's config first, falling back to Boot's for older deploys).

GDI note: the BitBlt capture path (`watchdog.capture_window_region_api`) releases its DC/bitmap handles in a `finally` block — required to avoid a slow GDI/RAM leak on the exception path over a long-running loop.

## DPI awareness

`set_dpi_awareness()` from `src/winops.py` **must be called before any Win32 coordinate work**. It is called at the very top of `watchdog.py` (before other imports) and must be similarly placed in any new script that does window positioning or click coordinate math. Skipping it causes incorrect pixel coordinates on high-DPI displays.

## Per-machine configuration

`config/regions.yaml` is machine-specific — it contains percentage-based coordinates and local filesystem paths (`panel.dir`, `paths.memreduct_exe`, etc.). Each deployed PC needs its own calibrated copy. Run `src/calibration.py` to recalibrate regions interactively.

Key regions:
- `log_region_pct` — top line of the log box (used by main watchdog loop for OCR)
- `logbox_full_pct` — full log box (used by CS2 instance checker to find "Launching" messages)
- `button_point_pct` — recovery button click target
- `kill_all_cs2_point_pct` — kill-all button for CS2 instance fix
- `panel.first_run.clicks` — sequence of percentage-based clicks performed once after `panel.exe` launches fresh

## Logging

Logs are written to `logs/` with timestamped filenames:
- Watchdog: `logs/watchdog_YYYYMMDD_HHMMSS.log`
- Boot: `logs/boot_YYYYMMDD_HHMMSS.log`
- RDP step: `logs/rdp_YYYYMMDD.log`
- Hung window diagnostics: `logs/hung_YYYYMMDD_HHMMSS.png`

Both file and console handlers are attached. Encoding is UTF-8.

## OCR timestamp parsing

`ENTRY_RE` in `watchdog.py` matches log entries in `HH:MM | message` format. The pipe separator (or OCR variants: `¦ ｜ 丨 I l`) is **required** — this prevents random `N:NN` patterns inside messages from being matched as timestamps.

`normalize_text_for_parsing()` corrects common Tesseract misreads before parsing: full-width pipes (｜→|), colons read as periods in timestamps (only when followed by a pipe), I/l as pipe after timestamps. Any new OCR-dependent parsing should go through this normalizer or extend it.

`minutes_since_hhmm()` compares a parsed `HH:MM` against `datetime.now()` (PC local time). Both the panel log and PC clock are in the same timezone on each machine.
