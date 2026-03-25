from winops import set_dpi_awareness
set_dpi_awareness()

import time
import os
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
import win32gui
import win32con
import pyautogui
import numpy as np
import cv2
import subprocess
import psutil
import win32console
import ctypes
import csv
import io
import random
import win32process
import win32ui
from ctypes import windll
from ocr import ocr_log_text
from utils import load_yaml, setup_logger
from window_connector import find_hwnd_by_title_substring
from layout import normalize_window_bottom_right
from auto_updater import check_updates, get_status
from pathlib import Path


def exe_dir() -> str:
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = exe_dir()
APP_CFG_PATH = os.path.join(BASE, "config", "app.yaml")
REGIONS_CFG_PATH = os.path.join(BASE, "config", "regions.yaml")


# Matches log entries in the format "HH:MM | message"
# Requires a pipe (or OCR variant) after the timestamp to avoid matching random numbers in messages
ENTRY_RE = re.compile(
    r"(?P<hh>[012]?\d)\s*:\s*(?P<mm>[0-5]\d)\s*[|\u00a6\uff5c\u4e28Il]\s*(?P<msg>.+?)(?=(?:[012]?\d)\s*:\s*[0-5]\d\s*[|\u00a6\uff5c\u4e28Il]|\Z)",
    re.DOTALL,
)

# Pattern for "warm up"
WARM_WORD_RE = re.compile(r"\bwarm[\s\-]*up\b", re.IGNORECASE)

def normalize_for_match(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("]", " ").replace("[", " ").replace("–", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalize_text_for_parsing(text: str) -> str:
    """
    BULLETPROOF: Normalize OCR text before parsing.
    Handles all common OCR misreads of pipe and timestamp characters.
    """
    if not text:
        return ""
    
    # Replace various pipe-like characters with standard pipe
    text = text.replace("｜", "|")  # Full-width pipe
    text = text.replace("¦", "|")   # Broken bar
    text = text.replace("丨", "|")  # CJK vertical line
    
    # OCR reads colon as period — only fix when followed by pipe (actual timestamp, not random N.N in messages)
    text = re.sub(r'\b(\d{1,2})\.(\d{2})\s*([|\u00a6\uff5c\u4e28Il])', r'\1:\2 \3', text)
    
    # IMPROVED: Handle "I" and "l" as pipe in timestamp context
    # Matches patterns like: "08:15I msg", "08:15 I msg", "8:5I msg"
    text = re.sub(r'(\d{1,2}:\d{1,2})\s*I\s+', r'\1 | ', text)  # I after timestamp
    text = re.sub(r'(\d{1,2}:\d{1,2})\s+l\s+', r'\1 | ', text)  # lowercase l
    
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

def latest_msg_is_warm(msg: str) -> bool:
    m = normalize_for_match(msg)
    return bool(WARM_WORD_RE.search(m))

def minutes_since_hhmm(hh: int, mm: int) -> float:
    now = datetime.now()
    last = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if last > now:
        last -= timedelta(days=1)
    return (now - last).total_seconds() / 60.0

def client_origin_screen(hwnd: int) -> Tuple[int, int]:
    return win32gui.ClientToScreen(hwnd, (0, 0))

def capture_window_region_api(hwnd: int, x: int, y: int, w: int, h: int) -> np.ndarray:
    """
    Capture window region using Windows API.
    
    CRITICAL FIX: Uses GetDC (client area) instead of GetWindowDC (entire window).
    This ensures coordinates are relative to CLIENT area, matching our percentage calculations.
    """
    try:
        # FIXED: Use GetDC (client area) not GetWindowDC (includes title bar)
        hwndDC = win32gui.GetDC(hwnd)  # ← Changed from GetWindowDC
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)
        
        # BitBlt from client area (x,y are now correct!)
        saveDC.BitBlt((0, 0), (w, h), mfcDC, (x, y), win32con.SRCCOPY)
        
        bmpstr = saveBitMap.GetBitmapBits(True)
        img = np.frombuffer(bmpstr, dtype=np.uint8)
        img.shape = (h, w, 4)
        
        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)
        
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    except Exception:
        return None


def capture_logbox_client(hwnd: int, log_region: dict) -> np.ndarray:
    """
    CRITICAL FIX: Properly handles both Windows API and screen capture coordinates.
    
    The log_region dict contains PIXEL coordinates (already calculated from percentage).
    These are CLIENT-relative coordinates (relative to window top-left).
    """
    x = int(log_region["x"])
    y = int(log_region["y"])
    w = int(log_region["w"])
    h = int(log_region["h"])
    
    # CRITICAL FIX: Always focus window before screenshot
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception as e:
        print(f"⚠️  Warning: Could not focus window: {e}")
    
    # Try Windows API first
    # capture_window_region_api expects CLIENT coordinates (x, y relative to window)
    img = capture_window_region_api(hwnd, x, y, w, h)
    if img is not None:
        return img
    
    # Fallback: pyautogui requires SCREEN coordinates
    # Convert client coords to screen coords
    cx, cy = client_origin_screen(hwnd)
    screen_left = cx + x
    screen_top = cy + y
    shot = pyautogui.screenshot(region=(screen_left, screen_top, w, h))
    return cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)


def scroll_logbox_to_top(hwnd: int, regions: dict, verbose: bool = True) -> bool:
    """
    FIXED: Scroll logbox to TOP (where latest messages appear in FSM Panel).
    
    FSM Panel shows messages in REVERSE order:
    - TOP = Latest messages (newest)
    - BOTTOM = Old messages (oldest)
    
    This is opposite of normal logs!
    
    Double-clicks the scroll bar near TOP to jump there.
    
    Config in regions.yaml:
        log_scroll_point_pct:
            x: 0.95    # Right side (scroll bar location)
            y: 0.08    # TOP area (where latest messages are!)
    
    Args:
        hwnd: Window handle
        regions: Config dict with log_scroll_point_pct
        verbose: Print debug info (default True for visibility)
    
    Returns:
        True if scroll attempted, False if not configured
    """
    scroll_cfg = regions.get("log_scroll_point_pct")
    
    if not scroll_cfg:
        if verbose:
            print("⏭️  Auto-scroll disabled (log_scroll_point_pct not configured)")
        return False
    
    try:
        # Get window size
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        cw = cr - cl
        ch = cb - ct
        cx, cy = client_origin_screen(hwnd)
        
        # Calculate scroll position
        x_pct = float(scroll_cfg.get("x", 0.95))
        y_pct = float(scroll_cfg.get("y", 0.08))  # TOP for FSM Panel!
        
        x = cx + int(cw * x_pct)
        y = cy + int(ch * y_pct)
        
        print(f"📜 Scrolling to TOP (where latest messages are)...")
        print(f"   Position: ({x_pct:.2f}, {y_pct:.2f}) → screen ({x}, {y})")
        
        # Focus window first
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        
        # VISUAL: Move mouse slowly so you can see it
        print(f"   🖱️  Moving mouse to scroll bar...")
        pyautogui.moveTo(x, y, duration=0.5)  # Slow move (0.5s) for visibility
        time.sleep(0.2)  # Pause so you can see where it is
        
        # Double-click scroll bar to jump to TOP
        print(f"   🖱️  Double-clicking scroll bar...")
        pyautogui.doubleClick()
        
        # Wait for scroll animation
        print(f"   ⏳ Waiting for scroll animation...")
        time.sleep(0.5)  # Longer wait to see scroll happen
        
        print(f"   ✅ Scroll complete!")
        return True
        
    except Exception as e:
        print(f"   ❌ Scroll failed: {e}")
        return False

def run_panel_first_run_if_needed(hwnd: int, regions: dict, log=None, force: bool = False) -> bool:
    """
    Runs first-run onboarding clicks for panel if OCR detects first-run keywords
    OR if you want it unconditional after launch.
    
    Returns:
        True if clicks completed successfully, False if failed
    """
    panel = (regions or {}).get("panel") or {}
    fr = panel.get("first_run") or {}
    clicks = fr.get("clicks") or []
    if not clicks:
        return True  # No clicks needed = success

    # Wait AFTER launch so UI has time to show onboarding
    initial_wait = fr.get("initial_wait_seconds", 10)
    print(f"⏳ Waiting {initial_wait}s for first-run screen...")
    time.sleep(initial_wait)

    # CRITICAL FIX: Force focus with verification
    print("🎯 Forcing window to foreground...")
    for attempt in range(3):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            
            # VERIFY focus was gained
            fg = win32gui.GetForegroundWindow()
            if fg == hwnd:
                print("   ✅ Window focused successfully")
                break
            else:
                print(f"   ⚠️  Focus attempt {attempt+1}/3 failed, retrying...")
                if attempt < 2:
                    win32gui.BringWindowToTop(hwnd)
                    time.sleep(0.3)
        except Exception as e:
            if log:
                log.warning("Focus attempt %d failed: %s", attempt+1, e)
            if attempt < 2:
                time.sleep(0.3)
    
    # Final verification
    if win32gui.GetForegroundWindow() != hwnd:
        print("❌ Failed to focus window after 3 attempts")
        if log:
            log.error("Could not focus panel window for first-run clicks")
        return False  # Failed
    
    time.sleep(0.5)  # Extra settle time

    # OPTIONAL (recommended): detect first-run screen by OCR
    detect = fr.get("detect_region")
    keywords = [k.lower() for k in (fr.get("keywords") or [])]

    should_click = force  # If force=True, always click
    
    if not force:
        # Only do OCR detection if not forced
        if detect and keywords:
            # convert pct region -> px region and OCR it
            cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
            cw = cr - cl
            ch = cb - ct
            region_px = {
                "x": int(float(detect["x"]) * cw),
                "y": int(float(detect["y"]) * ch),
                "w": int(float(detect["w"]) * cw),
                "h": int(float(detect["h"]) * ch),
            }
            img = capture_logbox_client(hwnd, region_px)
            txt = (ocr_log_text(img) or "").lower()
            should_click = any(k in txt for k in keywords)

    if not should_click:
        if log: log.info("Panel first-run NOT detected, skipping clicks.")
        print("⏭️  First-run screen not detected, skipping clicks")
        return

    if log: log.info("Panel first-run detected (or forced). Running clicks...")
    print(f"🖱️  Running {len(clicks)} first-run click(s)...")

    # click sequence (pct coords)
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    cw = cr - cl
    ch = cb - ct
    cx, cy = client_origin_screen(hwnd)

    for i, step in enumerate(clicks, 1):
        # CRITICAL FIX: Re-focus if lost, don't abort
        fg = win32gui.GetForegroundWindow()
        if fg != hwnd:
            print(f"   ⚠️  Window lost focus before click {i}, re-focusing...")
            if log:
                log.warning("Panel lost focus before click %d, re-focusing", i)
            
            # Try to regain focus
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.5)
                
                # Verify we got it back
                if win32gui.GetForegroundWindow() != hwnd:
                    print(f"   ❌ Failed to regain focus, aborting remaining clicks")
                    if log:
                        log.error("Could not regain focus, aborting first-run clicks")
                    return False  # Failed
                    
                print(f"   ✅ Focus regained, continuing...")
            except Exception as e:
                if log:
                    log.error("Exception regaining focus: %s", e)
                return False  # Failed
    
        x_pct = float(step.get("x_pct", step.get("x")))
        y_pct = float(step.get("y_pct", step.get("y")))
        wait_s = float(step.get("wait_s", step.get("wait", 0.8)))
    
        x = cx + int(cw * x_pct)
        y = cy + int(ch * y_pct)
    
        print(f"   Click {i}/{len(clicks)}: ({x_pct:.4f}, {y_pct:.4f}) -> screen ({x}, {y})")
        
        # ANTI-SPAM: Use jitter to prevent Windows ignoring repeated clicks
        pyautogui.moveRel(1, 0, duration=0)
        pyautogui.moveRel(-1, 0, duration=0)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click()
        
        # Wait after click
        time.sleep(wait_s)
        
        # ANTI-SPAM: Minimum cooldown to prevent accidental double-clicks
        min_cooldown = 0.3
        if wait_s < min_cooldown:
            time.sleep(min_cooldown - wait_s)
    
    print("✅ First-run clicks complete!")
    return True  # Success!

def is_process_running(image_name: str) -> bool:
    """Windows-only: returns True if a process with this exact image name is running."""
    if not image_name:
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image_name}"],
            text=True,
            errors="ignore"
        )
        # If no tasks match, tasklist outputs: INFO: No tasks are running...
        if "No tasks are running" in out:
            return False
        # Parse CSV rows; first column is image name
        reader = csv.reader(io.StringIO(out))
        for row in reader:
            if row and row[0].strip('"').lower() == image_name.lower():
                return True
        return False
    except Exception:
        return False

def launch_steam_route_if_configured(regions, log=None):
    """Launch Steam Route if configured and not already running."""
    cfg = (regions or {}).get("steam_route") or {}
    if not cfg.get("launch_with_panel", False):
        return

    proc = cfg.get("process_name")
    exe = cfg.get("exe")
    
    # Check if already running
    if proc and is_process_running(proc):
        print(f"✅ Steam Route already running ({proc}), skipping launch")
        if log: log.info("Steam Route already running (%s). Skipping launch.", proc)
        return
    
    # Validate exe path
    if not exe:
        if log: log.warning("steam_route.launch_with_panel true but steam_route.exe missing")
        print("⚠️  Steam Route exe not configured in regions.yaml")
        return
    
    if not os.path.exists(exe):
        if log: log.warning("Steam Route exe not found: %r", exe)
        print(f"⚠️  Steam Route exe not found: {exe}")
        return

    # Launch it
    try:
        print(f"🚀 Launching Steam Route: {exe}")
        
        # Get working directory for Steam Route
        exe_dir = os.path.dirname(exe)
        
        subprocess.Popen(
            [exe], 
            cwd=exe_dir,
            shell=False
        )
        
        if log: log.info("Launched Steam Route: %s", exe)
        print("✅ Steam Route launched successfully")
        
    except Exception as e:
        if log: log.error("Failed to launch Steam Route: %s", e)
        print(f"❌ Failed to launch Steam Route: {e}")


def find_latest_entry(text: str, debug=False) -> Tuple[Optional[float], Optional[int], Optional[int], Optional[str], Optional[str]]:
    """
    Parse OCR text for timestamped log entries (HH:MM | message).
    Returns the entry whose timestamp is closest to the PC's current local time.
    No entries are skipped — even very old timestamps are considered, because
    if that's the only entry visible it tells us the log is stale.
    """
    if not text:
        return None, None, None, None, None

    text = normalize_text_for_parsing(text)

    best_minutes = None
    best_hh = None
    best_mm = None
    best_line = None
    best_msg = None

    all_matches = []

    for m in ENTRY_RE.finditer(text):
        hh = int(m.group("hh"))
        mm = int(m.group("mm"))
        msg = (m.group("msg") or "").strip()

        # Skip empty messages
        if len(msg) < 2:
            continue

        mins = minutes_since_hhmm(hh, mm)

        if debug:
            all_matches.append((hh, mm, mins, msg[:50]))

        # Keep the entry closest to now (most recent)
        if best_minutes is None or mins < best_minutes:
            best_minutes = mins
            best_hh = hh
            best_mm = mm
            compact_msg = re.sub(r"\s+", " ", msg).strip()
            best_line = f"{hh:02d}:{mm:02d} | {compact_msg}"
            best_msg = msg

    # Debug output
    if debug and all_matches:
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"   Found {len(all_matches)} timestamp(s) (PC time: {now_str}):")
        for hh, mm, mins, msg in all_matches[:5]:
            marker = ">>>" if (best_hh == hh and best_mm == mm) else "   "
            print(f"   {marker} {hh:02d}:{mm:02d} ({mins:.1f} min ago) - {msg}")

    return best_minutes, best_hh, best_mm, best_line, best_msg


def trigger_recovery_action(hwnd: int, log, app, reason: str):
    log.warning("Recovery triggered: %s", reason)

    settle_click_ms = int(app["watchdog"].get("settle_after_click_ms", 2000))

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.3)

    regions = load_yaml(REGIONS_CFG_PATH)
    if "button_point_pct" in regions:
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        cw = cr - cl
        ch = cb - ct
        cx, cy = client_origin_screen(hwnd)
        b = regions["button_point_pct"]
        x = cx + int(cw * float(b["x"]))
        y = cy + int(ch * float(b["y"]))
    elif "button_point" in regions:
        b = regions["button_point"]
        cx, cy = client_origin_screen(hwnd)
        x = cx + int(b["x"])
        y = cy + int(b["y"])
    else:
        log.error("No button_point or button_point_pct in config.")
        return

    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.click()
    time.sleep(settle_click_ms / 1000)
    print(f"Recovery click at ({x}, {y})")
    log.info("Recovery click at (%d, %d)", x, y)


def resolve_panel_exe(regions: dict) -> Optional[str]:
    """
    IMPROVED: Smart panel exe detection with priority logic
    
    Priority:
    1. If "Panel.exe" exists → use it (most PCs)
    2. If not, find newest .exe in folder (PCs with changing names)
    
    Returns:
        Full path to panel exe, or None if not found
    """
    panel = (regions or {}).get("panel") or {}
    panel_dir = panel.get("dir")
    
    if not panel_dir:
        print("❌ panel.dir not configured in regions.yaml")
        return None

    d = Path(panel_dir)
    if not d.exists():
        print(f"❌ Panel directory does not exist: {panel_dir}")
        return None

    # PRIORITY 1: Check for Panel.exe specifically
    panel_exe_path = d / "Panel.exe"
    if panel_exe_path.exists():
        print(f"✅ Found Panel.exe")
        return str(panel_exe_path)
    
    # PRIORITY 2: Find any .exe files (for PCs with changing names)
    exes = list(d.glob("*.exe"))
    
    if not exes:
        print(f"❌ No .exe files found in: {panel_dir}")
        return None

    # Sort by modification time (newest first)
    exes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    
    newest_exe = str(exes[0])
    
    # Show what we found
    print(f"⚠️  Panel.exe not found, using newest .exe:")
    print(f"   📁 Found {len(exes)} .exe file(s) in {panel_dir}")
    for i, exe in enumerate(exes[:3], 1):  # Show top 3
        mtime = datetime.fromtimestamp(exe.stat().st_mtime)
        marker = "⭐" if i == 1 else "  "
        print(f"   {marker} {exe.name} (modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')})")
    if len(exes) > 3:
        print(f"   ... and {len(exes) - 3} more")
    
    print(f"✅ Will launch: {newest_exe}")
    
    return newest_exe


def _query_full_process_image_name(pid: int) -> str:
    """Return full exe path for a PID, or empty string."""
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        buf = ctypes.create_unicode_buffer(2048)
        size = ctypes.c_ulong(len(buf))
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(handle)
        return buf.value if ok else ""
    except Exception:
        return ""


def find_window_by_process_path(dir_substring: str) -> Tuple[Optional[int], Optional[str]]:
    """
    FALLBACK: Find window by process directory.
    Useful if title search fails for some reason.
    
    Args:
        dir_substring: Part of the directory path (e.g., "FSM_PANEL v.2.8.0")
    
    Returns:
        (hwnd, title) or (None, None)
    """
    dir_sub_lower = dir_substring.lower()
    matches = []
    
    def enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe_path = _query_full_process_image_name(pid)
            if exe_path and dir_sub_lower in exe_path.lower():
                title = win32gui.GetWindowText(hwnd) or ""
                matches.append((hwnd, title))
        except Exception:
            pass
    
    win32gui.EnumWindows(enum_handler, None)
    return matches[0] if matches else (None, None)

def restart_explorer(log=None):
    """Restart Windows Explorer for the CURRENT user session only.

    Uses taskkill with USERNAME filter so dual-session PCs don't
    kill the other user's explorer (which causes a black screen).
    """

    print("🔄 Restarting Explorer (current user only)...")
    if log:
        log.warning("Restarting explorer.exe for current user")

    try:
        username = os.environ.get("USERNAME", "")
        if username:
            cmd = ["taskkill", "/F", "/FI", f"IMAGENAME eq explorer.exe",
                   "/FI", f"USERNAME eq {username}"]
        else:
            # Fallback: kill by PID of current session's explorer
            my_session = _current_session_id()
            explorer_pids = []
            for proc in psutil.process_iter(['name', 'pid']):
                try:
                    if proc.info['name'] and proc.info['name'].lower() == 'explorer.exe':
                        if my_session is not None:
                            sess = ctypes.c_ulong()
                            if ctypes.windll.kernel32.ProcessIdToSessionId(
                                    proc.info['pid'], ctypes.byref(sess)):
                                if sess.value != my_session:
                                    continue
                        explorer_pids.append(str(proc.info['pid']))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if explorer_pids:
                for pid in explorer_pids:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10
                    )
                time.sleep(1)
                subprocess.Popen(["explorer.exe"], shell=False)
                time.sleep(2)
                print("✅ Explorer restarted (PID fallback)")
                if log:
                    log.info("Explorer restarted (PID fallback)")
                return
            else:
                if log:
                    log.warning("No explorer.exe found for current session")
                return

        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        time.sleep(1)

        subprocess.Popen(["explorer.exe"], shell=False)
        time.sleep(2)

        print("✅ Explorer restarted")
        if log:
            log.info("Explorer restarted")

    except Exception as e:
        print(f"❌ Failed: {e}")
        if log:
            log.error("Explorer restart failed: %s", e)


def _current_session_id():
    """Return the Windows session ID for the current process."""
    try:
        pid = os.getpid()
        session = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
            return session.value
    except Exception:
        pass
    return None


def is_launch_in_progress(hwnd: int, regions: dict, log=None, max_age_minutes: float = 10.0) -> bool:
    """
    OCR the log box and check if there's a recent "Launching" message.
    Returns True if a "Launching" entry exists and is younger than max_age_minutes.
    """
    try:
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        client_w = cr - cl
        client_h = cb - ct

        r = regions.get("logbox_full_pct")
        if not r:
            if log:
                log.warning("logbox_full_pct not configured in regions.yaml")
            return False
        log_region = {
            "x": int(r["x"] * client_w),
            "y": int(r["y"] * client_h),
            "w": int(r["w"] * client_w),
            "h": int(r["h"] * client_h),
        }

        img = capture_logbox_client(hwnd, log_region)
        text = normalize_text_for_parsing((ocr_log_text(img) or "").strip())

        # Search all timestamped entries for one containing "launching"
        for m in ENTRY_RE.finditer(text):
            msg = (m.group("msg") or "").strip()
            if "launching" not in msg.lower():
                continue
            hh = int(m.group("hh"))
            mm = int(m.group("mm"))
            mins = minutes_since_hhmm(hh, mm)
            if log:
                log.info("Found 'Launching' at %02d:%02d (%.1f min ago)", hh, mm, mins)
            print(f"Found 'Launching' at {hh:02d}:{mm:02d} ({mins:.1f}m ago)")
            if mins < max_age_minutes:
                return True

    except Exception as e:
        if log:
            log.warning("is_launch_in_progress OCR failed: %s", e)
    return False


def count_cs2_instances():
    """Count CS2 instances in the CURRENT user session only.

    On multi-user PCs (2 users, 4 CS2 each = 8 total) we must only
    count the 4 that belong to our session, not all 8.
    """
    my_session = _current_session_id()
    count = 0
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == 'cs2.exe':
                if my_session is not None:
                    sess = ctypes.c_ulong()
                    if ctypes.windll.kernel32.ProcessIdToSessionId(proc.info['pid'], ctypes.byref(sess)):
                        if sess.value != my_session:
                            continue
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return count


def check_cs2_instance_count(hwnd, regions, expected=4, log=None):
    """
    Check if exactly 4 CS2 instances running.
    If not, click kill_all_cs2 button and re-run first-run clicks.
    """
    count = count_cs2_instances()

    if count == expected:
        if log:
            log.info("CS2 count OK: %d", count)
        return True

    if log:
        log.warning("CS2 count wrong: %d (expected %d)", count, expected)

    # Check if a launch is already in progress (< 10 min old)
    if is_launch_in_progress(hwnd, regions, log=log, max_age_minutes=10.0):
        print(f"CS2: {count}/{expected} - launch in progress, skipping fix")
        if log:
            log.info("Skipping CS2 fix: 'Launching' message is recent (< 10 min)")
        return False

    print(f"CS2: {count}/{expected} - fixing...")

    kill_button = regions.get("kill_all_cs2_point_pct")
    if not kill_button:
        if log:
            log.error("kill_all_cs2_point_pct not configured")
        return False

    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.5)

        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        cx, cy = client_origin_screen(hwnd)
        x = cx + int((cr - cl) * float(kill_button["x"]))
        y = cy + int((cb - ct) * float(kill_button["y"]))

        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click()
        time.sleep(10)

        run_panel_first_run_if_needed(hwnd, regions, log=log, force=True)
        print(f"CS2 fix complete")
        return False

    except Exception as e:
        if log:
            log.error("CS2 fix failed: %s", e)
        return False


def reposition_console_window():
    """Reposition console to bottom-left corner"""
    try:
        console_hwnd = win32console.GetConsoleWindow()
        if not console_hwnd:
            return
        
        work_area = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work_area), 0)
        
        console_width = 800
        console_height = 400
        
        x = work_area.left
        y = work_area.bottom - console_height
        
        print(f"📐 Repositioning console to bottom-left ({x}, {y})...")
        
        win32gui.SetWindowPos(
            console_hwnd,
            win32con.HWND_TOP,
            x, y,
            console_width, console_height,
            win32con.SWP_SHOWWINDOW
        )
        
        print("✅ Console repositioned\n")
        
    except Exception as e:
        print(f"⚠️  Console reposition failed: {e}\n")

def run_watchdog() -> None:
    log = setup_logger()

    # Reposition console BEFORE launching anything
    reposition_console_window()
    
    app = load_yaml(APP_CFG_PATH)

    title_sub = app["window"]["title_substring"]
    width = int(app["layout"]["width"])
    height = int(app["layout"]["height"])
    margin_right = int(app["layout"].get("margin_right", 0))
    margin_bottom = int(app["layout"].get("margin_bottom", 0))

    poll = int(app["watchdog"]["poll_seconds"])
    general_timeout = float(app["watchdog"].get("general_timeout_minutes", 60))
    debounce = int(app["watchdog"].get("action_debounce_seconds", 180))

    debug_print_ocr = bool(app["watchdog"].get("debug_print_ocr", False))
    save_last_log_image = bool(app["watchdog"].get("save_last_log_image", True))
    settle_norm_ms = int(app["watchdog"].get("settle_after_normalize_ms", 150))
    settle_focus_ms = int(app["watchdog"].get("settle_after_focus_ms", 200))

    # Initialize normalize flag based on config
    normalize_every = bool(app["watchdog"].get("normalize_every_loop", True))

    os.makedirs("logs", exist_ok=True)
    hwnd = None
    last_found_title = None
    last_action_ts = 0.0
    last_logged_latest_line = None
    steam_route_launched = False
    first_run_completed_pids = set()  # Track PIDs we've already run first-run on
    last_explorer_restart_ts = time.time()  # Periodic explorer restart every 30 min
    last_cs2_check_ts = 0.0  # CS2 instance check every 5 min
    # Stagger first update check by 0-120s so dual-session users don't download simultaneously
    _update_stagger = random.randint(0, 120)
    last_update_check_ts = time.time() - 3600 + _update_stagger

    print(f"\nWatchdog started | timeout={general_timeout}m poll={poll}s\n")

    log.info("Starting watchdog - Timeout: %.1f min, Poll: %ds", general_timeout, poll)

    # OPTIMIZATION: Load regions once at startup (not every loop)
    regions = load_yaml(REGIONS_CFG_PATH)
    loop_count = 0

    while True:
        loop_count += 1

        # === PERIODIC AUTO-UPDATE CHECK (every 1 hour) ===
        if time.time() - last_update_check_ts >= 3600:
            try:
                log.info("Auto-update: checking...")
                update_result = check_updates()
                log.info(f"Auto-update result: {update_result}")
                if update_result and update_result.get('error'):
                    error_msg = update_result['error']
                    if 'Interval' not in error_msg and 'Disabled' not in error_msg and 'Dev mode' not in error_msg:
                        log.warning(f"Auto-update failed: {error_msg}")
            except Exception as e:
                log.warning(f"Auto-update exception: {e}")
            last_update_check_ts = time.time()

        # Check window
        if hwnd is None or not win32gui.IsWindow(hwnd):
            hwnd, last_found_title = find_hwnd_by_title_substring(title_sub)

            if not hwnd:
                print(f"Window not found. Launching panel...")
                log.warning("Window not found. Launching panel.exe")

                try:
                    regions = load_yaml(REGIONS_CFG_PATH)
                    panel_exe = resolve_panel_exe(regions)

                    if not panel_exe:
                        log.error("Panel EXE not found in panel.dir")
                        print("Panel EXE not found. Check regions.yaml -> panel.dir")
                        time.sleep(5)
                        continue

                    log.warning("Launching Panel: %s", panel_exe)
                    exe_dir = os.path.dirname(panel_exe)
                    process = subprocess.Popen([panel_exe], cwd=exe_dir, shell=False)
                    print(f"Launched panel (PID: {process.pid})")

                    # if not steam_route_launched:
                    #     launch_steam_route_if_configured(regions, log=log)
                    #     steam_route_launched = True

                except Exception as e:
                    log.exception("Failed to launch panel.exe: %s", e)
                    print(f"Panel launch failed: {e}")
                    time.sleep(5)
                    continue

                # Progressive retry to find window
                retry_delays = [3, 5, 8, 12]
                panel_dir = regions.get("panel", {}).get("dir", "")

                for attempt, delay in enumerate(retry_delays, 1):
                    time.sleep(delay)
                    hwnd, last_found_title = find_hwnd_by_title_substring(title_sub)
                    if hwnd:
                        break
                    if panel_dir:
                        hwnd, last_found_title = find_window_by_process_path(panel_dir)
                        if hwnd:
                            break

                if not hwnd:
                    print("Panel window not found after retries")
                    log.error("Panel window not found after progressive retries")
                    time.sleep(5)
                    continue

                print(f"Window found: {last_found_title}")
                log.info("Window found after launch: hwnd=%s title=%r", hwnd, last_found_title)

                # First-run setup
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)

                    if pid not in first_run_completed_pids:
                        for attempt in range(1, 4):
                            log.info("First-run attempt %d/3 for PID %d", attempt, pid)
                            success = run_panel_first_run_if_needed(hwnd, regions, log=log, force=True)
                            if success:
                                first_run_completed_pids.add(pid)
                                log.info("First-run completed for PID %d", pid)
                                break
                            else:
                                log.warning("First-run attempt %d failed for PID %d", attempt, pid)
                                if attempt < 3:
                                    time.sleep(5)
                                else:
                                    log.error("All first-run attempts failed for PID %d", pid)

                except Exception as e:
                    log.warning("Could not get PID for first-run tracking: %s", e)
                    run_panel_first_run_if_needed(hwnd, regions, log=log, force=True)

                # Normalize window position
                x, y, moved = normalize_window_bottom_right(
                    hwnd, width=width, height=height,
                    margin_right=margin_right, margin_bottom=margin_bottom,
                )
                if moved:
                    log.info("Normalized window after first launch -> %d, %d", x, y)
                time.sleep(settle_norm_ms / 1000)

            else:
                log.info("Found window: hwnd=%s title=%r", hwnd, last_found_title)

        # Normalize window (if allowed)
        if normalize_every:
            x, y, moved = normalize_window_bottom_right(
                hwnd, width=width, height=height,
                margin_right=margin_right, margin_bottom=margin_bottom,
            )
            if moved:
                log.info("Normalized window -> %d, %d", x, y)
            time.sleep(settle_norm_ms / 1000)
        
        try:
            cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
            client_w = cr - cl
            client_h = cb - ct

            if "log_region_pct" in regions:
                r = regions["log_region_pct"]
                log_region = {
                    "x": int(r["x"] * client_w),
                    "y": int(r["y"] * client_h),
                    "w": int(r["w"] * client_w),
                    "h": int(r["h"] * client_h),
                }
            else:
                log_region = regions.get("log_region")
                if not log_region:
                    raise RuntimeError("Missing log_region in config.")

            img = capture_logbox_client(hwnd, log_region)

            logs_dir = os.path.join(BASE, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            cv2.imwrite(os.path.join(logs_dir, "last_log.png"), img)

            text = (ocr_log_text(img) or "").strip()

            if debug_print_ocr:
                print(f"OCR: {text[:100]}...")

        except Exception as e:
            log.exception("Capture failed: %s", e)
            time.sleep(poll)
            continue

        # Parse timestamps
        minutes_ago, hh, mm, latest_line, latest_msg = find_latest_entry(text, debug=debug_print_ocr)

        # "Cannot add" error → restart explorer
        if latest_msg and "cannot add" in latest_msg.lower():
            print(f"'Cannot add' detected, restarting explorer")
            restart_explorer(log=log)

        if minutes_ago is None:
            # Retry with screen-capture fallback
            try:
                def _norm(s: str) -> str:
                    return (s or "").replace("\uff5c", "|").replace("\xa6", "|").replace("\u4e28", "|")

                cx, cy = client_origin_screen(hwnd)
                left = cx + int(log_region["x"])
                top  = cy + int(log_region["y"])
                w    = int(log_region["w"])
                h    = int(log_region["h"])

                shot = pyautogui.screenshot(region=(left, top, w, h))
                img2 = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
                cv2.imwrite(os.path.join(logs_dir, "last_log_fallback.png"), img2)

                text2 = _norm((ocr_log_text(img2) or "").strip())
                minutes_ago, hh, mm, latest_line, latest_msg = find_latest_entry(text2, debug=debug_print_ocr)

            except Exception as e:
                log.warning("Fallback capture/OCR failed: %s", e)

        if minutes_ago is None:
            log.info("No parseable entry.")
            time.sleep(poll)
            continue

        # Only print when the latest entry changes
        if latest_line != last_logged_latest_line:
            print(f"[LOG] {latest_line} ({minutes_ago:.1f}m ago)")
            last_logged_latest_line = latest_line

        # Timeout check
        now_ts = time.time()
        since_last = now_ts - last_action_ts
        should_trigger = False
        trigger_reason = ""

        if minutes_ago >= general_timeout:
            should_trigger = True
            trigger_reason = f"Timeout ({minutes_ago:.1f} >= {general_timeout}m)"

        if should_trigger:
            if since_last < debounce:
                remaining = debounce - since_last
                log.warning("Cooldown (%ds left). %s", int(remaining), trigger_reason)
            else:
                normalize_every = False
                print(f"RECOVERY: {trigger_reason}")
                trigger_recovery_action(hwnd, log, app, trigger_reason)
                last_action_ts = now_ts
        else:
            normalize_every = bool(app["watchdog"].get("normalize_every_loop", True))

        # SteamRoute crash check (disabled)
        # if steam_route_launched:
        #     sr_cfg = regions.get("steam_route", {})
        #     sr_proc = sr_cfg.get("process_name")
        #     if sr_proc and not is_process_running(sr_proc):
        #         print("SteamRoute died, relaunching...")
        #         log.warning("SteamRoute crashed, relaunching")
        #         launch_steam_route_if_configured(regions, log=log)

        # PID tracking cleanup
        if len(first_run_completed_pids) > 20:
            first_run_completed_pids = set(list(first_run_completed_pids)[-10:])

        # Periodic explorer restart (30 min)
        if time.time() - last_explorer_restart_ts >= 30 * 60:
            log.info("Periodic explorer restart triggered")
            restart_explorer(log=log)
            last_explorer_restart_ts = time.time()

        # CS2 instance check (every 5 min)
        if time.time() - last_cs2_check_ts >= 300:
            check_cs2_instance_count(hwnd, regions, expected=4, log=log)
            last_cs2_check_ts = time.time()

        time.sleep(poll)

if __name__ == "__main__":
    run_watchdog()