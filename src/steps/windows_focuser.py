"""
steps/focus_maintenance.py - Keep RDP windows focused every 15 minutes

This step runs continuously after boot completes.
Finds and focuses both "SinFermera" windows every 15 minutes.
"""

import time
import win32gui
import win32con
import logging
from datetime import datetime


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


def focus_both_windows(title_search="SinFermera", log=None):
    """
    Find and focus both SinFermera windows.
    
    Returns:
        Number of windows successfully focused
    """
    windows = find_rdp_windows(title_search)
    
    if not windows:
        if log:
            log.warning("No SinFermera windows found")
        print("⚠️  No SinFermera windows found")
        return 0
    
    focused_count = 0
    
    for hwnd, title in windows[:2]:  # Only first 2 windows
        print(f"🎯 Focusing: {title}")
        if focus_window_aggressive(hwnd, title, log):
            print(f"   ✅ Focused")
            focused_count += 1
        else:
            print(f"   ⚠️  Failed to focus")
        
        time.sleep(0.5)  # Small delay between focuses
    
    if log:
        log.info(f"Focused {focused_count}/{len(windows[:2])} windows")
    
    return focused_count


def run(config=None, context=None):
    """
    Run continuous focus maintenance.
    
    Reads settings from regions.yaml:
    - rdp_windows.title_search: Window title to search for
    - rdp_windows.focus_interval_minutes: How often to focus (default: 15)
    """
    log = logging.getLogger("boot")
    
    # NEW: Load config from regions.yaml
    from utils import load_yaml
    cfg = load_yaml("config/regions.yaml")
    
    rdp_config = cfg.get("rdp_windows", {})
    title_search = rdp_config.get("title_search", "SinFermera")
    focus_interval_minutes = rdp_config.get("focus_interval_minutes", 15)
    focus_interval_seconds = focus_interval_minutes * 60
    
    log.info("=" * 70)
    log.info("FOCUS MAINTENANCE STARTED")
    log.info(f"Will focus '{title_search}' windows every {focus_interval_minutes} minutes")
    log.info("=" * 70)
    
    print("\n" + "=" * 70)
    print("🔄 FOCUS MAINTENANCE ACTIVE")
    print(f"   Focusing windows every {focus_interval_minutes} minutes")
    print("   Press Ctrl+C to stop")
    print("=" * 70 + "\n")
    
    cycle_count = 0
    
    try:
        while True:
            cycle_count += 1
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print(f"\n[{now}] Cycle #{cycle_count}")
            log.info(f"Focus cycle #{cycle_count} starting")
            
            # Focus both windows
            focused = focus_both_windows(title_search, log)
            
            if focused > 0:
                print(f"✅ Focused {focused} window(s)")
            else:
                print(f"⚠️  No windows focused")
            
            # Calculate next focus time
            next_focus_time = datetime.now()
            next_focus_time = next_focus_time.replace(
                minute=(next_focus_time.minute + focus_interval_minutes) % 60,
                second=0
            )
            next_focus_str = next_focus_time.strftime("%H:%M:%S")
            
            print(f"⏳ Next focus: {next_focus_str}")
            log.info(f"Next focus scheduled for {next_focus_str}")
            
            # Wait for next cycle
            time.sleep(focus_interval_seconds)
            
    except KeyboardInterrupt:
        log.info("Focus maintenance stopped by user")
        print("\n⏹️  Focus maintenance stopped")
    
    except Exception as e:
        log.exception("Focus maintenance error")
        print(f"\n❌ Error: {e}")
        raise


if __name__ == "__main__":
    # For testing
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    
    run()