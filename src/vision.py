"""
vision.py - Shared screenshot & UI-loaded heuristics.

This stays intentionally simple: no OCR here, only "is the UI rendered yet?" checks.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

import cv2
import numpy as np
import pyautogui
import win32gui


def capture_window_region_pct(hwnd: int, x_pct: float, y_pct: float, w_pct: float, h_pct: float) -> Optional[np.ndarray]:
    """
    Capture a region of the *client area* using percentages.
    Returns BGR image (OpenCV) or None.
    """
    try:
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        cw = cr - cl
        ch = cb - ct
        cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))

        x = cx + int(cw * x_pct)
        y = cy + int(ch * y_pct)
        w = max(1, int(cw * w_pct))
        h = max(1, int(ch * h_pct))

        shot = pyautogui.screenshot(region=(x, y, w, h))
        return cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def is_ui_loaded_basic(hwnd: int) -> bool:
    """
    Heuristic: capture center region and ensure it's not blank (white/black) and has variance.
    Useful for apps that show blank/loading placeholder frames right after launch.
    """
    img = capture_window_region_pct(hwnd, 0.2, 0.2, 0.6, 0.6)
    if img is None:
        return False

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean_brightness = float(gray.mean())
    if mean_brightness > 240 or mean_brightness < 15:
        return False

    std_dev = float(gray.std())
    if std_dev < 10:
        return False

    return True


def wait_for_ui_loaded(
    hwnd: int,
    max_wait_s: float = 15.0,
    check_interval_s: float = 0.5,
    required_consecutive: int = 2,
    verbose: bool = True,
) -> bool:
    """
    Wait for is_ui_loaded_basic() to be True N consecutive times.
    """
    deadline = time.time() + max_wait_s
    consecutive = 0

    if verbose:
        print(f"\n⏳ Waiting for UI to load (max {max_wait_s}s)...")

    while time.time() < deadline:
        if is_ui_loaded_basic(hwnd):
            consecutive += 1
            if verbose:
                print(f"   ✓ UI appears loaded ({consecutive}/{required_consecutive})")
            if consecutive >= required_consecutive:
                if verbose:
                    print("✅ UI is stable and loaded!")
                return True
        else:
            if consecutive > 0 and verbose:
                print("   ⚠ UI not stable, rechecking...")
            consecutive = 0

        time.sleep(check_interval_s)

    if verbose:
        print(f"⚠️  UI load timeout reached ({max_wait_s}s)")
    return False
