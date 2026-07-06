# Farm Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a frozen renderer/checker a self-healed non-event: harden WindowChecker (R1-R6), add a FarmAgent supervisor with an HTTP control API, ship both via CI, roll out to 12 boxes.

**Architecture:** Pure decision logic lives in new win32-free modules (`recovery_rules.py`, `farm_agent_core.py`) tested on macOS; win32/side-effect code stays in `winops.py` / `windows_focuser.py` / `farm_agent_main.py` and is verified by a scripted live drill on host-67. FarmAgent is a separate stdlib-only process so the supervisor survives its patient.

**Tech Stack:** Python 3.11, pywin32 (WC only), stdlib `http.server` (agent), PyInstaller onefile via existing GitHub Actions workflow, bash+sshpass rollout script.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-07-farm-self-healing-design.md` — R-numbers reference it.
- FarmAgent: **stdlib only** — no pywin32, no requests; all OS actions via `subprocess` (`tasklist`, `schtasks`, `taskkill`, `shutdown`).
- Never kill a pid whose image is `dwm.exe`/`explorer.exe`; kill allowlist = `{wfreerdp.exe}`.
- `focus_interval_minutes` code floor: **10**. Reboot rate limit: **max 1 per 7200s**, after **3** consecutive unhealthy loops.
- Asset name contract: new release asset is exactly `FarmAgent.exe`.
- No secrets in git: `farm_agent_config.yaml` ships `token: ""`; real tokens per-box.
- Keep files ≤ ~500 lines; tests live in `Test files/` as standalone unittest scripts (`python3 "Test files/<f>.py"`), matching repo convention.
- Windows-only tests are impossible on this Mac — every task marked **[mac-TDD]** must be green locally; tasks marked **[live-smoke]** are verified by Task 10's drill.

---

### Task 1: `recovery_rules.py` — pure decision rules [mac-TDD]

**Files:**
- Create: `src/recovery_rules.py`
- Test: `Test files/test_recovery_rules.py`

**Interfaces:**
- Produces: `effective_focus_interval(value, default=30.0) -> float`, `disconnect_confirmed(dialog_seen: bool, old_window_destroyed: bool) -> bool`, `may_kill_process(image_name: str) -> bool`, constants `KILL_IMAGE_ALLOWLIST`, `NEVER_KILL_IMAGES`, `MIN_FOCUS_INTERVAL_MINUTES = 10.0`

- [ ] **Step 1: Write the failing test** — `Test files/test_recovery_rules.py`:

```python
"""Unit tests for src/recovery_rules.py (pure logic — runs anywhere)."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from recovery_rules import (effective_focus_interval, disconnect_confirmed,
                            may_kill_process, MIN_FOCUS_INTERVAL_MINUTES)

class TestFocusIntervalFloor(unittest.TestCase):
    def test_below_floor_clamped(self):
        self.assertEqual(effective_focus_interval(1), MIN_FOCUS_INTERVAL_MINUTES)
    def test_above_floor_kept(self):
        self.assertEqual(effective_focus_interval(30), 30.0)
    def test_garbage_falls_back_to_default(self):
        self.assertEqual(effective_focus_interval("bogus"), 30.0)
        self.assertEqual(effective_focus_interval(None), 30.0)

class TestDisconnectConfirmed(unittest.TestCase):
    def test_dialog_alone_confirms(self):
        self.assertTrue(disconnect_confirmed(True, False))
    def test_destroyed_window_alone_confirms(self):
        self.assertTrue(disconnect_confirmed(False, True))
    def test_click_dispatch_alone_never_confirms(self):
        self.assertFalse(disconnect_confirmed(False, False))

class TestKillAllowlist(unittest.TestCase):
    def test_renderer_killable(self):
        self.assertTrue(may_kill_process("wfreerdp.exe"))
        self.assertTrue(may_kill_process("WFreeRDP.EXE"))
    def test_dwm_never(self):
        self.assertFalse(may_kill_process("dwm.exe"))
    def test_explorer_never(self):
        self.assertFalse(may_kill_process("explorer.exe"))
    def test_unknown_and_empty_never(self):
        self.assertFalse(may_kill_process("cs2.exe"))
        self.assertFalse(may_kill_process(""))
        self.assertFalse(may_kill_process(None))

if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run to verify it fails** — `python3 "Test files/test_recovery_rules.py"` → FAIL: `ModuleNotFoundError: recovery_rules`
- [ ] **Step 3: Implement** — `src/recovery_rules.py`:

```python
"""Pure decision rules for RDP recovery. NO win32 imports — unit-testable anywhere.

These rules exist because the 7/4 + 7/6 host-67 outages traced to (a) killing
by hwnd-derived pid without checking the owning image (ghost windows belong to
dwm.exe) and (b) treating click-dispatch as proof of a disconnect."""

MIN_FOCUS_INTERVAL_MINUTES = 10.0
KILL_IMAGE_ALLOWLIST = frozenset({"wfreerdp.exe"})
NEVER_KILL_IMAGES = frozenset({"dwm.exe", "explorer.exe"})


def effective_focus_interval(value, default=30.0):
    """Clamp the cycle interval to the floor; garbage -> default."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(v, MIN_FOCUS_INTERVAL_MINUTES)


def disconnect_confirmed(dialog_seen, old_window_destroyed):
    """A session counts as disconnected ONLY on observed effect (R3)."""
    return bool(dialog_seen) or bool(old_window_destroyed)


def may_kill_process(image_name, allowlist=KILL_IMAGE_ALLOWLIST):
    """True only for explicitly allowlisted images; system processes never (R4)."""
    img = (image_name or "").strip().lower()
    if not img or img in NEVER_KILL_IMAGES:
        return False
    return img in allowlist
```

- [ ] **Step 4: Run to verify pass** — `python3 "Test files/test_recovery_rules.py"` → `OK` (10 tests)
- [ ] **Step 5: Commit** — `git add src/recovery_rules.py "Test files/test_recovery_rules.py" && git commit -m "feat(recovery): pure decision rules — interval floor, effect-confirmed disconnect, kill allowlist"`

---

### Task 2: `winops.py` hung-window probes [live-smoke]

**Files:**
- Modify: `src/winops.py` (append after `pct_to_screen_xy`, ~line 344)

**Interfaces:**
- Produces: `window_responsive(hwnd, timeout_ms=2000) -> bool`, `resolve_real_hwnd(hwnd) -> int`, `process_image_of(hwnd) -> str` (lowercase basename or `""`)

- [ ] **Step 1: Implement** — append to `src/winops.py`:

```python
# ---- hung-window primitives (farm self-healing R1/R4) ----------------------

SMTO_ABORTIFHUNG = 0x0002


def window_responsive(hwnd: int, timeout_ms: int = 2000) -> bool:
    """True if the window's thread is pumping messages.

    Fail-open: a probe *error* reports responsive (all mutating callers use
    async flags, so a wrong 'responsive' cannot re-create the SetWindowPos
    deadlock; a wrong 'hung' could kill a healthy session)."""
    try:
        if ctypes.windll.user32.IsHungAppWindow(hwnd):
            return False
        res = ctypes.windll.user32.SendMessageTimeoutW(
            hwnd, 0x0000, 0, 0, SMTO_ABORTIFHUNG, timeout_ms,
            ctypes.byref(ctypes.c_ulong()))
        return bool(res)
    except Exception:
        return True


def resolve_real_hwnd(hwnd: int) -> int:
    """Map a DWM ghost window back to the real hung window; identity otherwise.

    Ghost windows (class 'Ghost') are owned by dwm.exe — acting on the ghost's
    pid would target the compositor (observed root-risk on host-67)."""
    try:
        if win32gui.GetClassName(hwnd) == "Ghost":
            real = ctypes.windll.user32.HungWindowFromGhostWindow(hwnd)
            if real:
                return int(real)
    except Exception:
        pass
    return hwnd


def process_image_of(hwnd: int) -> str:
    """Lowercase exe basename owning hwnd, or '' on failure."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return os.path.basename(_query_full_process_image_name(pid)).lower()
    except Exception:
        return ""
```

- [ ] **Step 2: Sanity-parse** — `python3 -m py_compile src/winops.py` → exit 0 (imports won't run on mac; compile check only)
- [ ] **Step 3: Commit** — `git add src/winops.py && git commit -m "feat(winops): window_responsive/resolve_real_hwnd/process_image_of probes"`

---

### Task 3: WindowChecker R1 + R6 — deadlock-proof reposition, exe-filtered enumeration, mutex, floor, boundary match [live-smoke]

**Files:**
- Modify: `src/steps/windows_focuser.py` (`find_rdp_windows` ~:30, `restart_watchdog_for_titles` ~:97, `reposition_rdp_windows_to_corners` ~:279-299)
- Modify: `src/window_checker_main.py` (imports ~:40, `main()` ~:95)

**Interfaces:**
- Consumes: Task 1 `effective_focus_interval`; Task 2 probes.
- Produces: `find_rdp_windows` now returns only wfreerdp-owned, ghost-resolved windows (no `[:2]` anywhere downstream).

- [ ] **Step 1: `find_rdp_windows` exe filter + ghost resolve** — replace the function body:

```python
def find_rdp_windows(title_substring="SinFermera"):
    """All wfreerdp-owned windows matching the title. Ghost hwnds are resolved
    to the real (hung) window; non-renderer processes (explorer folders named
    'SinFermera', etc.) are excluded so they can never be killed/repositioned."""
    from winops import resolve_real_hwnd, process_image_of
    windows = []

    def enum_callback(hwnd, results):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not (title and title_substring.lower() in title.lower()):
            return
        real = resolve_real_hwnd(hwnd)
        if process_image_of(real) != "wfreerdp.exe":
            return
        results.append((real, title))

    win32gui.EnumWindows(enum_callback, windows)
    return windows
```

- [ ] **Step 2: drop the z-order truncation** — in `cycle_or_recover_rdp_windows` change `windows = find_rdp_windows(title_search)[:2]  # only the 2 sessions` → `windows = find_rdp_windows(title_search)`
- [ ] **Step 3: boundary-match fix** — in `restart_watchdog_for_titles` replace `matched = any(title_match.lower() in ct.lower() for ct in closed_titles)` with `matched = any(_title_matches_session(ct, title_match) for ct in closed_titles)` (note: `_title_matches_session` is defined below it in the file — move the `_base_title`/`_title_matches_session` definitions ABOVE `restart_watchdog_for_titles`).
- [ ] **Step 4: deadlock-proof reposition (R1)** — inside `reposition_rdp_windows_to_corners`'s loop, replace the `try:` body:

```python
        try:
            if not win32gui.IsWindow(hwnd):
                continue
            from winops import window_responsive
            if not window_responsive(hwnd):
                if log:
                    log.warning("Reposition: %s NOT RESPONDING — skipping (frozen renderer)", title)
                print(f"   !! Skipping frozen window: {title}")
                continue
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if corner == "top-left":
                x, y = work_left, work_top
            else:
                x, y = work_right - w, work_bottom - h
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOP, x, y, 0, 0,
                win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW | win32con.SWP_ASYNCWINDOWPOS,
            )
            if log:
                log.info("Reposition: %s -> %s at (%d, %d)", title, corner, x, y)
            print(f"   -> Repositioned {title} to {corner}")
        except Exception as e:
            if log:
                log.warning("Reposition failed for %s: %s", title, e)
```

- [ ] **Step 5: single-instance mutex + interval floor** — `src/window_checker_main.py`: after the imports block add:

```python
def acquire_single_instance_or_exit(log=None) -> None:
    """Named mutex — a second WindowChecker exits immediately (R6)."""
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\WindowCheckerSingleton")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        msg = "Another WindowChecker instance is already running — exiting."
        (log.error if log else print)(msg)
        raise SystemExit(0)
```

In `main()` call `acquire_single_instance_or_exit(log)` right after `log = setup_logger()`, and replace `focus_interval_minutes = rdp_cfg.get("focus_interval_minutes", 30)` with:

```python
    from recovery_rules import effective_focus_interval
    focus_interval_minutes = effective_focus_interval(rdp_cfg.get("focus_interval_minutes", 30))
```

- [ ] **Step 6: compile + existing suite** — `python3 -m py_compile src/steps/windows_focuser.py src/window_checker_main.py && python3 "Test files/test_recovery_rules.py"` → OK
- [ ] **Step 7: Commit** — `git commit -am "feat(wc): R1+R6 — async guarded reposition, exe-filtered enum, mutex, interval floor, boundary match"`

---

### Task 4: WindowChecker R2/R3/R4/R5 — effect-confirmed teardown-respecting reconnect + ghost-safe kill + post-reopen probe [live-smoke]

**Files:**
- Modify: `src/steps/windows_focuser.py` (`reconnect_stuck_session` ~:302-374, `_force_kill_window_process` ~:209-236, sections (a)/(b2)/(e2) of `cycle_or_recover_rdp_windows`)

**Interfaces:**
- Consumes: Task 1 `disconnect_confirmed`/`may_kill_process`; Task 2 probes.
- Produces: `reconnect_stuck_session(entry_pct, disconnect_pct, old_hwnd=None, log=None, settle_max_s=10.0, reopen_settle_s=3.0) -> dict` with keys `ran, dialog_seen, old_destroyed` — callers gate on `disconnect_confirmed(r["dialog_seen"], r["old_destroyed"])`. `write_recovery_breadcrumb(session, state)` appends JSON lines to `logs/recovery_state.json`.

- [ ] **Step 1: ghost-safe `_force_kill_window_process` (R4)** — replace the function:

```python
def _force_kill_window_process(hwnd, title, log=None):
    """Force-kill the FROZEN renderer owning `hwnd` — only if the owning image
    is allowlisted (wfreerdp.exe). Ghost hwnds are resolved first so we never
    target dwm.exe (R4). Returns True if the kill command ran."""
    from winops import resolve_real_hwnd, process_image_of
    from recovery_rules import may_kill_process
    real = resolve_real_hwnd(hwnd)
    image = process_image_of(real)
    if not may_kill_process(image):
        if log:
            log.error("Force-kill REFUSED for %s — owning image %r not allowlisted", title, image)
        return False
    try:
        _, pid = win32process.GetWindowThreadProcessId(real)
    except Exception as e:
        if log:
            log.error("Force-kill: could not get PID for %s: %s", title, e)
        return False
    if not pid:
        return False
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=15)
        if log:
            log.warning("Force-killed FROZEN %s (image=%s pid=%d)", title, image, pid)
        print(f"   !! Force-killed FROZEN window: {title} (pid={pid})")
        return True
    except Exception as e:
        if log:
            log.error("Force-kill taskkill failed for %s (pid=%d): %s", title, pid, e)
        return False
```

- [ ] **Step 2: rework `reconnect_stuck_session` (R2+R3)** — keep `_click_then_confirm` but make it return `(clicked: bool, dialog_seen: bool)` (capture `close_confirmation_dialog`'s return instead of discarding); new tail:

```python
    # returns dict — see Interfaces. Sequence: select -> Disconnect ->
    # WAIT for teardown (old window destroyed, poll <= settle_max_s) ->
    # settle reopen_settle_s -> reopen double-click.
    result = {"ran": False, "dialog_seen": False, "old_destroyed": False}
    ok, _ = _click_then_confirm("select entry", safe_click, entry_pct)
    if not ok:
        return result
    ok, dlg = _click_then_confirm("click Disconnect", safe_click, disconnect_pct)
    if not ok:
        return result
    result["ran"] = True
    result["dialog_seen"] = bool(dlg)
    if old_hwnd:
        deadline = time.time() + float(settle_max_s)
        while time.time() < deadline:
            if not win32gui.IsWindow(old_hwnd):
                result["old_destroyed"] = True
                break
            time.sleep(0.5)
    time.sleep(float(reopen_settle_s))   # let server-side arbitration finish (R2)
    _click_then_confirm("re-open entry (double-click)", safe_double_click, entry_pct)
    return result
```

- [ ] **Step 3: caller (a) uses effect-confirmation** — in `cycle_or_recover_rdp_windows`, pass each session's pre-cycle hwnd (`old_hwnd = next((h for h, t in windows if _title_matches_session(t, sess_title)), None)`), read timings from `rdp_cfg` (`disconnect_settle_max_s`, default 10; `reopen_settle_s`, default 3), and build `cycled_titles` via:

```python
                r = reconnect_stuck_session(entry_pct, disconnect_pct, old_hwnd=old_hwnd,
                                            log=log, settle_max_s=settle_max, reopen_settle_s=reopen_settle)
                from recovery_rules import disconnect_confirmed
                if disconnect_confirmed(r["dialog_seen"], r["old_destroyed"]):
                    cycled_titles.append(sess_title)
                    closed_count += 1
                elif r["ran"]:
                    if log:
                        log.warning("Disconnect DISPATCHED but UNCONFIRMED for %r — "
                                    "not eligible for frozen-kill (manager hung?)", sess_title)
```

- [ ] **Step 4: post-reopen probe + breadcrumb (R5)** — after the (b2) kill block and re-enumeration, add:

```python
    # (b3) R5: a window that reopened FROZEN gets one kill+reconnect retry,
    # then a breadcrumb for FarmAgent/Sherlock.
    from winops import window_responsive
    for hwnd, title in list(reappeared):
        if window_responsive(hwnd):
            continue
        if log:
            log.warning("Post-reopen: %s NOT RESPONDING — kill + retry", title)
        if _force_kill_window_process(hwnd, title, log=log):
            base = _base_title(title)
            sess = next(((st, ep) for st, ep in configured_sessions
                         if _title_matches_session(title, st)), None)
            if sess and disconnect_pct:
                try:
                    reconnect_stuck_session(sess[1], disconnect_pct, log=log,
                                            settle_max_s=settle_max, reopen_settle_s=reopen_settle)
                except Exception:
                    log and log.exception("R5 retry failed for %r", base)
            write_recovery_breadcrumb(base, "frozen-after-reopen")
    reappeared = find_rdp_windows(title_search)
```

And the helper (module level):

```python
def write_recovery_breadcrumb(session, state):
    """Append a JSON line to logs/recovery_state.json for FarmAgent /status."""
    import json
    try:
        path = os.path.join(exe_dir(), "logs", "recovery_state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "session": session, "state": state}) + "\n")
    except Exception:
        pass
```

- [ ] **Step 5: (e2) retry also passes old_hwnd + timings** (same call shape as Step 3; result may be ignored there — it's already the retry rung).
- [ ] **Step 6: compile + suites** — `python3 -m py_compile src/steps/windows_focuser.py && python3 "Test files/test_recovery_rules.py" && python3 "Test files/test_updater.py"` → OK
- [ ] **Step 7: Commit** — `git commit -am "feat(wc): R2-R5 — teardown-respecting effect-confirmed reconnect, ghost-safe kill, post-reopen probe + breadcrumbs"`

---

### Task 5: `farm_agent_core.py` — health evaluation + escalation ladder [mac-TDD]

**Files:**
- Create: `src/farm_agent_core.py`
- Test: `Test files/test_farm_agent_core.py`

**Interfaces:**
- Produces: `evaluate_health(snapshot: dict, thresholds: dict) -> list[dict]` (each `{"check","healthy","detail"}`); `EscalationLadder(state_path, clock=time.time, unhealthy_loops_before_reboot=3, reboot_min_interval_s=7200)` with `.next_actions(checks) -> list[str]` returning action names from `{"restart_windowchecker","run_watchdog_task:<user>","reboot"}` and persisting `{"consecutive_unhealthy": int, "last_reboot_ts": float}` as JSON at `state_path`.
- Snapshot keys (produced by Task 7): `wc_heartbeat_age_s: float|None`, `wc_running: bool`, `renderer_count: int`, `expected_sessions: int`, `missing_watchdog_users: list[str]`.

- [ ] **Step 1: Write the failing test** — `Test files/test_farm_agent_core.py`:

```python
import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from farm_agent_core import evaluate_health, EscalationLadder

THRESH = {"wc_heartbeat_max_age_s": 300}
HEALTHY = {"wc_heartbeat_age_s": 30.0, "wc_running": True,
           "renderer_count": 2, "expected_sessions": 2, "missing_watchdog_users": []}

def sick(**over):
    d = dict(HEALTHY); d.update(over); return d

class TestEvaluateHealth(unittest.TestCase):
    def test_all_healthy(self):
        self.assertTrue(all(c["healthy"] for c in evaluate_health(HEALTHY, THRESH)))
    def test_stale_heartbeat_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(wc_heartbeat_age_s=999), THRESH)}
        self.assertFalse(checks["wc_heartbeat"]["healthy"])
    def test_missing_heartbeat_file_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(wc_heartbeat_age_s=None), THRESH)}
        self.assertFalse(checks["wc_heartbeat"]["healthy"])
    def test_missing_renderers_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(renderer_count=1), THRESH)}
        self.assertFalse(checks["renderers"]["healthy"])
    def test_missing_watchdog_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(missing_watchdog_users=["SinFermera11"]), THRESH)}
        self.assertFalse(checks["watchdogs"]["healthy"])

class FakeClock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t

class TestLadder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = str(Path(self.dir) / "ladder.json")
        self.clock = FakeClock()
        self.ladder = EscalationLadder(self.state, clock=self.clock)
    def unhealthy(self):
        return evaluate_health(sick(wc_heartbeat_age_s=999), THRESH)
    def test_healthy_resets_and_no_actions(self):
        self.assertEqual(self.ladder.next_actions(evaluate_health(HEALTHY, THRESH)), [])
    def test_unhealthy_restarts_wc_not_reboot(self):
        acts = self.ladder.next_actions(self.unhealthy())
        self.assertIn("restart_windowchecker", acts)
        self.assertNotIn("reboot", acts)
    def test_reboot_after_three_consecutive(self):
        for _ in range(2):
            self.assertNotIn("reboot", self.ladder.next_actions(self.unhealthy()))
        self.assertIn("reboot", self.ladder.next_actions(self.unhealthy()))
    def test_reboot_rate_limited(self):
        for _ in range(3): self.ladder.next_actions(self.unhealthy())
        # 3 more unhealthy loops right after: counter is there but rate limit blocks
        for _ in range(3):
            self.assertNotIn("reboot", self.ladder.next_actions(self.unhealthy()))
        self.clock.t += 7201
        for _ in range(3): acts = self.ladder.next_actions(self.unhealthy())
        self.assertIn("reboot", acts)
    def test_missing_watchdog_action_is_targeted(self):
        checks = evaluate_health(sick(missing_watchdog_users=["SinFermera11"]), THRESH)
        self.assertIn("run_watchdog_task:SinFermera11", self.ladder.next_actions(checks))
    def test_state_persists(self):
        for _ in range(2): self.ladder.next_actions(self.unhealthy())
        reloaded = EscalationLadder(self.state, clock=self.clock)
        self.assertIn("reboot", reloaded.next_actions(self.unhealthy()))

if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run to verify FAIL** (`ModuleNotFoundError: farm_agent_core`)
- [ ] **Step 3: Implement** — `src/farm_agent_core.py`:

```python
"""FarmAgent pure logic: health evaluation + escalation ladder. STDLIB ONLY."""
import json
import os
import time


def evaluate_health(snapshot, thresholds):
    max_age = float(thresholds.get("wc_heartbeat_max_age_s", 300))
    checks = []
    age = snapshot.get("wc_heartbeat_age_s")
    checks.append({"check": "wc_heartbeat",
                   "healthy": age is not None and age <= max_age,
                   "detail": f"age={age}"})
    checks.append({"check": "wc_process",
                   "healthy": bool(snapshot.get("wc_running")),
                   "detail": f"running={snapshot.get('wc_running')}"})
    rc, exp = snapshot.get("renderer_count", 0), snapshot.get("expected_sessions", 2)
    checks.append({"check": "renderers", "healthy": rc >= exp,
                   "detail": f"{rc}/{exp}"})
    missing = list(snapshot.get("missing_watchdog_users") or [])
    checks.append({"check": "watchdogs", "healthy": not missing,
                   "detail": ",".join(missing) or "all running",
                   "missing_users": missing})
    return checks


class EscalationLadder:
    """Deterministic, persisted. Rung 1: targeted restarts every unhealthy
    loop. Rung 2: reboot after N consecutive unhealthy loops, rate-limited."""

    def __init__(self, state_path, clock=time.time,
                 unhealthy_loops_before_reboot=3, reboot_min_interval_s=7200):
        self.state_path = state_path
        self.clock = clock
        self.n_before_reboot = int(unhealthy_loops_before_reboot)
        self.reboot_min_interval_s = float(reboot_min_interval_s)
        self.state = self._load()

    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"consecutive_unhealthy": 0, "last_reboot_ts": 0.0}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
        except Exception:
            pass

    def next_actions(self, checks):
        by = {c["check"]: c for c in checks}
        unhealthy = [c for c in checks if not c["healthy"]]
        actions = []
        if not unhealthy:
            self.state["consecutive_unhealthy"] = 0
            self._save()
            return actions
        self.state["consecutive_unhealthy"] = int(self.state.get("consecutive_unhealthy", 0)) + 1

        if not by["wc_heartbeat"]["healthy"] or not by["wc_process"]["healthy"] \
                or not by["renderers"]["healthy"]:
            actions.append("restart_windowchecker")
        for user in by["watchdogs"].get("missing_users", []):
            actions.append(f"run_watchdog_task:{user}")

        if self.state["consecutive_unhealthy"] >= self.n_before_reboot:
            since = self.clock() - float(self.state.get("last_reboot_ts", 0.0))
            if since >= self.reboot_min_interval_s:
                actions.append("reboot")
                self.state["last_reboot_ts"] = self.clock()
                self.state["consecutive_unhealthy"] = 0
        self._save()
        return actions
```

- [ ] **Step 4: Run to verify PASS** — `python3 "Test files/test_farm_agent_core.py"` → OK (11 tests)
- [ ] **Step 5: Commit** — `git commit -am "feat(agent): health evaluation + persisted rate-limited escalation ladder"`

---

### Task 6: FarmAgent HTTP API [mac-TDD]

**Files:**
- Modify: `src/farm_agent_core.py` (append)
- Test: append class to `Test files/test_farm_agent_core.py`

**Interfaces:**
- Produces: `make_api_server(host, port, token, status_provider, action_executor) -> ThreadingHTTPServer`. `status_provider() -> dict`; `action_executor(name: str, arg: str|None) -> dict`. Routes: `GET /status`; `POST /action/restart-windowchecker`, `POST /action/restart-watchdog/<user>`, `POST /action/run-health-check`, `POST /action/reboot` (body `{"force": true}` honored, passed through as arg). Auth: header `X-Farm-Token`; 401 on mismatch; 404 unknown route. All responses JSON.

- [ ] **Step 1: failing tests** (append):

```python
import http.client, threading
from farm_agent_core import make_api_server

class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls.srv = make_api_server(
            "127.0.0.1", 0, "sekret",
            status_provider=lambda: {"box": "test", "ok": True},
            action_executor=lambda name, arg=None: (cls.calls.append((name, arg)) or {"ran": name}))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
    def _req(self, method, path, token="sekret", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-Farm-Token": token} if token else {}
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    def test_status_ok(self):
        status, body = self._req("GET", "/status")
        self.assertEqual(status, 200); self.assertEqual(body["box"], "test")
    def test_bad_token_401(self):
        self.assertEqual(self._req("GET", "/status", token="wrong")[0], 401)
        self.assertEqual(self._req("GET", "/status", token=None)[0], 401)
    def test_action_dispatch(self):
        status, body = self._req("POST", "/action/restart-windowchecker")
        self.assertEqual(status, 200); self.assertEqual(body["ran"], "restart_windowchecker")
    def test_watchdog_action_carries_user(self):
        self._req("POST", "/action/restart-watchdog/SinFermera11")
        self.assertIn(("run_watchdog_task", "SinFermera11"), self.calls)
    def test_unknown_404(self):
        self.assertEqual(self._req("POST", "/action/nuke")[0], 404)
        self.assertEqual(self._req("GET", "/nope")[0], 404)
```

- [ ] **Step 2: verify FAIL** (`ImportError: make_api_server`)
- [ ] **Step 3: implement** (append to `farm_agent_core.py`):

```python
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ACTION_ROUTES = {
    "restart-windowchecker": ("restart_windowchecker", False),
    "run-health-check": ("run_health_check", False),
    "reboot": ("reboot", False),
    "restart-watchdog": ("run_watchdog_task", True),   # True -> takes <user> path arg
}


def make_api_server(host, port, token, status_provider, action_executor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet — agent has its own log
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            if self.headers.get("X-Farm-Token") != token:
                self._send(401, {"error": "bad token"})
                return False
            return True

        def do_GET(self):
            if not self._authed():
                return
            if self.path.rstrip("/") == "/status":
                try:
                    self._send(200, status_provider())
                except Exception as e:
                    self._send(500, {"error": str(e)})
            else:
                self._send(404, {"error": "unknown route"})

        def do_POST(self):
            if not self._authed():
                return
            parts = [p for p in self.path.split("/") if p]
            if len(parts) >= 2 and parts[0] == "action" and parts[1] in _ACTION_ROUTES:
                name, takes_arg = _ACTION_ROUTES[parts[1]]
                arg = parts[2] if (takes_arg and len(parts) > 2) else None
                if takes_arg and not arg:
                    self._send(400, {"error": "missing argument"})
                    return
                try:
                    self._send(200, action_executor(name, arg) if takes_arg
                               else action_executor(name))
                except Exception as e:
                    self._send(500, {"error": str(e)})
            else:
                self._send(404, {"error": "unknown route"})

    return ThreadingHTTPServer((host, port), Handler)
```

Note: the executor is called `action_executor(name)` for no-arg routes and `action_executor(name, arg)` for arg routes — the test's lambda `lambda name, arg=None: ...` accepts both shapes; Task 7's real executor must too.

- [ ] **Step 4: verify PASS** — full file: `python3 "Test files/test_farm_agent_core.py"` → OK (16+ tests)
- [ ] **Step 5: Commit** — `git commit -am "feat(agent): token-authed HTTP API (status + actions)"`

---

### Task 7: `farm_agent_main.py` — adapters, wiring, configs [live-smoke]

**Files:**
- Create: `src/farm_agent_main.py`
- Create: `config/farm_agent_config.yaml`, `config/farm_agent_update_config.yaml`

**Interfaces:**
- Consumes: Task 5/6 (`evaluate_health`, `EscalationLadder`, `make_api_server`); `auto_updater.check_updates`; `utils.exe_dir` (frozen-safe pathing — same pattern as `window_checker_main.py`, but yaml read via stdlib-safe fallback if `utils` unavailable: NOT needed, pyyaml ships in all exes).
- Produces: `FarmAgent.exe` behavior: 60s loop → snapshot → evaluate → ladder → execute; API thread on configured port; heartbeat `logs/farmagent_heartbeat.txt`; actions log `logs/farmagent_actions.log`.

- [ ] **Step 1: implement** — `src/farm_agent_main.py`:

```python
"""FarmAgent.exe — per-box supervisor + HTTP control plane. STDLIB + pyyaml only.

Deliberately does NOT import pywin32: every OS action is a subprocess call
(tasklist/schtasks/taskkill/shutdown), so this exe stays tiny and can never
deadlock on a hung window. Supervises WindowChecker + per-user Watchdogs;
Sherlock Homeless polls GET /status (a timeout IS the alert)."""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

if not getattr(sys, "frozen", False):
    _SRC = os.path.dirname(os.path.abspath(__file__))
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)

from utils import exe_dir, load_yaml
from auto_updater import check_updates
from farm_agent_core import evaluate_health, EscalationLadder, make_api_server

CONFIG_PATH = os.path.join(exe_dir(), "config", "farm_agent_config.yaml")
UPDATE_CONFIG = os.path.join(exe_dir(), "config", "farm_agent_update_config.yaml")
LOOP_SECONDS = 60
VERSION = "1.0.0"


def setup_logger():
    logs = os.path.join(exe_dir(), "logs")
    os.makedirs(logs, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s",
                        handlers=[logging.FileHandler(os.path.join(logs, f"farmagent_{ts}.log"),
                                                      encoding="utf-8"),
                                  logging.StreamHandler()], force=True)
    return logging.getLogger("farmagent")


def _tasklist_count(image, username=None):
    cmd = ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image}"]
    if username:
        cmd += ["/FI", f"USERNAME eq {username}"]
    try:
        out = subprocess.check_output(cmd, text=True, errors="ignore", timeout=15)
        return sum(1 for line in out.splitlines() if image.lower() in line.lower())
    except Exception:
        return 0


def collect_snapshot(cfg):
    hb = os.path.join(exe_dir(), "logs", "windowchecker_heartbeat_%s.txt" % os.environ.get("USERNAME", ""))
    if not os.path.exists(hb):   # older heartbeat naming — glob fallback
        import glob
        cands = glob.glob(os.path.join(exe_dir(), "logs", "windowchecker_heartbeat_*.txt"))
        hb = cands[0] if cands else None
    age = (time.time() - os.stat(hb).st_mtime) if hb and os.path.exists(hb) else None
    watchdog_tasks = cfg.get("watchdog_users") or {}   # {"SinFermera11": "Watchdog11", ...}
    missing = [u for u in watchdog_tasks if _tasklist_count("Watchdog.exe", username=u) == 0]
    return {
        "wc_heartbeat_age_s": age,
        "wc_running": _tasklist_count("WindowChecker.exe") > 0,
        "renderer_count": _tasklist_count("wfreerdp.exe"),
        "expected_sessions": int(cfg.get("expected_sessions", 2)),
        "missing_watchdog_users": missing,
        "cs2_count": _tasklist_count("cs2.exe"),
    }


class ActionExecutor:
    def __init__(self, cfg, ladder, log):
        self.cfg, self.ladder, self.log = cfg, ladder, log
        self.actions_log = os.path.join(exe_dir(), "logs", "farmagent_actions.log")
        self.recent = []

    def _record(self, entry):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {entry}"
        self.recent = (self.recent + [line])[-20:]
        try:
            with open(self.actions_log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        self.log.info("ACTION: %s", entry)

    def __call__(self, name, arg=None):
        if name == "restart_windowchecker":
            subprocess.run(["taskkill", "/F", "/IM", "WindowChecker.exe"],
                           capture_output=True, timeout=15)
            time.sleep(2)
            r = subprocess.run(["schtasks", "/Run", "/TN",
                                self.cfg.get("windowchecker_task", "WindowsChecker")],
                               capture_output=True, text=True, timeout=15)
            self._record(f"restart_windowchecker rc={r.returncode}")
            return {"ran": name, "rc": r.returncode}
        if name == "run_watchdog_task":
            task = (self.cfg.get("watchdog_users") or {}).get(arg)
            if not task:
                return {"error": f"unknown user {arg!r}"}
            r = subprocess.run(["schtasks", "/Run", "/TN", task],
                               capture_output=True, text=True, timeout=15)
            self._record(f"run_watchdog_task user={arg} task={task} rc={r.returncode}")
            return {"ran": name, "user": arg, "rc": r.returncode}
        if name == "run_health_check":
            self._record("manual health check requested")
            return {"ran": name}          # loop picks it up next tick; cheap + safe
        if name == "reboot":
            self._record("REBOOT (requested)")
            subprocess.run(["shutdown", "/r", "/t", "30",
                            "/c", "FarmAgent recovery reboot"], capture_output=True, timeout=15)
            return {"ran": name, "in_seconds": 30}
        return {"error": f"unknown action {name!r}"}


def main():
    log = setup_logger()
    log.info("=== FarmAgent v%s STARTED ===", VERSION)
    try:
        check_updates(config_path=UPDATE_CONFIG)
    except Exception as e:
        log.warning("update check failed: %s", e)

    cfg = load_yaml("config/farm_agent_config.yaml") or {}
    thresholds = {"wc_heartbeat_max_age_s": cfg.get("wc_heartbeat_max_age_s", 300)}
    ladder = EscalationLadder(os.path.join(exe_dir(), "logs", "ladder_state.json"),
                              unhealthy_loops_before_reboot=cfg.get("unhealthy_loops_before_reboot", 3),
                              reboot_min_interval_s=cfg.get("reboot_min_interval_s", 7200))
    executor = ActionExecutor(cfg, ladder, log)
    last = {"snapshot": {}, "checks": [], "actions": []}

    def status():
        return {"box": os.environ.get("COMPUTERNAME", "?"),
                "agent_version": VERSION,
                "snapshot": last["snapshot"], "checks": last["checks"],
                "recent_actions": executor.recent,
                "ladder": ladder.state,
                "ts": datetime.now().isoformat(timespec="seconds")}

    srv = make_api_server(cfg.get("bind", "0.0.0.0"), int(cfg.get("port", 8765)),
                          str(cfg.get("token", "")), status, executor)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("API listening on %s:%s", cfg.get("bind", "0.0.0.0"), cfg.get("port", 8765))

    hb_path = os.path.join(exe_dir(), "logs", "farmagent_heartbeat.txt")
    while True:
        try:
            with open(hb_path, "w") as f:
                f.write(datetime.now().isoformat())
            snap = collect_snapshot(cfg)
            checks = evaluate_health(snap, thresholds)
            actions = ladder.next_actions(checks)
            last.update(snapshot=snap, checks=checks, actions=actions)
            for a in actions:
                name, _, arg = a.partition(":")
                executor(name, arg or None)
        except Exception:
            log.exception("agent loop error — continuing")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 2: configs** — `config/farm_agent_config.yaml`:

```yaml
# FarmAgent per-box config. Fill token + watchdog_users per machine.
bind: "0.0.0.0"
port: 8765
# Shared secret for Sherlock; header X-Farm-Token. NEVER commit a real value.
token: ""
expected_sessions: 2
wc_heartbeat_max_age_s: 300
unhealthy_loops_before_reboot: 3
reboot_min_interval_s: 7200
windowchecker_task: "WindowsChecker"
# Map RDP username -> its Watchdog Task Scheduler task
watchdog_users:
  SinFermera11: "Watchdog11"
  SinFermera12: "Watchdog12"
```

`config/farm_agent_update_config.yaml`: copy `config/windowchecker_update_config.yaml` structure with `executable_name: "FarmAgent.exe"`, `current_version: "1.0.0"`, `github_token: ""`, no `watchdog_tasks` block.

- [ ] **Step 3: compile + import smoke (mac)** — `python3 -m py_compile src/farm_agent_main.py && python3 -c "import yaml; yaml.safe_load(open('config/farm_agent_config.yaml')); yaml.safe_load(open('config/farm_agent_update_config.yaml')); print('ok')"`
- [ ] **Step 4: Commit** — `git commit -am "feat(agent): FarmAgent main — snapshot adapters, executor, wiring, configs"`

---

### Task 8: CI sixth asset + docs

**Files:**
- Modify: `.github/workflows/release.yml` (build step ~:49-53, assets array ~:102, upload list ~:137-141)
- Modify: `AGENTS.md` (architecture + release sections)

- [ ] **Step 1:** add to the build step: `pyinstaller --noconfirm --clean --onefile --name FarmAgent src/farm_agent_main.py`; change `assets=(Watchdog Boot WindowChecker DropStats MemReductLooped)` → `assets=(Watchdog Boot WindowChecker DropStats MemReductLooped FarmAgent)`; add `dist/FarmAgent.exe \` to `gh release create`.
- [ ] **Step 2:** AGENTS.md — add FarmAgent to the executables list (entry point, deploy folder `Documents\FarmAgent\`, task names `FarmAgent` + `FarmAgentKeepAlive`), document `/status` + `/action/*` API and the two new regions.yaml keys (`disconnect_settle_max_s`, `reopen_settle_s`).
- [ ] **Step 3:** `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"` → ok; Commit: `git commit -am "ci: build+release FarmAgent.exe as sixth asset; docs"`

---

### Task 9: fleet bootstrap script

**Files:**
- Create: `scripts/bootstrap_box.sh` (mac-side; bash + sshpass + scp)

**Interfaces:**
- Consumes: a local `dist/` dir of downloaded release exes (`gh release download -p '*.exe' -D dist`), inventory lines `user@host` in a creds file next to per-host password files (documented in header comment).
- Produces: idempotent per-box install: stop tasks → backup exes → push exes + missing config templates (never overwrites existing regions.yaml/tokens) → register `FarmAgent` (ONLOGON) + `FarmAgentKeepAlive` (every 10 min, starts only if not running) tasks → restart → acceptance: `curl -s -H "X-Farm-Token: $TOKEN" http://<host>:8765/status` returns JSON with `"box"`.

- [ ] **Step 1:** write the script (~120 lines: args parsing, `run()` ssh helper, staged `scp` to `C:/Users/<user>/Documents/staging`, per-exe move to deploy folders per AGENTS.md layout, `schtasks /Create /F /SC ONLOGON /RL HIGHEST /TN FarmAgent /TR "<path>"` + `/SC MINUTE /MO 10` keep-alive, final curl check; `set -euo pipefail`; every remote step echoes a receipt).
- [ ] **Step 2:** `bash -n scripts/bootstrap_box.sh` → syntax ok; shellcheck if installed.
- [ ] **Step 3:** Commit: `git commit -am "feat(rollout): per-box bootstrap script with /status acceptance check"`

---

### Task 10: live smoke drill on host-67 (GATE before fleet rollout)

**Files:** none (procedure — receipts pasted into PR #3 description)

- [ ] **Step 1 — deploy candidates to host-67 only** via `scripts/bootstrap_box.sh` using locally-built exes (or first CI release artifacts).
- [ ] **Step 2 — API drill (from mac):** `curl -H "X-Farm-Token: <tok>" http://192.168.1.132:8765/status` → 200 JSON, checks all healthy; bad token → 401.
- [ ] **Step 3 — freeze drill:** on the box: `powershell "(Get-Process wfreerdp | Select -First 1).Id"` → `Suspend-Process -Id <pid>` (via `pssuspend` or `powershell -c "Debug-Process"` alternative: use Sysinternals `pssuspend.exe` shipped to staging). Expected within one WC cycle: reposition SKIPS the frozen window (log line `NOT RESPONDING — skipping`), R5 kills it (`Force-killed FROZEN`, image=wfreerdp.exe), session reconnects, **WC log keeps writing past the incident** (the 7/6 signature cannot reproduce).
- [ ] **Step 4 — supervisor drill:** suspend WindowChecker itself → within ≤2 agent loops: `restart_windowchecker` appears in `farmagent_actions.log`, WC pid changes, heartbeat fresh. `/status` shows the action.
- [ ] **Step 5 — reboot rung (opt-in, announce to Sigma first):** stop WC task + keep it stopped → after 3 loops expect `REBOOT (requested)` + box reboots once; second forced failure within 2h must NOT reboot (rate limit log line).
- [ ] **Step 6 — paste receipts in PR #3**, mark workflow surfaces Built.

---

## Self-review (done at write time)

- **Spec coverage:** R1→T3, R2/R3→T4, R4→T2+T4, R5→T4, R6→T3; agent loop/ladder→T5, API→T6, adapters/config→T7, CI→T8, rollout→T9, drills/testing→T10. Interval floor→T1+T3. Breadcrumbs→T4 (consumed by /status via actions log + ladder state; full breadcrumb ingestion parked — WISHLIST).
- **Placeholders:** none — every code step carries real code (T9 is a scripted-steps task with exact commands named inline).
- **Type consistency:** `reconnect_stuck_session` dict contract used in T4 steps 2/3/5; executor call shapes match T6 note; snapshot keys match T5 tests and T7 collector.
