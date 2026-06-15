"""
steps/windows_focuser.py - periodic RDP session reconnect cycle

Driven by WindowChecker via cycle_or_recover_rdp_windows. Every tick it:
  1. Disconnects and reconnects EVERY configured session through the RDP
     Session Manager UI (no hung/black detection — a server-side disconnect
     heals a frozen renderer and refreshes a healthy one alike).
  2. Verifies both windows reopened; retries the clicks (up to 3x) for any
     session that did not come back.
  3. Repositions the two windows to opposite desktop corners.
  4. Ensures each user's Watchdog.exe is running, starting any that aren't.
  5. If no session reappears and the host RDP process is dead, relaunches it.

Requires the reconnect keys (rdp.user1_title/user2_title + disconnect_point_pct)
in regions.yaml — without them the cycle warns and only the reposition +
host-relaunch safety net runs. Tunables live in regions.yaml → rdp_windows + rdp.
"""

import os
import subprocess
import time
import win32gui
import win32con
import logging

from utils import exe_dir, load_yaml


def find_rdp_windows(title_substring="SinFermera"):
    """
    Find all RDP game windows matching title substring.

    Returns:
        List of (hwnd, title) tuples
    """
    windows = []

    def enum_callback(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and title_substring.lower() in title.lower():
                results.append((hwnd, title))

    win32gui.EnumWindows(enum_callback, windows)
    return windows


def _has_filled_tasks(loaded):
    """True if `loaded` has at least one watchdog_tasks entry with both a
    username and a task_name. A list of blank placeholders is truthy but useless."""
    for e in (loaded.get("watchdog_tasks") or []):
        if isinstance(e, dict) and e.get("username") and e.get("task_name"):
            return True
    return False


def _load_watchdog_tasks(log=None):
    """Return the watchdog_tasks list (username + task_name per user) from the
    first config that actually has a filled entry. WindowChecker owns this now,
    so its config is read first; fall back to boot_update_config.yaml for older
    deploys. Returns [] (and warns) when nothing is configured.
    """
    try:
        import yaml
        cfg_dir = os.path.join(exe_dir(), "config")
        for fname in ("windowchecker_update_config.yaml", "boot_update_config.yaml"):
            p = os.path.join(cfg_dir, fname)
            if not os.path.exists(p):
                continue
            with open(p, 'r', encoding='utf-8') as f:
                loaded = yaml.safe_load(f) or {}
            if _has_filled_tasks(loaded):
                return loaded.get("watchdog_tasks", []) or []
        if log:
            log.warning("watchdog_tasks not configured in %s — cannot verify/restart "
                        "Watchdog (fill username+task_name in windowchecker_update_config.yaml)",
                        cfg_dir)
        print("   !! watchdog_tasks not configured — skipping Watchdog liveness check")
    except Exception as e:
        if log:
            log.warning(f"Failed to load watchdog_tasks: {e}")
    return []


def _watchdog_running_for_user(username, log=None):
    """True if Watchdog.exe is running under `username`. On any tasklist error,
    assume it IS running (return True) so we never spuriously launch a duplicate.
    """
    try:
        out = subprocess.check_output(
            ["tasklist", "/NH", "/FI", "IMAGENAME eq Watchdog.exe",
             "/FI", f"USERNAME eq {username}"],
            text=True, errors="ignore", timeout=15,
        )
        return "watchdog.exe" in out.lower()
    except Exception as e:
        if log:
            log.warning("tasklist check failed for %s (assuming Watchdog alive): %s", username, e)
        return True


def ensure_watchdogs_running(log=None):
    """Make sure each configured user's Watchdog.exe is running; start any that
    are NOT, via its Task Scheduler task. Already-running Watchdogs are left
    untouched (no kill). Called at the end of every RDP cycle.
    """
    for entry in _load_watchdog_tasks(log):
        if not isinstance(entry, dict):
            continue
        username = entry.get("username", "")
        task_name = entry.get("task_name", "")
        if not username or not task_name:
            continue
        if _watchdog_running_for_user(username, log=log):
            if log:
                log.info("Watchdog alive for user '%s'", username)
            print(f"   OK Watchdog running for {username}")
            continue
        if log:
            log.warning("Watchdog NOT running for user '%s' — starting task '%s'", username, task_name)
        print(f"   !! Watchdog down for {username} — starting task {task_name}")
        try:
            subprocess.run(["schtasks", "/Run", "/TN", task_name],
                           capture_output=True, timeout=15)
        except Exception as e:
            if log:
                log.warning("Failed to run task '%s': %s", task_name, e)


# ============================================================================
# RDP session reconnect cycle
#
# Every cycle, each configured session is disconnected and reconnected through
# the RDP Session Manager UI — no hung/black detection (the disconnect itself
# heals a frozen renderer). Windows that don't reopen get their clicks retried;
# only if NO session reappears AND the host RDP process is dead do we relaunch
# RDPClient.
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
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if corner == "top-left":
                x, y = work_left, work_top
            else:  # bottom-right: align the window's bottom-right to the work-area's
                x, y = work_right - w, work_bottom - h
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOP, x, y, 0, 0,
                win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            if log:
                log.info("Reposition: %s -> %s at (%d, %d)", title, corner, x, y)
            print(f"   -> Repositioned {title} to {corner}")
        except Exception as e:
            if log:
                log.warning("Reposition failed for %s: %s", title, e)


def reconnect_stuck_session(entry_pct, disconnect_pct, log=None):
    """Recover a session that didn't reopen, via the RDP Session Manager UI:
    select its entry -> click Disconnect -> re-open it. All click points are
    percentages of the RDP Session Manager window's client area (resolved live
    from its hwnd, so its on-screen position doesn't matter).

    RDPClient pops a "Success" confirmation dialog ("Session '...' disconnected")
    after EVERY click, so each click is followed by close_confirmation_dialog()
    (presses Enter, falls back to WM_CLOSE) and a re-focus before the next click.

    Click types:
      - select entry  : single click
      - disconnect    : single click (top toolbar button)
      - reconnect     : double click (same action the launcher uses to start)

    Returns True if the sequence ran, False if the manager wasn't found/focusable
    (in which case the host is likely down and the caller's relaunch path covers it).
    """
    from winops import find_window, force_foreground, pct_to_screen_xy, safe_click, safe_double_click
    from steps.rdp import close_confirmation_dialog

    RDP_MANAGER_TITLE = "RDP Session Manager"
    m = find_window(RDP_MANAGER_TITLE, require_visible=True)
    if not m:
        if log:
            log.warning("Reconnect: '%s' window not found — skipping (host may be down)", RDP_MANAGER_TITLE)
        return False

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
            return False
        time.sleep(0.25)
        x, y = pct_to_screen_xy(m.hwnd, float(pct["x"]), float(pct["y"]))
        if x < 0 or y < 0:
            if log:
                log.warning("Reconnect: %s target off-screen (%d, %d) — window not restored; skipping", label, x, y)
            return False
        if log:
            log.info("Reconnect: %s at (%d, %d)", label, x, y)
        click_fn(x, y)
        time.sleep(0.4)
        # Dismiss the "Success" dialog this click spawns (short appear-timeout
        # so a click that happens not to spawn one doesn't stall the sequence).
        try:
            close_confirmation_dialog(hwnd=m.hwnd, verbose=False, appear_timeout_s=2.5)
        except Exception as e:
            if log:
                log.warning("Reconnect: dialog handling failed after %s: %s", label, e)
        return True

    # If we can't even select the entry (window won't restore/focus), abort —
    # don't fire blind clicks. The caller's host-relaunch path still covers the
    # fully-dead case.
    if not _click_then_confirm("select entry", safe_click, entry_pct):
        return False
    # Disconnect must also be confirmed-clicked: a True return means the full
    # disconnect+reconnect sequence ran (counted by the caller). A focus failure
    # here must read as False so the caller knows the session wasn't cycled.
    if not _click_then_confirm("click Disconnect", safe_click, disconnect_pct):
        return False
    _click_then_confirm("re-open entry (double-click)", safe_double_click, entry_pct)
    return True


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
    """Periodic RDP maintenance tick — DISCONNECT/RECONNECT ONLY (no hung
    detection). Every cycle:

      (1) for each configured session: select entry -> Disconnect -> double-click
          to reopen (reconnect_stuck_session). A server-side disconnect heals a
          frozen renderer and refreshes a healthy one with the same clicks;
      (2) wait reopen_wait_seconds, then verify both windows reopened. Any
          session still missing has its clicks retried, up to RETRY_ATTEMPTS
          times, re-checking after each attempt;
      (3) once both windows are present, reposition them to opposite desktop
          corners (top-left + bottom-right);
      (4) ensure each configured user's Watchdog.exe is running — start (not
          kill) any that aren't, via its Task Scheduler task;
      (5) safety net: if NO session window exists AND neither wfreerdp.exe nor
          RDPClient.exe is running, relaunch the host stack via steps.rdp.run().

    Requires rdp.user1_title/user2_title + rdp.disconnect_point_pct in
    regions.yaml — without them the cycle only warns and falls through to the
    reposition + Watchdog-liveness + host-relaunch safety net.

    CS2 is left entirely to Watchdog.exe. No CS2 actions here, so no lock needed.

    Returns (reconnected_count, relaunched_host: bool).
    """
    RETRY_ATTEMPTS = 3

    cfg = load_yaml("config/regions.yaml")
    rdp_cfg = cfg.get("rdp_windows", {}) or {}
    reopen_wait = float(rdp_cfg.get("reopen_wait_seconds", 15))

    rdp_ui = cfg.get("rdp", {}) or {}
    disconnect_pct = rdp_ui.get("disconnect_point_pct")
    configured_sessions = [
        (rdp_ui.get("user1_title"), rdp_ui.get("user1_point_pct")),
        (rdp_ui.get("user2_title"), rdp_ui.get("user2_point_pct")),
    ]
    configured_sessions = [(t, p) for t, p in configured_sessions if t and p]
    reconnect_mode = bool(disconnect_pct) and bool(configured_sessions)

    def _beat():
        if beat:
            try:
                beat()
            except Exception:
                pass

    reconnected_count = 0
    relaunched_host = False

    # (1) Disconnect+reconnect every configured session via the Session Manager.
    if reconnect_mode:
        if log:
            log.info("Reconnect cycle: disconnect+reconnect %d session(s) via Session Manager",
                     len(configured_sessions))
        print(f"   Reconnect cycle: {len(configured_sessions)} session(s) via Session Manager")
        for sess_title, entry_pct in configured_sessions:
            _beat()
            try:
                if reconnect_stuck_session(entry_pct, disconnect_pct, log=log):
                    reconnected_count += 1
                else:
                    if log:
                        log.warning("Reconnect sequence did not complete for %r "
                                    "(manager missing or focus failed)", sess_title)
                    print(f"   !! Reconnect sequence did not complete for {sess_title}")
            except Exception:
                if log:
                    log.exception("Reconnect cycle failed for %r", sess_title)
    else:
        if log:
            log.error("Reconnect cycle NOT configured (rdp.user1_title/user2_title + "
                      "disconnect_point_pct required in regions.yaml) — skipping cycle")
        print("   !! Reconnect cycle not configured — fill rdp.user1_title/user2_title "
              "+ disconnect_point_pct in regions.yaml")

    # (2) Wait, then verify both reopened; retry the clicks for any that didn't.
    if reconnect_mode or not find_rdp_windows(title_search):
        if log:
            log.info("Waiting %.0fs for RDP window(s) to reopen...", reopen_wait)
        print(f"   Waiting {reopen_wait:.0f}s for RDP to reopen...")
        _sleep_with_beat(reopen_wait, beat=beat)

    if reconnect_mode:
        for sess_title, entry_pct in configured_sessions:
            for attempt in range(1, RETRY_ATTEMPTS + 1):
                if _session_present(sess_title, [t for _, t in find_rdp_windows(title_search)]):
                    break
                if log:
                    log.warning("Session %r not open — retry %d/%d (disconnect+reconnect)",
                                sess_title, attempt, RETRY_ATTEMPTS)
                print(f"   !! {sess_title} not open — retry {attempt}/{RETRY_ATTEMPTS}")
                _beat()
                try:
                    reconnect_stuck_session(entry_pct, disconnect_pct, log=log)
                except Exception:
                    if log:
                        log.exception("Retry reconnect failed for %r", sess_title)
                _sleep_with_beat(reopen_wait, beat=beat)

    # (3) Reposition both windows to opposite corners once present.
    reappeared = find_rdp_windows(title_search)
    if len(reappeared) >= 2:
        reposition_rdp_windows_to_corners(reappeared, log)
    elif log:
        log.warning("Only %d RDP window(s) present after retries — skipping reposition",
                    len(reappeared))

    # (4) Ensure each user's Watchdog is alive; start any that aren't.
    _beat()
    ensure_watchdogs_running(log)

    # (5) Host-dead safety net: nothing reopened AND host process dead -> relaunch.
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
            _beat()
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
        log.info("RDP tick result: reconnected=%d, relaunched_host=%s",
                 reconnected_count, relaunched_host)
    return reconnected_count, relaunched_host


if __name__ == "__main__":
    # Manual test: run ONE RDP maintenance tick — the same routine Boot runs.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    cycle_or_recover_rdp_windows()