import win32gui
import win32con
import win32api
import pywintypes

def get_workarea_rect():
    hmon = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
    info = win32api.GetMonitorInfo(hmon)
    return info["Work"]  # (left, top, right, bottom)

def normalize_window_bottom_right(hwnd: int, width: int, height: int, margin_right=0, margin_bottom=0):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    wa_left, wa_top, wa_right, wa_bottom = get_workarea_rect()

    # bottom-right position inside work area
    x = (wa_right - width) - margin_right
    y = (wa_bottom - height) - margin_bottom

    try:
        win32gui.MoveWindow(hwnd, x, y, width, height, True)
        return x, y, True
    except pywintypes.error:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return left, top, False
