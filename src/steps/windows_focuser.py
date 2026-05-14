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