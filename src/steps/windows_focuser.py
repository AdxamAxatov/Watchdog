"""
steps/windows_focuser.py - periodic RDP session reconnect cycle + frozen recovery

Driven by WindowChecker via cycle_or_recover_rdp_windows. Every tick it:
  1. Disconnects and reconnects EVERY configured session through the RDP
     Session Manager UI (no hung/black detection — a server-side disconnect
     heals a frozen renderer and refreshes a healthy one alike).
  2. Force-kills any pre-cycle window whose handle survived a confirmed
     disconnect (frozen renderer), then restarts that user's Watchdog and
     retries the reconnect.
  3. If no session reappears and the host RDP process is dead, relaunches it.

Requires the reconnect keys (rdp.user1_title/user2_title + disconnect_point_pct)
in regions.yaml — without them the cycle warns and only the reposition +
host-relaunch safety net runs. Tunables live in regions.yaml → rdp_windows + rdp.
"""

import os
import re
import subprocess
import time
import win32gui
import win32con
import win32process
import logging

from utils import exe_dir, load_yaml


def find_rdp_windows(title_substring="SinFermera"):
    """All wfreerdp-owned windows matching the title. Ghost hwnds are resolved
    to the real (hung) window; non-renderer processes (explorer folders named
    'SinFermera', etc.) are excluded so they can never be killed/repositioned.

    Returns:
        List of (hwnd, title) tuples
    """
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


def restart_watchdog_for_titles(closed_titles, log=None):
    """Kill and restart only the Watchdog(s) matching the hung window titles.

    The window-title -> username -> task_name mapping lives under
    `watchdog_tasks:`. WindowChecker.exe owns this recovery now, so we read
    its config first; fall back to boot_update_config.yaml for compatibility
    with older deploys where the mapping still lived with Boot.
    """
    def _has_filled_tasks(loaded):
        for e in (loaded.get("watchdog_tasks") or []):
            if isinstance(e, dict) and e.get("title_contains") and e.get("username") and e.get("task_name"):
                return True
        return False

    try:
        import yaml
        cfg = None
        cfg_dir = os.path.join(exe_dir(), "config")
        # Pick a config that has at least one FULLY-FILLED watchdog_tasks entry.
        # A list of blank placeholder entries is truthy but useless — selecting it
        # would silently skip every entry and never restart any Watchdog.
        for fname in ("windowchecker_update_config.yaml", "boot_update_config.yaml"):
            p = os.path.join(cfg_dir, fname)
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    loaded = yaml.safe_load(f) or {}
                if _has_filled_tasks(loaded):
                    cfg = loaded
                    break
        if cfg is None:
            if log:
                log.warning("restart_watchdog_for_titles: NO config with filled watchdog_tasks "
                            "found in %s — cannot restart Watchdog for %s (fill watchdog_tasks "
                            "in windowchecker_update_config.yaml)", cfg_dir, closed_titles)
            print(f"   !! watchdog_tasks not configured — cannot restart Watchdog for {closed_titles}")
            return
        tasks = cfg.get("watchdog_tasks", [])

        for entry in tasks:
            if not isinstance(entry, dict):
                continue
            title_match = entry.get("title_contains", "")
            username = entry.get("username", "")
            task_name = entry.get("task_name", "")
            if not title_match or not username or not task_name:
                continue

            # Boundary match (not substring): "SinFermera1" must NOT match
            # "SinFermera16" — same rule as _title_matches_session.
            matched = any(_title_matches_session(ct, title_match) for ct in closed_titles)
            if not matched:
                continue

            # Kill only this user's Watchdog
            if log:
                log.info(f"Killing Watchdog.exe for user '{username}'")
            print(f"   Killing Watchdog.exe for user: {username}")
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "Watchdog.exe", "/FI", f"USERNAME eq {username}"],
                    capture_output=True, timeout=15
                )
            except Exception as e:
                if log:
                    log.warning(f"taskkill failed for {username}: {e}")

            time.sleep(2)

            # Restart this user's Watchdog via Task Scheduler
            if log:
                log.info(f"Restarting Watchdog task: {task_name}")
            print(f"   Restarting Watchdog task: {task_name}")
            try:
                subprocess.run(
                    ["schtasks", "/Run", "/TN", task_name],
                    capture_output=True, timeout=15
                )
            except Exception as e:
                if log:
                    log.warning(f"Failed to run task '{task_name}': {e}")
    except Exception as e:
        if log:
            log.warning(f"restart_watchdog_for_titles failed: {e}")


# ============================================================================
# RDP session reconnect cycle + frozen recovery
#
# Every cycle, each configured session is disconnected and reconnected through
# the RDP Session Manager UI — no hung/black detection. A renderer that ignores
# the disconnect (handle survives) is FROZEN and gets force-killed. Only if NO
# session reappears AND the host RDP process is dead do we relaunch RDPClient.
#
# CS2 recovery is intentionally NOT touched here — Watchdog.exe owns it.
# ============================================================================


def _host_rdp_process_running(cfg, log=None):
    """True if either the FreeRDP renderer (wfreerdp.exe) or RDPClient.exe is
    running. Used to decide whether we must relaunch the host RDP stack after
    closing all session windows. Self-contained tasklist check (mirrors
    watchdog.is_process_running) — no dependency on watchdog.py.
    """
    paths = (cfg or {}).get("paths", {}) or {}

    host_names = []
    wfreerdp_list = paths.get("wfreerdp_exe") or []
    if wfreerdp_list:
        host_names.append(os.path.basename(str(wfreerdp_list[0])))
    else:
        host_names.append("wfreerdp.exe")  # sensible default even if unconfigured
    host_names.append("RDPClient.exe")

    for image_name in host_names:
        if not image_name:
            continue
        try:
            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image_name}"],
                text=True, errors="ignore", timeout=15,
            )
            if "No tasks are running" in out:
                continue
            if image_name.lower() in out.lower():
                if log:
                    log.info(f"Host RDP process alive: {image_name}")
                return True
        except Exception as e:
            if log:
                log.warning(f"tasklist check failed for {image_name}: {e}")
    return False


def _base_title(title):
    """Strip a trailing ' (Not Responding)' / 'Не отвечает' suffix that Windows
    appends to a hung window, so the title matches the configured session name."""
    return re.sub(r"\s*\((?:not responding|не отвечает)\)\s*$", "",
                  title or "", flags=re.IGNORECASE).strip()


def _title_matches_session(title, sess_title):
    """True if window `title` belongs to session `sess_title`.

    Real RDP window titles look like 'SinFermera16 (SinFermera16@127.0.0.2)', so
    we can't require an exact match. Match the session name at the START of the
    title followed by a boundary (space, '(', or end) — this matches the
    '(User@Host)' and ' (Not Responding)' variants but NOT 'SinFermera1' against
    'SinFermera16' (digit prefix collision).
    """
    s = (sess_title or "").strip().lower()
    if not s:
        return False
    tl = (title or "").strip().lower()
    return tl == s or tl.startswith(s + " ") or tl.startswith(s + "(")


def _session_present(sess_title, window_titles):
    """True if a window for session `sess_title` is among window_titles."""
    return any(_title_matches_session(t, sess_title) for t in window_titles)


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
        if log:
            log.error("Force-kill: no PID for %s", title)
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


def _clear_wer_dialogs(log=None):
    """Clear any leftover Windows Error Reporting dialog (WerFault.exe) for the
    current user after a frozen-window kill. The 'not responding' ghost dialog
    clears itself once the real window's process is gone; WerFault is separate."""
    try:
        user = os.environ.get("USERNAME", "")
        cmd = ["taskkill", "/F", "/IM", "WerFault.exe"]
        if user:
            cmd += ["/FI", f"USERNAME eq {user}"]
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as e:
        if log:
            log.warning("WerFault cleanup failed: %s", e)


def reposition_rdp_windows_to_corners(windows, log=None):
    """Move the two RDP session windows into opposite desktop corners:
      windows[0] -> TOP-LEFT      (window's top-left aligns to the work area's top-left)
      windows[1] -> BOTTOM-RIGHT  (window's bottom-right aligns to the work area's bottom-right)

    Pure move — each window keeps its current size (SWP_NOSIZE). Uses the work
    area (SPI_GETWORKAREA) so the windows don't tuck under the taskbar.
    """
    import ctypes
    import ctypes.wintypes

    if not windows or len(windows) < 2:
        if log:
            log.info("Reposition: need 2 windows, have %d — skipping", len(windows) if windows else 0)
        return

    rect = ctypes.wintypes.RECT()
    SPI_GETWORKAREA = 0x0030
    if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        if log:
            log.warning("Reposition: SystemParametersInfoW(SPI_GETWORKAREA) failed — skipping")
        return
    work_left, work_top, work_right, work_bottom = rect.left, rect.top, rect.right, rect.bottom

    corners = ("top-left", "bottom-right")
    for (hwnd, title), corner in zip(windows[:2], corners):
        try:
            if not win32gui.IsWindow(hwnd):
                continue
            # R1: NEVER make a synchronous win32 call into a frozen window's
            # thread — that deadlocked WindowChecker on 7/4 and 7/6.
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
            else:  # bottom-right: align the window's bottom-right to the work-area's
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


def reconnect_stuck_session(entry_pct, disconnect_pct, old_hwnd=None, log=None,
                            settle_max_s=10.0, reopen_settle_s=3.0):
    """Cycle a session via the RDP Session Manager UI with EFFECT CONFIRMATION:
    select its entry -> click Disconnect -> WAIT for the server-side teardown
    (old window destroyed, polled up to settle_max_s) -> settle reopen_settle_s
    -> re-open. Returns a dict {"ran", "dialog_seen", "old_destroyed"}; callers
    must gate frozen-kill eligibility on recovery_rules.disconnect_confirmed
    (R2+R3 — reopening ~1.5s after Disconnect raced the session arbitration and
    birthed frozen renderers; click dispatch alone proved nothing when the
    manager itself was hung).

    All click points are percentages of the RDP Session Manager window's client
    area (resolved live from its hwnd, so its on-screen position doesn't matter).

    RDPClient pops a "Success" confirmation dialog ("Session '...' disconnected")
    after EVERY click, so each click is followed by close_confirmation_dialog()
    (presses Enter, falls back to WM_CLOSE) and a re-focus before the next click.

    Click types:
      - select entry  : single click
      - disconnect    : single click (top toolbar button)
      - reconnect     : double click (same action the launcher uses to start)

    Result dict is all-False if the manager wasn't found, is hung, or couldn't
    be focused (host down / manager frozen — caller's escalation covers both).
    """
    from winops import (find_window, force_foreground, pct_to_screen_xy,
                        safe_click, safe_double_click, window_responsive)
    from steps.rdp import close_confirmation_dialog

    result = {"ran": False, "dialog_seen": False, "old_destroyed": False}

    RDP_MANAGER_TITLE = "RDP Session Manager"
    m = find_window(RDP_MANAGER_TITLE, require_visible=True)
    if not m:
        if log:
            log.warning("Reconnect: '%s' window not found — skipping (host may be down)", RDP_MANAGER_TITLE)
        return result
    # A hung manager still accepts focus but eats clicks — don't fire blind
    # clicks into a dead message queue (observed on host-67; the phantom
    # "confirmed" disconnects then ghost-killed healthy renderers).
    if not window_responsive(m.hwnd):
        if log:
            log.error("Reconnect: RDP Session Manager NOT RESPONDING — skipping clicks "
                      "(FarmAgent/host relaunch path owns this)")
        write_recovery_breadcrumb("rdp-session-manager", "manager-hung")
        return result

    def _click_then_confirm(label, click_fn, pct):
        # RESTORE + focus the manager BEFORE reading its rect. A minimized window
        # reports its rect at (-32000, -32000); computing a click point from that
        # flings the mouse to a screen corner and trips pyautogui's fail-safe
        # (exactly what crashed the previous run). Recompute the coord each step
        # from the *current* rect so a dialog moving/minimizing it can't stale us.
        try:
            win32gui.ShowWindow(m.hwnd, win32con.SW_RESTORE)
        except Exception:
            pass
        if not force_foreground(m.hwnd, tries=6, sleep_s=0.2):
            if log:
                log.warning("Reconnect: could not focus RDP Session Manager before %s — skipping", label)
            return False, False
        time.sleep(0.25)
        x, y = pct_to_screen_xy(m.hwnd, float(pct["x"]), float(pct["y"]))
        if x < 0 or y < 0:
            if log:
                log.warning("Reconnect: %s target off-screen (%d, %d) — window not restored; skipping", label, x, y)
            return False, False
        if log:
            log.info("Reconnect: %s at (%d, %d)", label, x, y)
        click_fn(x, y)
        time.sleep(0.4)
        # Dismiss the "Success" dialog this click spawns (short appear-timeout
        # so a click that happens not to spawn one doesn't stall the sequence).
        # Its return value is EVIDENCE: dialog seen == the manager reacted.
        dialog_seen = False
        try:
            dialog_seen = bool(close_confirmation_dialog(
                hwnd=m.hwnd, verbose=False, appear_timeout_s=2.5))
        except Exception as e:
            if log:
                log.warning("Reconnect: dialog handling failed after %s: %s", label, e)
        return True, dialog_seen

    # If we can't even select the entry (window won't restore/focus), abort —
    # don't fire blind clicks. The caller's host-relaunch path still covers the
    # fully-dead case.
    ok, _ = _click_then_confirm("select entry", safe_click, entry_pct)
    if not ok:
        return result
    ok, dlg = _click_then_confirm("click Disconnect", safe_click, disconnect_pct)
    if not ok:
        return result
    result["ran"] = True
    result["dialog_seen"] = bool(dlg)
    # R2: respect the server-side teardown — wait for the OLD window to die
    # before reopening (reopening ~1.5s after Disconnect raced the session
    # arbitration; LSM showed arbitration ending ~8s after the old timing).
    if old_hwnd:
        deadline = time.time() + float(settle_max_s)
        while time.time() < deadline:
            if not win32gui.IsWindow(old_hwnd):
                result["old_destroyed"] = True
                break
            time.sleep(0.5)
    time.sleep(float(reopen_settle_s))
    _click_then_confirm("re-open entry (double-click)", safe_double_click, entry_pct)
    return result


def _sleep_with_beat(total_seconds, beat=None, chunk=10):
    """Sleep total_seconds, calling beat() every `chunk` seconds so a long wait
    inside the recovery cycle keeps the heartbeat fresh (health_check won't kill us)."""
    end = time.time() + total_seconds
    while True:
        if beat:
            try:
                beat()
            except Exception:
                pass
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(chunk, remaining))


def cycle_or_recover_rdp_windows(title_search="SinFermera", log=None, beat=None):
    """Owner-approved periodic RDP maintenance tick — the RECONNECT CYCLE.

    No hung/black detection: every cycle, each configured session is
    disconnected and reconnected through the RDP Session Manager UI. A
    server-side disconnect doesn't care whether the local renderer is frozen,
    so this heals a stuck window and refreshes a healthy one with the same
    three clicks (owner-validated).

      (a) for each configured session: select entry -> Disconnect -> double-click
          to reopen (reconnect_stuck_session). Pre-cycle window handles are kept;
      (d) wait reopen_wait_seconds;
      (b2) FROZEN cleanup: a healthy disconnect destroys the old window (the
          reopen creates a NEW handle). A pre-cycle handle that SURVIVED a
          *confirmed* disconnect belonged to a frozen renderer — force-kill its
          process (clears the WER dialogs). Only handles whose session's
          disconnect was actually clicked are eligible — a focus failure can
          never ghost-kill a healthy window;
      (e) frozen kills (only) restart that user's Watchdog;
      (e2) any expected session still missing gets one more disconnect+reconnect
          attempt;
      (f) if NO session window exists AND neither wfreerdp.exe nor RDPClient.exe
          is running, relaunch the host stack via steps.rdp.run().

    Requires rdp.user1_title/user2_title + rdp.disconnect_point_pct in
    regions.yaml — without them the cycle only warns and falls through to the
    reposition + host-relaunch safety net (no close/reconnect actions).

    CS2 is left entirely to Watchdog.exe. No CS2 actions here, so no lock needed.

    Returns (closed_count, relaunched_host: bool).
    """
    cfg = load_yaml("config/regions.yaml")
    rdp_cfg = cfg.get("rdp_windows", {}) or {}
    reopen_wait = float(rdp_cfg.get("reopen_wait_seconds", 15))
    settle_max = float(rdp_cfg.get("disconnect_settle_max_s", 10))   # R2
    reopen_settle = float(rdp_cfg.get("reopen_settle_s", 3))          # R2

    rdp_ui = cfg.get("rdp", {}) or {}
    disconnect_pct = rdp_ui.get("disconnect_point_pct")
    expected_sessions = [
        (rdp_ui.get("user1_title"), rdp_ui.get("user1_point_pct")),
        (rdp_ui.get("user2_title"), rdp_ui.get("user2_point_pct")),
    ]
    configured_sessions = [(t, p) for t, p in expected_sessions if t and p]
    reconnect_mode = bool(disconnect_pct) and bool(configured_sessions)

    windows = find_rdp_windows(title_search)  # exe-filtered; no z-order truncation
    if not windows:
        if log:
            log.warning("No SinFermera windows found at tick start")
        print("!!  No SinFermera windows found")

    closed_count = 0
    relaunched_host = False
    hung_titles = []     # windows that need their user's Watchdog restarted
    closed_hwnds = []    # (hwnd, title) expected to be GONE after the wait — any
                         # handle that SURVIVED was a FROZEN renderer and gets
                         # force-killed in (b2).

    if reconnect_mode:
        # (a) Reconnect cycle: disconnect+reconnect every configured session via
        # the Session Manager — no detection, healthy and stuck treated alike.
        if log:
            log.info("Reconnect cycle: disconnect+reconnect %d session(s) via Session Manager",
                     len(configured_sessions))
        print(f"   Reconnect cycle: {len(configured_sessions)} session(s) via Session Manager")
        from recovery_rules import disconnect_confirmed
        cycled_titles = []  # sessions whose Disconnect was CONFIRMED by effect (R3)
        for sess_title, entry_pct in configured_sessions:
            if beat:
                try:
                    beat()
                except Exception:
                    pass
            old_hwnd = next((h for h, t in windows
                             if _title_matches_session(t, sess_title)), None)
            try:
                r = reconnect_stuck_session(entry_pct, disconnect_pct, old_hwnd=old_hwnd,
                                            log=log, settle_max_s=settle_max,
                                            reopen_settle_s=reopen_settle)
                if disconnect_confirmed(r["dialog_seen"], r["old_destroyed"]):
                    cycled_titles.append(sess_title)
                    closed_count += 1
                elif r["ran"]:
                    if log:
                        log.warning("Disconnect DISPATCHED but UNCONFIRMED for %r — "
                                    "not eligible for frozen-kill (manager hung?)", sess_title)
                    print(f"   !! Disconnect unconfirmed for {sess_title}")
                else:
                    if log:
                        log.warning("Reconnect cycle: sequence did not complete for %r "
                                    "(manager missing/hung or focus failed)", sess_title)
                    print(f"   !! Reconnect sequence did not complete for {sess_title}")
            except Exception:
                if log:
                    log.exception("Reconnect cycle failed for %r", sess_title)
        # Only pre-cycle windows belonging to a CONFIRMED-disconnected session
        # may be ghost-checked in (b2). If the disconnect never happened, the
        # old window legitimately survives — it must not be force-killed.
        closed_hwnds = [
            (h, t) for h, t in windows
            if any(_title_matches_session(t, s) for s in cycled_titles)
        ]
    else:
        # Reconnect keys missing — nothing can be cycled. Warn loudly; the
        # reposition + host-relaunch safety net below still runs.
        if log:
            log.error("Reconnect cycle NOT configured (rdp.user1_title/user2_title + "
                      "disconnect_point_pct required in regions.yaml) — skipping cycle")
        print("   !! Reconnect cycle not configured — fill rdp.user1_title/user2_title "
              "+ disconnect_point_pct in regions.yaml")

    # (d) wait, then re-enumerate
    if closed_count > 0 or not windows:
        if log:
            log.info(f"Waiting {reopen_wait:.0f}s for RDP window(s) to reopen...")
        print(f"   Waiting {reopen_wait:.0f}s for RDP to reopen...")
        _sleep_with_beat(reopen_wait, beat=beat)

    # (b2) FROZEN-window recovery: a healthy window is destroyed by the
    # disconnect and reopens with a NEW handle. A FROZEN renderer ignores the
    # disconnect — so any handle in closed_hwnds that is STILL a window after
    # the wait is frozen. Force-kill its process; the killed session then shows
    # as missing below and gets reconnected via the Session Manager, and its
    # user's Watchdog is restarted. The healthy window (new handle) is never
    # touched.
    killed_any = False
    for hwnd, title in closed_hwnds:
        if win32gui.IsWindow(hwnd):
            if _force_kill_window_process(hwnd, title, log=log):
                killed_any = True
                base = _base_title(title)
                if base not in hung_titles:
                    hung_titles.append(base)
    if killed_any:
        _clear_wer_dialogs(log=log)
        time.sleep(1.0)  # let the handles/dialogs settle before re-enumerating
    if beat:
        try:
            beat()
        except Exception:
            pass

    reappeared = find_rdp_windows(title_search)

    # (b3) R5: a window that reopened FROZEN gets one kill+reconnect retry,
    # then a breadcrumb for FarmAgent/Sherlock. Prevents carrying a dead-on-
    # arrival renderer into reposition/next cycle.
    from winops import window_responsive
    for hwnd, title in list(reappeared):
        if window_responsive(hwnd):
            continue
        if log:
            log.warning("Post-reopen: %s NOT RESPONDING — kill + retry", title)
        print(f"   !! Post-reopen frozen: {title}")
        if _force_kill_window_process(hwnd, title, log=log):
            base = _base_title(title)
            sess = next(((st, ep) for st, ep in configured_sessions
                         if _title_matches_session(title, st)), None)
            if sess and disconnect_pct:
                try:
                    reconnect_stuck_session(sess[1], disconnect_pct, log=log,
                                            settle_max_s=settle_max,
                                            reopen_settle_s=reopen_settle)
                except Exception:
                    if log:
                        log.exception("R5 retry failed for %r", base)
            write_recovery_breadcrumb(base, "frozen-after-reopen")
            if base not in hung_titles:
                hung_titles.append(base)
    reappeared = find_rdp_windows(title_search)

    # (d2) Both windows are back and healthy — snap them into the two opposite
    # desktop corners (top-left + bottom-right). Runs every cycle so the windows
    # always end up in a known position after the close/reopen.
    if len(reappeared) >= 2:
        reposition_rdp_windows_to_corners(reappeared, log)

    # (e) a window was genuinely FROZEN (force-killed in b2) — bounce that
    # user's Watchdog so it recovers cleanly in the freshly-reopened session.
    # A routine healthy cycle does NOT reach here (hung_titles stays empty).
    if hung_titles:
        if log:
            log.info(f"Restarting Watchdog for stuck session(s): {hung_titles}")
        restart_watchdog_for_titles(hung_titles, log)

    # (e2) Targeted reconnect: any EXPECTED session that didn't reopen gets a
    # (nother) disconnect+reconnect through the RDP Session Manager UI. In
    # reconnect mode this is the retry for a session whose first sequence
    # failed (focus) or whose frozen renderer was just killed. If the mapping
    # isn't set, this is skipped and (f) below still handles the
    # host-fully-dead case. reconnect_stuck_session itself no-ops when the
    # session manager isn't open (host down).
    reappeared_titles = [t for _, t in reappeared]
    if disconnect_pct and any(st for st, _ in expected_sessions):
        for sess_title, entry_pct in expected_sessions:
            if not sess_title or not entry_pct:
                continue
            if _session_present(sess_title, reappeared_titles):
                continue  # this session is open — nothing to do
            if log:
                log.warning("Expected session %r did not reopen — disconnect+reconnect "
                            "via RDP Session Manager", sess_title)
            print(f"   !! {sess_title} did not reopen — reconnecting via session manager")
            if beat:
                try:
                    beat()
                except Exception:
                    pass
            try:
                reconnect_stuck_session(entry_pct, disconnect_pct, log=log,
                                        settle_max_s=settle_max,
                                        reopen_settle_s=reopen_settle)
            except Exception:
                if log:
                    log.exception("reconnect_stuck_session failed for %r", sess_title)

    # (f) relaunch host only if nothing reappeared AND host process is dead
    if not reappeared:
        if _host_rdp_process_running(cfg, log=log):
            if log:
                log.info("No session window yet but host RDP process is alive — "
                         "leaving it to auto-reopen, not relaunching")
            print("   No window yet, but host RDP alive — waiting for auto-reopen")
        else:
            if log:
                log.warning("No RDP session window and host RDP process DEAD — "
                            "relaunching via steps.rdp.run()")
            print("   !! Host RDP dead — relaunching RDPClient")
            if beat:
                try:
                    beat()
                except Exception:
                    pass
            try:
                from steps.rdp import run as rdp_run
                rdp_run()
                relaunched_host = True
                if log:
                    log.info("Relaunched host RDP stack")
            except Exception as e:
                if log:
                    log.exception("Failed to relaunch host RDP stack")
                print(f"   !! Relaunch failed: {e}")
    else:
        if log:
            log.info(f"RDP session window(s) present after cycle: {len(reappeared)}")
        print(f"   OK {len(reappeared)} RDP window(s) present")

    if log:
        log.info(f"RDP tick result: closed={closed_count}, relaunched_host={relaunched_host}")
    return closed_count, relaunched_host


if __name__ == "__main__":
    # Manual test: run ONE RDP maintenance tick — the same routine Boot runs.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    cycle_or_recover_rdp_windows()