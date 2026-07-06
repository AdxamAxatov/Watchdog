# Watchdog — Wishlist

> **Capture inbox for future / nice-to-have / explicitly-deferred items.** Low ceremony.
> Promote an item into [ROADMAP.md](ROADMAP.md) when it gets scoped into a phase.
>
> Buckets: **Deferred by design** (consciously out of scope) and **Future enhancements**
> (planned-later upgrades). **New ideas** is the untriaged inbox.

---

## 🚫 Deferred by design (out of scope for now)

_(consciously NOT built — each is a separate track or a non-goal, not a gap)_

---

## ✨ Future enhancements (planned-later upgrades)

_(real upgrades to build once the current system is production-grade)_

- **[rdp] upgrade FreeRDP client (wfreerdp 3.17.2 → current)** — 6 WER-confirmed client hangs
  in 17 days on host-67; newer FreeRDP reduces the frozen-renderer trigger rate that Phase 1
  armor absorbs. Build when: after ROADMAP Phase 5 fleet rollout is stable.
- **[agent] breadcrumb ingestion into /status** — FarmAgent currently surfaces actions + ladder
  state; parsing `logs/recovery_state.json` into `/status` gives Sherlock per-session frozen
  history. Build when: Phase 6 Sherlock integration wants it.

---

## 🆕 New ideas (untriaged)

_(raw ideas land here; promote to ROADMAP.md once scoped into a phase)_

---

## 🔬 Deep review findings (2026-07-06) — RDP Session Manager hung-window recovery

> **→ PROMOTED to [ROADMAP.md](ROADMAP.md) (2026-07-07):** all confirmed bugs below → hotlist +
> **Phase 1** (WC hardening R1–R6); health_check zombie → **Phase 2** (FarmAgent); rollout →
> **Phase 5**. Kept below as history per wishlist convention. Spec:
> `docs/superpowers/specs/2026-07-07-farm-self-healing-design.md`.

Full review of the WindowChecker RDP recovery chain (`src/steps/windows_focuser.py` +
`src/window_checker_main.py` + `src/steps/rdp.py` + `src/winops.py`). Parked pending a
live SSH diagnosis of a currently-stuck PC (root-cause first, then fix). One fix lane:
`src/steps/windows_focuser.py` + a small `winops.py` responsiveness helper.

### Confirmed bugs

- 🐞 **[critical] hung RDP Session Manager ghost-kills healthy sessions, then deadlocks the farm** —
  "confirmed disconnect" only means clicks were *dispatched* (`src/steps/windows_focuser.py:357`
  ignores the Success-dialog result; `src/winops.py:245` `force_foreground` never checks
  responsiveness; no `IsHungAppWindow`/`SendMessageTimeout` probe anywhere). Hung manager →
  void clicks → healthy pre-cycle windows land in `closed_hwnds` → survive the wait → (b2)
  force-kills both healthy renderers (`windows_focuser.py:508`) → (f) never relaunches because
  hung RDPClient.exe counts as "alive" (`windows_focuser.py:571`) → permanent outage, clean logs.
  Fix: responsiveness probe + manager-hung recovery path (kill RDPClient.exe → `steps.rdp.run()`),
  and disconnect-confirmed = Success dialog observed OR old window destroyed. Effort: M.
- 🐞 **[high] frozen-window kill likely targets dwm.exe via the ghost window** —
  "(Not Responding)" titles come from the DWM ghost hwnd (code strips the suffix at
  `windows_focuser.py:181`); `_force_kill_window_process` taskkills the pid-from-hwnd
  (`windows_focuser.py:209-236`) which for a ghost is the compositor, while frozen wfreerdp
  survives. Fix: `HungWindowFromGhostWindow()` map-back or kill by image name `wfreerdp.exe`;
  never taskkill a pid whose image is dwm/explorer. Needs live-repro confirmation. Effort: S.
- 🐞 **[medium] single fixed 15s `reopen_wait_seconds` is the only frozen/healthy discriminator** —
  `windows_focuser.py:427`; slow server-side disconnect (>15s) = healthy window killed; session 2
  gets less settle time than session 1 (one shared wait). Fix: verify-then-escalate instead of
  one blind wait. Effort: S.
- 🐞 **[medium] failed re-open = silent 30-min outage** — re-open click result discarded
  (`windows_focuser.py:373`), (e2) retry result discarded (`:564`), no post-(e2) re-check, no
  escalation counter → N dead cycles never force a host restart. Effort: S-M.
- 🐞 **[medium] `restart_watchdog_for_titles` digit-collision** — plain substring match
  (`windows_focuser.py:97`): `title_contains: "SinFermera1"` also matches SinFermera16 →
  restarts the wrong user's Watchdog. `_title_matches_session` (`:188`) already solved this with
  boundary matching; reuse it. Effort: S.
- 🐞 **[low] `find_rdp_windows` trusts titles from any process + `[:2]` truncation** —
  no exe filter (`windows_focuser.py:30-46`), z-order-dependent slice (`:438`); a stray
  "SinFermera…" explorer window can displace a real session or get its process killed.
  Fix: filter by owning image name (wfreerdp.exe). Effort: S.

### Live-autopsy addendum (2026-07-06, host-67 via SSH) — CONFIRMED root cause

- 🐞 **[critical] reposition deadlocks on a hung window — THE observed root cause** —
  renderer wfreerdp spawned frozen at 11:47:24; at 11:47:48 WindowChecker's
  `reposition_rdp_windows_to_corners` called `ShowWindow`/`SetWindowPos` on it
  (`src/steps/windows_focuser.py:283-293`) — synchronous win32 into a frozen thread →
  WC wedged forever (log + heartbeat both stop at 11:47:48, task still "Running").
  Half the farm down 9+ h. Fix: hung-probe before touching any hwnd + `SWP_ASYNCWINDOWPOS`
  + never reposition a window that fails the responsiveness check. Effort: S. **Receipts:**
  WerFault `/p 21256` on the frozen renderer; newest WC log ends mid-cycle at the first
  reposition line; heartbeat file LastWriteTime == log end == 11:47:48.
- 🐞 **[high] health_check safety net silently dead** — `health_check.log` on host-67 last
  wrote 7/4 16:52 (5 min after boot) while the WC heartbeat sat stale for 9 h; the
  WatchdogHealthCheck task shows "Running" (zombie loop). Needs: heartbeat-of-the-health-check
  (Telegram/Viper ping on staleness) + investigate why the loop died. Effort: M.
- ~~🐞 **[critical] deployed exes ship with auto-updater DISABLED** — `requests` missing from
  `requirements.txt`, so PyInstaller builds exclude it; deployed WC logs "Missing dependencies
  (requests/yaml) - auto-updater disabled" → no exe built that way can ever self-update.~~
  → **fixed on PR #2** (`7d00b99`): requests added + CI import guard. Deployed boxes still
  need ONE manual exe swap to escape the trap.
- ℹ️ the "two WindowChecker processes" observation is benign — PyInstaller onefile
  bootloader+child pairs (same for RDPClient and Watchdog). Not a duplicate-instance war.
  A single-instance mutex is still cheap insurance. Effort: S.

### Follow-ups

- **[diagnosis first] SSH into the currently-stuck PC** before fixing — capture WHY the manager
  hangs (process states, ghost windows, WerFault, RDPClient stack) so the fix targets the root
  cause, not just the symptom. Owner: Sigma to provide SSH access.
- **[docs] stale comment rot** — `_force_kill_window_process` docstring still claims a WM_CLOSE
  was sent (`windows_focuser.py:209-214`); AGENTS.md says Watchdog update check is hourly while
  `watchdog.py` comments say 2 min — sync after the fix lands.
