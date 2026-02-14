"""
winops.py - Shared Windows automation primitives (Win32 + safe input).

Goal: keep *all* low-level window discovery/focus/coordinate/click logic in one place,
so step scripts stay short and consistent.

ENHANCEMENTS:
- Added find_window_by_process() for finding windows by process name
- Improved error handling and logging

Designed for: Windows 10/11, Python 3.10+
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, List

import win32con
import win32gui
import win32process
import pyautogui


def set_dpi_awareness() -> None:
    """Make the process DPI-aware so Windows returns real pixel coordinates."""
    try:
        # Per-monitor v2 (best)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        # Per-monitor (Win 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        # System DPI aware (legacy)
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _query_full_process_image_name(pid: int) -> str:
    """Return full exe path for a PID, or empty string."""
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        buf = ctypes.create_unicode_buffer(2048)
        size = ctypes.c_ulong(len(buf))
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(handle)
        return buf.value if ok else ""
    except Exception:
        return ""


@dataclass
class WindowMatch:
    hwnd: int
    title: str
    exe_path: str
    visible: bool


def enum_top_level_windows() -> List[int]:
    hwnds: List[int] = []

    def cb(hwnd, _):
        hwnds.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return hwnds


def find_window(
    title_substring: str,
    require_visible: bool = True,
    exe_name_contains: Optional[str] = None,
    exe_path_contains: Optional[str] = None,
) -> Optional[WindowMatch]:
    """
    Find a top-level window by title substring, optionally filtering by owning process.
    Returns the first match.
    """
    title_sub = (title_substring or "").lower()
    name_contains = (exe_name_contains or "").lower() if exe_name_contains else None
    path_contains = (exe_path_contains or "").lower() if exe_path_contains else None

    matches: List[WindowMatch] = []

    def cb(hwnd, _):
        if require_visible and not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if title_sub and title_sub not in title.lower():
            return

        exe_path = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe_path = _query_full_process_image_name(pid) or ""
        except Exception:
            exe_path = ""

        if name_contains:
            base = os.path.basename(exe_path).lower()
            if name_contains not in base:
                return

        if path_contains:
            if path_contains not in exe_path.lower():
                return

        matches.append(WindowMatch(hwnd=hwnd, title=title, exe_path=exe_path, visible=bool(win32gui.IsWindowVisible(hwnd))))

    win32gui.EnumWindows(cb, None)
    return matches[0] if matches else None


def find_window_by_process(
    process_name: str,
    title_substring: Optional[str] = None,
    require_visible: bool = True,
) -> Optional[WindowMatch]:
    """
    ENHANCED: Find a window by its owning process name.
    Optionally filter by title substring as well.
    
    Args:
        process_name: Process executable name (e.g., "Panel.exe")
        title_substring: Optional title substring filter
        require_visible: Only return visible windows
    
    Returns:
        WindowMatch or None
    """
    process_name_lower = process_name.lower()
    title_sub = (title_substring or "").lower() if title_substring else None
    
    matches: List[WindowMatch] = []
    
    def cb(hwnd, _):
        if require_visible and not win32gui.IsWindowVisible(hwnd):
            return
            
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe_path = _query_full_process_image_name(pid) or ""
            
            if not exe_path:
                return
                
            exe_name = os.path.basename(exe_path).lower()
            
            if exe_name != process_name_lower:
                return
            
            title = win32gui.GetWindowText(hwnd) or ""
            
            if title_sub and title_sub not in title.lower():
                return
            
            matches.append(WindowMatch(
                hwnd=hwnd,
                title=title,
                exe_path=exe_path,
                visible=bool(win32gui.IsWindowVisible(hwnd))
            ))
        except Exception:
            pass
    
    win32gui.EnumWindows(cb, None)
    return matches[0] if matches else None


def wait_for_window(
    title_substring: str,
    timeout_s: float = 30.0,
    poll_s: float = 0.25,
    require_visible: bool = True,
    exe_name_contains: Optional[str] = None,
    exe_path_contains: Optional[str] = None,
) -> Optional[WindowMatch]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        m = find_window(
            title_substring=title_substring,
            require_visible=require_visible,
            exe_name_contains=exe_name_contains,
            exe_path_contains=exe_path_contains,
        )
        if m and win32gui.IsWindow(m.hwnd):
            return m
        time.sleep(poll_s)
    return None


def wait_for_window_by_process(
    process_name: str,
    title_substring: Optional[str] = None,
    timeout_s: float = 30.0,
    poll_s: float = 0.25,
    require_visible: bool = True,
) -> Optional[WindowMatch]:
    """
    ENHANCED: Wait for a window to appear by process name.
    
    Args:
        process_name: Process executable name (e.g., "Panel.exe")
        title_substring: Optional title substring filter
        timeout_s: Maximum time to wait in seconds
        poll_s: Polling interval in seconds
        require_visible: Only return visible windows
    
    Returns:
        WindowMatch or None
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        m = find_window_by_process(
            process_name=process_name,
            title_substring=title_substring,
            require_visible=require_visible,
        )
        if m and win32gui.IsWindow(m.hwnd):
            return m
        time.sleep(poll_s)
    return None


def launch_exe(exe_path: str) -> None:
    if not os.path.exists(exe_path):
        raise FileNotFoundError(exe_path)
    subprocess.Popen(exe_path, shell=False)


def force_foreground(hwnd: int, tries: int = 8, sleep_s: float = 0.15) -> bool:
    """
    Hard focus: handles Windows focus restrictions better than SetForegroundWindow alone.
    Returns True if foreground == hwnd.
    """
    for _ in range(tries):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass
        try:
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

        time.sleep(sleep_s)
        if win32gui.GetForegroundWindow() == hwnd:
            return True

        # thread attach trick
        try:
            fg = win32gui.GetForegroundWindow()
            cur_tid = win32process.GetWindowThreadProcessId(fg)[0]
            tgt_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
            this_tid = ctypes.windll.kernel32.GetCurrentThreadId()

            ctypes.windll.user32.AttachThreadInput(this_tid, cur_tid, True)
            ctypes.windll.user32.AttachThreadInput(this_tid, tgt_tid, True)

            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)

            ctypes.windll.user32.AttachThreadInput(this_tid, cur_tid, False)
            ctypes.windll.user32.AttachThreadInput(this_tid, tgt_tid, False)
        except Exception:
            pass

        time.sleep(sleep_s)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    return False


def assert_foreground(hwnd: int) -> None:
    fg = win32gui.GetForegroundWindow()
    if fg != hwnd:
        fg_title = win32gui.GetWindowText(fg) or "Unknown"
        raise RuntimeError(f"Safety stop: target window not foreground. Foreground='{fg_title}' hwnd={fg}")


def client_size(hwnd: int) -> Tuple[int, int]:
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    return (cr - cl), (cb - ct)


def client_origin_screen(hwnd: int) -> Tuple[int, int]:
    return win32gui.ClientToScreen(hwnd, (0, 0))


def pct_to_screen_xy(hwnd: int, x_pct: float, y_pct: float) -> Tuple[int, int]:
    cw, ch = client_size(hwnd)
    cx, cy = client_origin_screen(hwnd)
    return cx + int(cw * x_pct), cy + int(ch * y_pct)


def safe_click(x: int, y: int, move_duration: float = 0.15) -> None:
    # tiny jitter to avoid Windows "same position" ignore in some setups
    pyautogui.moveRel(1, 0, duration=0)
    pyautogui.moveRel(-1, 0, duration=0)
    pyautogui.moveTo(x, y, duration=move_duration)
    pyautogui.click()


def safe_double_click(x: int, y: int, move_duration: float = 0.15, interval: float = 0.05) -> None:
    pyautogui.moveRel(1, 0, duration=0)
    pyautogui.moveRel(-1, 0, duration=0)
    pyautogui.moveTo(x, y, duration=move_duration)
    pyautogui.doubleClick(interval=interval)
