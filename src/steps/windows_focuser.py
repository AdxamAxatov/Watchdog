"""
steps/windows_focuser.py - Monitor RDP windows and recover hung ones

This step runs continuously after boot completes.
Every cycle it checks each RDP window:
  1. If a window is NOT RESPONDING → close it (dismiss crash dialog if needed)
  2. If a window IS responding → refocus it

The interval between cycles is configured via regions.yaml → rdp_windows.focus_interval_minutes.
"""

import os
import subprocess
import time
import win32gui
import win32con
import logging
from datetime import datetime

import pyautogui
from winops import is_window_responding, close_hung_window
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


def _dismiss_crash_dialog(log=None):
    """
    After closing a hung window, Windows may show an error/reporting dialog.
    Look for common crash dialog windows and dismiss them.
    """
    crash_titles = [
        "not responding",
        "has stopped working",
        "problem reporting",
        "windows error reporting",
    ]

    def enum_callback(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            for pattern in crash_titles:
                if pattern in title:
                    results.append((hwnd, win32gui.GetWindowText(hwnd)))
                    break

    dialogs = []
    win32gui.EnumWindows(enum_callback, dialogs)

    for hwnd, title in dialogs:
        try:
            if log:
                log.info(f"Dismissing crash dialog: {title}")
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.5)
        except Exception as e:
            if log:
                log.warning(f"Failed to dismiss dialog '{title}': {e}")


def probe_window_with_click(hwnd, title, log=None, settle_seconds: float = 2.5):
    """Interactive probe — surfaces "stuck RDP" cases that is_window_responding
    misses. The local RDP client (mstsc.exe) keeps pumping messages even when
    the underlying remote session is frozen, so the cheap API check returns
    True for a window that's actually dead.

    We bring the window forward and click its title bar. That routes through
    the OS input system (not just our message pump), which forces Windows'
    own hung-app detector to evaluate the window more thoroughly. After a
    couple seconds, an actually-stuck window has its "Not Responding" ghost
    overlay visible, and `is_window_responding` returns False.

    The click lands on the title bar (left edge, on the title text), NOT on
    the remote desktop content, so it doesn't disturb anything inside the
    RDP session — title-bar clicks are handled by the local RDP client app.

    Returns True if the window is still responsive after the probe,
    False if it revealed itself as stuck.
    """
    try:
        # Bring window to front so the click lands on this window, not
        # whatever happens to be at those screen coords.
        if not focus_window_aggressive(hwnd, title, log):
            # Can't focus — could already be stuck, but we don't want a
            # focus failure alone to be enough to close the window. Other
            # signals (is_window_responding, ghost overlay) handle that.
            return True

        rect = win32gui.GetWindowRect(hwnd)
        # Pick a point on the title bar text: ~80px right of the left edge
        # (past the system menu icon), ~8px down from the top (well inside
        # the title bar, away from the resize border). Avoids the minimize/
        # maximize/close buttons on the right.
        click_x = rect[0] + 80
        click_y = rect[1] + 8

        pyautogui.click(click_x, click_y)
        if log:
            log.info(f"Probe click on title bar of {title} at ({click_x}, {click_y})")

        # Give Windows time to update its responsiveness state. The hung-app
        # detector typically kicks in within ~2 seconds of a non-responsive
        # window receiving input.
        time.sleep(settle_seconds)

        responsive = is_window_responding(hwnd)
        if not responsive and log:
            log.warning(f"Window revealed as STUCK after probe click: {title}")
        return responsive
    except Exception as e:
        if log:
            log.warning(f"probe_window_with_click failed for {title}: {e}")
        return True  # On error, fall through — don't false-positive on bugs


def check_and_recover_window(hwnd, title, log=None):
    """
    Check if a window is responding. If hung, close it and dismiss crash dialogs.

    Returns:
        "responding" - window is fine
        "closed"     - window was hung and has been closed
        "gone"       - window no longer exists
    """
    if not win32gui.IsWindow(hwnd):
        if log:
            log.warning(f"Window no longer exists: {title}")
        return "gone"

    # Layer 1: cheap API check — catches "hard" hangs where the window
    # is already showing the ghost overlay or has stopped pumping messages.
    if not is_window_responding(hwnd):
        hang_reason = "API not responding"
    # Layer 2: interactive probe — RDP windows can stay API-responsive even
    # when the underlying remote session is frozen. The click forces a
    # deeper hung-app check that surfaces this case.
    elif not probe_window_with_click(hwnd, title, log=log):
        hang_reason = "stuck after probe click (RDP session likely frozen)"
    else:
        return "responding"

    if log:
        log.warning(f"Window HUNG ({hang_reason}): {title} (hwnd={hwnd})")
    print(f"   !! HUNG ({hang_reason}): {title}")

    # Capture diagnostics: screenshot + all visible window titles
    try:
        logs_dir = os.path.join(exe_dir(), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Full screenshot
        shot = pyautogui.screenshot()
        shot_path = os.path.join(logs_dir, f"hung_{ts}.png")
        shot.save(shot_path)
        if log:
            log.info(f"Hung screenshot saved: {shot_path}")

        # Log all visible window titles
        all_windows = []
        def _enum_all(h, results):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if t:
                    results.append(t)
        win32gui.EnumWindows(_enum_all, all_windows)
        if log:
            log.info(f"Visible windows at hang time ({len(all_windows)}):")
            for wt in all_windows:
                log.info(f"  -> {wt}")
    except Exception as e:
        if log:
            log.warning(f"Hung diagnostics failed: {e}")

    # Close the hung window
    close_hung_window(hwnd)
    if log:
        log.info(f"Sent WM_CLOSE to hung window: {title}")

    # Wait a moment for the window to close / crash dialog to appear
    time.sleep(3)

    # Dismiss any crash/error reporting dialogs
    _dismiss_crash_dialog(log)

    # Verify window is gone
    if win32gui.IsWindow(hwnd):
        # Still alive — try EndTask as a harder close
        try:
            import ctypes
            ctypes.windll.user32.EndTask(hwnd, False, True)
            if log:
                log.info(f"EndTask sent to stubborn window: {title}")
            time.sleep(2)
            _dismiss_crash_dialog(log)
        except Exception as e:
            if log:
                log.error(f"EndTask failed for {title}: {e}")

    return "closed"


def focus_window_aggressive(hwnd, title, log=None):
    """
    Focus a window using minimize->restore trick.
    Works even from background processes.
    """
    try:
        # Minimize then restore (forces focus)
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        time.sleep(0.1)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)

        # Try SetForegroundWindow
        try:
            win32gui.SetForegroundWindow(hwnd)
        except:
            pass

        time.sleep(0.2)

        # Verify
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            if log:
                log.info(f"Focused window: {title}")
            return True
        else:
            if log:
                log.warning(f"Failed to focus: {title}")
            return False

    except Exception as e:
        if log:
            log.error(f"Error focusing {title}: {e}")
        return False


def restart_watchdog_for_titles(closed_titles, log=None):
    """Kill and restart only the Watchdog(s) matching the hung window titles."""
    try:
        boot_cfg_path = os.path.join(exe_dir(), "config", "boot_update_config.yaml")
        import yaml
        with open(boot_cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        tasks = cfg.get("watchdog_tasks", [])

        for entry in tasks:
            if not isinstance(entry, dict):
                continue
            title_match = entry.get("title_contains", "")
            username = entry.get("username", "")
            task_name = entry.get("task_name", "")
            if not title_match or not username or not task_name:
                continue

            # Check if any closed window title matches this entry
            matched = any(title_match.lower() in ct.lower() for ct in closed_titles)
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


def check_and_focus_windows(title_search="SinFermera", log=None):
    """
    Find RDP windows, check each for responsiveness, and handle accordingly:
    - Hung windows → close and dismiss crash dialog
    - Healthy windows → refocus

    Returns:
        (focused_count, closed_count)
    """
    windows = find_rdp_windows(title_search)

    if not windows:
        if log:
            log.warning("No SinFermera windows found")
        print("!!  No SinFermera windows found")
        return 0, 0

    focused_count = 0
    closed_count = 0
    closed_titles = []

    for hwnd, title in windows[:2]:  # Only first 2 windows
        print(f"   Checking: {title}")

        status = check_and_recover_window(hwnd, title, log)

        if status == "closed":
            closed_count += 1
            closed_titles.append(title)
            print(f"   -> Closed hung window")
        elif status == "responding":
            print(f"   -> Responding, focusing...")
            if focus_window_aggressive(hwnd, title, log):
                print(f"   -> Focused")
                focused_count += 1
            else:
                print(f"   -> Failed to focus")
        elif status == "gone":
            print(f"   -> Window already gone")

        time.sleep(0.5)

    if log:
        log.info(f"Cycle result: focused={focused_count}, closed_hung={closed_count}, total_found={len(windows[:2])}")

    # If any hung windows were closed: wait for RDP to reopen, then kill + restart only the affected user's Watchdog
    if closed_count > 0:
        if log:
            log.info(f"Hung window(s) closed: {closed_titles} — waiting 10s for RDP to reopen...")
        print("   Waiting 10s for RDP window to reopen...")
        time.sleep(10)

        restart_watchdog_for_titles(closed_titles, log)

    return focused_count, closed_count


# ============================================================================
# Black-screen / hung RDP recovery (added)
#
# The owner's RDP session windows sometimes go fully BLACK while the local RDP
# client keeps pumping messages — so is_window_responding() returns True and
# the old hung-detection path never fires. We add a pixel-brightness check on
# each window's client area and, on each 30-min tick, close-and-reopen the
# session window(s) (the programmatic equivalent of pressing X). The RDP client
# auto-reopens them; only if NO session reappears AND the host RDP process is
# dead do we relaunch RDPClient.
#
# CS2 recovery is intentionally NOT touched here — Watchdog.exe owns it.
# ============================================================================

# Per-window short history of "was the last sample black?" so we only act after
# `blackness_consecutive` black samples in a row (avoids one-off capture glitches
# / transient loading frames). Keyed by hwnd.
_BLACK_SAMPLE_HISTORY = {}


def _sample_window_brightness(hwnd, log=None):
    """Return mean brightness (0-255) of the window's client area, or None.

    Self-contained BitBlt capture (mirrors watchdog.capture_window_region_api)
    so this module gains no dependency on watchdog.py's heavy import chain.
    Works on unfocused / partially covered windows. Returns None on any
    failure so the caller can fall back to the responsiveness check alone.
    """
    try:
        import win32ui
        import numpy as np

        l, t, r, b = win32gui.GetClientRect(hwnd)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return None

        hwndDC = win32gui.GetDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)
        saveDC.BitBlt((0, 0), (w, h), mfcDC, (0, 0), win32con.SRCCOPY)

        bmpstr = saveBitMap.GetBitmapBits(True)
        img = np.frombuffer(bmpstr, dtype=np.uint8)
        img.shape = (h, w, 4)  # BGRA

        # Mean over the BGR channels only (ignore alpha).
        mean_brightness = float(img[:, :, :3].mean())

        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)

        return mean_brightness
    except Exception as e:
        if log:
            log.warning(f"Brightness sample failed (hwnd={hwnd}): {e}")
        return None


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


def _is_black_or_hung(hwnd, title, threshold, needed_consecutive, log=None):
    """Decide if a window should be treated as black/hung.

    True when is_window_responding()==False, OR mean brightness has stayed
    below `threshold` for `needed_consecutive` samples in a row.
    """
    # Hung path first — cheap and unambiguous.
    if not is_window_responding(hwnd):
        if log:
            log.warning(f"Window not responding (hung): {title}")
        _BLACK_SAMPLE_HISTORY.pop(hwnd, None)
        return True

    brightness = _sample_window_brightness(hwnd, log=log)
    if brightness is None:
        # Capture failed — don't treat as black on a capture bug; reset streak.
        _BLACK_SAMPLE_HISTORY.pop(hwnd, None)
        return False

    is_black_now = brightness < threshold
    streak = _BLACK_SAMPLE_HISTORY.get(hwnd, 0)
    streak = streak + 1 if is_black_now else 0
    _BLACK_SAMPLE_HISTORY[hwnd] = streak

    if log:
        log.info(
            f"Brightness {brightness:.1f} (thr {threshold}) "
            f"black_now={is_black_now} streak={streak}/{needed_consecutive}: {title}"
        )

    return is_black_now and streak >= needed_consecutive


def _close_window(hwnd, title, log=None):
    """Close one RDP session window via WM_CLOSE (the X-button equivalent),
    so the RDP client auto-reopens it. Reuses the existing close primitive.
    """
    close_hung_window(hwnd)  # posts WM_CLOSE (non-blocking)
    _BLACK_SAMPLE_HISTORY.pop(hwnd, None)
    if log:
        log.info(f"Sent WM_CLOSE to RDP session window: {title}")
    print(f"   -> Closed RDP window (X): {title}")


def cycle_or_recover_rdp_windows(title_search="SinFermera", log=None):
    """Owner-approved 30-min RDP maintenance tick.

    Each tick:
      (a) sample every SinFermera RDP window -> black (brightness streak) or
          hung (is_window_responding) windows are flagged;
      (b) close flagged windows via WM_CLOSE (X) so the RDP client reopens them;
      (c) if unconditional_cycle is on and nothing was flagged, still close the
          healthy windows (owner's literal "otherwise close every 30 min");
      (d) wait reopen_wait_seconds, re-enumerate;
      (e) if NO session reappeared AND neither wfreerdp.exe nor RDPClient.exe is
          running, relaunch the host stack via steps.rdp.run().

    CS2 is left entirely to Watchdog.exe. No CS2 actions here, so no lock needed.

    Returns (closed_count, relaunched_host: bool).
    """
    cfg = load_yaml("config/regions.yaml")
    rdp_cfg = cfg.get("rdp_windows", {}) or {}
    threshold = float(rdp_cfg.get("blackness_threshold", 12))
    needed_consecutive = int(rdp_cfg.get("blackness_consecutive", 2))
    reopen_wait = float(rdp_cfg.get("reopen_wait_seconds", 12))
    unconditional = bool(rdp_cfg.get("unconditional_cycle", True))

    windows = find_rdp_windows(title_search)[:2]  # only the 2 sessions
    if not windows:
        if log:
            log.warning("No SinFermera windows found at tick start")
        print("!!  No SinFermera windows found")

    closed_count = 0
    relaunched_host = False

    # (a)+(b) close black/hung windows
    for hwnd, title in windows:
        if not win32gui.IsWindow(hwnd):
            continue
        if _is_black_or_hung(hwnd, title, threshold, needed_consecutive, log=log):
            print(f"   !! BLACK/HUNG: {title}")
            _close_window(hwnd, title, log=log)
            closed_count += 1

    # (c) healthy tick + unconditional cycle -> still close-and-reopen
    if closed_count == 0 and windows and unconditional:
        if log:
            log.info("Healthy tick — performing unconditional close-and-reopen cycle")
        print("   Healthy — unconditional 30-min close-and-reopen cycle")
        for hwnd, title in windows:
            if win32gui.IsWindow(hwnd):
                _close_window(hwnd, title, log=log)
                closed_count += 1
    elif closed_count == 0 and windows and not unconditional:
        if log:
            log.info("Healthy tick — all RDP windows OK (unconditional cycle disabled)")
        print("   Healthy — all RDP windows OK")

    if closed_count == 0 and not windows:
        # Nothing open at all — fall through to the relaunch check below.
        pass

    # (d) wait, then re-enumerate
    if closed_count > 0 or not windows:
        if log:
            log.info(f"Waiting {reopen_wait:.0f}s for RDP window(s) to reopen...")
        print(f"   Waiting {reopen_wait:.0f}s for RDP to reopen...")
        time.sleep(reopen_wait)

    reappeared = find_rdp_windows(title_search)

    # (e) relaunch host only if nothing reappeared AND host process is dead
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


def run(config=None, context=None):
    """
    Run continuous window health check and focus maintenance.

    Reads settings from regions.yaml:
    - rdp_windows.title_search: Window title to search for
    - rdp_windows.focus_interval_minutes: How often to check (default: 15)
    """
    log = logging.getLogger("boot")

    from utils import load_yaml
    cfg = load_yaml("config/regions.yaml")

    rdp_config = cfg.get("rdp_windows", {})
    title_search = rdp_config.get("title_search", "SinFermera")
    focus_interval_minutes = rdp_config.get("focus_interval_minutes", 15)
    focus_interval_seconds = focus_interval_minutes * 60

    log.info("=" * 70)
    log.info("WINDOW HEALTH CHECK + FOCUS MAINTENANCE STARTED")
    log.info(f"Monitoring '{title_search}' windows every {focus_interval_minutes} minutes")
    log.info("=" * 70)

    print("\n" + "=" * 70)
    print("WINDOW HEALTH CHECK + FOCUS MAINTENANCE ACTIVE")
    print(f"   Checking windows every {focus_interval_minutes} minutes")
    print(f"   - Hung windows will be closed automatically")
    print(f"   - Healthy windows will be refocused")
    print("   Press Ctrl+C to stop")
    print("=" * 70 + "\n")

    cycle_count = 0

    try:
        while True:
            cycle_count += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"\n[{now}] Cycle #{cycle_count}")
            log.info(f"Health check cycle #{cycle_count} starting")

            # Check responsiveness and focus healthy windows
            focused, closed = check_and_focus_windows(title_search, log)

            if closed > 0:
                print(f"!! Closed {closed} hung window(s)")
            if focused > 0:
                print(f"OK Focused {focused} window(s)")
            if focused == 0 and closed == 0:
                print(f"!! No windows to focus")

            # Calculate next check time
            next_check_time = datetime.now()
            next_check_time = next_check_time.replace(
                minute=(next_check_time.minute + focus_interval_minutes) % 60,
                second=0
            )
            next_check_str = next_check_time.strftime("%H:%M:%S")

            print(f"Next check: {next_check_str}")
            log.info(f"Next check scheduled for {next_check_str}")

            # Wait for next cycle
            time.sleep(focus_interval_seconds)

    except KeyboardInterrupt:
        log.info("Window health check stopped by user")
        print("\nWindow health check stopped")

    except Exception as e:
        log.exception("Window health check error")
        print(f"\nError: {e}")
        raise


if __name__ == "__main__":
    # For testing
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    
    run()