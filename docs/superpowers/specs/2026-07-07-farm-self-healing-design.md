# Farm Self-Healing — Design Spec

**Date:** 2026-07-07 · **Owner:** Sigma · **Status:** approved (design), pre-implementation
**Repo:** AdxamAxatov/Watchdog · **Branch:** `feat/farm-self-healing` (stacked on `feat/auto-release-on-push`, PR #2)

## Problem (evidence-backed, host-67 live autopsy 2026-07-06)

The farm loses ~10-15h per incident, ~2 incidents/week, from one failure class:

1. wfreerdp (FreeRDP 3.17.2) occasionally spawns **frozen** when the reconnect cycle reopens a
   session ~1.5s after Disconnect, racing the server's session-teardown arbitration
   (6 WER-confirmed wfreerdp hangs in 17 days; `focus_interval_minutes: 1` rolled the dice
   ~600×/day per box).
2. WindowChecker then **deadlocks** calling synchronous `ShowWindow`/`SetWindowPos` on the frozen
   window inside `reposition_rdp_windows_to_corners` — no hung-window guard. Log + heartbeat
   freeze mid-cycle (observed twice: 7/4 02:13, 7/6 11:47).
3. The health_check .bat loop **zombies** minutes after boot (log stops, task shows "Running"),
   so nothing restarts the wedged checker.
4. Deployed exes were built without `requests` → **auto-updater disabled** → no fix can propagate
   without manual swaps (fixed in PR #2: requirements.txt + CI guard).

**Invariant to enforce:** no single frozen process can take the farm down for more than one
cycle, and no failure is ever silent.

## Non-goals

- Upgrading FreeRDP (parked on WISHLIST — reduces trigger rate, doesn't change the architecture).
- Sherlock-Homeless-side code (it only consumes the new HTTP API).
- Central dashboards / fleet aggregation.
- CS2/panel recovery logic (stays owned by Watchdog.exe, unchanged).

## Components

### 1. WindowChecker hardening (`src/steps/windows_focuser.py`, `src/winops.py`)

New primitive in `winops.py`:

```
window_responsive(hwnd, timeout_ms=2000) -> bool
    IsHungAppWindow(hwnd) OR SendMessageTimeout(hwnd, WM_NULL, SMTO_ABORTIFHUNG) failure -> False
resolve_real_hwnd(hwnd) -> hwnd
    Ghost window (class 'Ghost' / owned by dwm) -> HungWindowFromGhostWindow() map-back, else identity
process_image_of(hwnd) -> basename of owning exe
```

Changes in `windows_focuser.py`:

- **R1 — deadlock-proof reposition:** probe `window_responsive` before touching any hwnd; use
  `SWP_ASYNCWINDOWPOS | SWP_NOSIZE`; never `ShowWindow` an unresponsive window. A window failing
  the probe is recorded as frozen, not repositioned.
- **R2 — teardown-respecting reconnect:** after a *confirmed* Disconnect click, poll up to
  `disconnect_settle_max_s` (default 10) until the session's pre-cycle window is destroyed, then
  wait `reopen_settle_s` (default 3) before the reopen double-click. Timings configurable under
  `rdp_windows:` in regions.yaml.
- **R3 — effect-confirmed disconnect:** a session counts as cycled ONLY if the success dialog was
  observed (`close_confirmation_dialog` returned True) OR its old window was destroyed within the
  settle window. Click dispatch alone never marks a session cycled (kills the healthy-window
  ghost-kill class).
- **R4 — ghost-safe kill:** before `taskkill /PID` from an hwnd: `resolve_real_hwnd`, then verify
  `process_image_of` is `wfreerdp.exe` (configurable allowlist). Never kill a pid whose image is
  dwm.exe / explorer.exe. Prefer image-name+window match over raw pid where possible.
- **R5 — post-reopen probe:** after reopen, wait for the session window and require
  `window_responsive`. Frozen newborn → R4 kill → retry reconnect (max 2). Still broken →
  write a `recovery_state.json` breadcrumb (consumed by FarmAgent + /status) and continue the
  cycle for other sessions.
- **R6 — housekeeping:** single-instance mutex (named mutex, exit if held); code-side floor
  `focus_interval_minutes >= 10`; boundary-match fix in `restart_watchdog_for_titles` (reuse
  `_title_matches_session` semantics); `find_rdp_windows` filters by owning image
  `wfreerdp.exe`; drop the `[:2]` z-order truncation in favor of the exe filter.

### 2. FarmAgent.exe — box supervisor + control plane (NEW, `src/farm_agent_main.py`)

Separate stdlib-only process (http.server + threading; **no new pip deps**), own Task Scheduler
task (`FarmAgent`, at-logon, main session), own log + heartbeat.

**Supervision loop (every 60s, deterministic):**

| check | unhealthy when | action (bounded) |
|---|---|---|
| WC heartbeat file age | > 5 min | taskkill WindowChecker + `schtasks /Run WindowsChecker` |
| WC process count | 0 real instances | `schtasks /Run WindowsChecker` |
| session renderers | < expected for 10+ min | `schtasks /Run WindowsChecker` (WC owns reconnect) |
| watchdog per user | task not running | `schtasks /Run Watchdog<N>` |
| everything above failed | N=3 consecutive loops still unhealthy | **auto-reboot**, rate-limited: max 1 per 2h (state file), skipped if last reboot < 2h ago → alert-only mode |

All actions append to `logs/farmagent_actions.log` and surface in `/status`.

**HTTP API** (default `0.0.0.0:8765`, every request requires header `X-Farm-Token: <token>`
from `config/farm_agent_config.yaml`; non-matching → 401; token generated per-fleet, never
committed):

- `GET /status` → JSON: box name, heartbeat ages, WC/watchdog/renderer/cs2 counts per session,
  exe versions, ladder state, last 20 actions, uptime, last reboot.
- `POST /action/restart-windowchecker` · `POST /action/restart-watchdog/<user>` ·
  `POST /action/run-health-check` (immediate loop pass) · `POST /action/reboot` (honors the same
  rate limit unless `{"force": true}`).
- Design rule: **silence = unhealthy.** Sherlock treats a `/status` timeout as the alert; the
  agent never needs outbound connectivity.

**Agent self-supervision:** Task Scheduler repeating trigger (every 10 min, "start if not
running") relaunches a dead agent — stateless one-shot semantics, no loop to zombie. A wedged
agent = `/status` timeout = Sherlock alert.

### 3. Pipeline + fleet rollout

- CI builds `FarmAgent.exe` as the 6th release asset (same per-asset SHA256 contract); agent
  self-updates via `config/farm_agent_update_config.yaml` like every other exe.
- `scripts/bootstrap_box.sh` (runs from the Mac): given `user@host` + password file — stops
  tasks, backs up old exes, pushes new exes + config templates via scp/ssh, registers the
  `FarmAgent` task (schtasks XML), restarts everything, curls `/status` as the acceptance check.
- `farm_sshs` grows into the 12-box inventory consumed by the bootstrap loop (stays untracked /
  outside the repo — it holds credentials).

## Escalation ladder (end-to-end)

```
frozen renderer (probe fails)
  → WC: ghost-safe kill + reconnect (≤2 retries)          [in-cycle, seconds]
  → WC: breadcrumb recovery_state.json                    [hand-off]
  → Agent: restart WC task / restart RDP stack task       [≤3 loops, minutes]
  → Agent: auto-reboot (≤1 per 2h)                        [last rung, ~10 min]
  → Sherlock: /status timeout or unhealthy JSON → human   [always-on, parallel]
```

## Testing

- **Unit (mac, TDD):** R2/R3 state transitions, R4 image-allowlist decisions, ladder state
  machine, rate limiter, API auth + routing + JSON shape, config parsing. Win32 calls isolated
  behind injectable seams so logic tests run headless.
- **Live smoke (host-67, before fleet rollout):** one forced full cycle; one staged freeze drill
  (`Suspend-Process` on a renderer → expect R4/R5 recovery without WC deadlock); agent drill
  (suspend WC → expect agent restart within 2 loops); API drill via curl from the Mac.
- **Gate:** unit suite green + all yamls parse + workflow parses + smoke receipts before merge.

## Config summary (new/changed keys)

- `regions.yaml → rdp_windows:` `disconnect_settle_max_s: 10`, `reopen_settle_s: 3`
  (floor: `focus_interval_minutes >= 10`).
- `config/farm_agent_config.yaml`: port, token, expected_sessions (2), thresholds, reboot policy.
- `config/farm_agent_update_config.yaml`: standard updater block, `executable_name: FarmAgent.exe`.

## Risks / honest labels

- Win32 hung-window semantics have edge cases only observable live → the freeze drill on host-67
  is a hard gate, not optional.
- The reboot rung assumes Task Scheduler tasks are set to fire at boot/logon on every box —
  bootstrap script must verify (it does, via post-reboot `/status` check).
- CI workflow remains **Spec-only** until PR #2's first green run.
