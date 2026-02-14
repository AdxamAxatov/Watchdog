"""
diagnose_ocr.py - Diagnose why OCR can't find log entries

This script will:
1. Capture screenshot of log region
2. Show you what OCR reads
3. Test if regex pattern matches
4. Show you the exact coordinates being captured
"""

import os
import sys
import time
import re
import cv2
import numpy as np
import pyautogui
import win32gui
import win32con

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from utils import load_yaml
from ocr import ocr_log_text
from window_connector import find_hwnd_by_title_substring

# Same pattern as watchdog
ENTRY_RE = re.compile(
    r"(?P<hh>[01]?\d|2[0-3])\s*:\s*(?P<mm>[0-5]\d)\s*\|\s*(?P<msg>.*?)(?=(?:\b[01]?\d|2[0-3])\s*:\s*[0-5]\d\s*\||\Z)",
    re.IGNORECASE | re.DOTALL,
)

def client_origin_screen(hwnd):
    return win32gui.ClientToScreen(hwnd, (0, 0))


def main():
    print("=" * 70)
    print("🔍 OCR DIAGNOSTIC TOOL")
    print("=" * 70)
    print()
    
    # Load config
    app = load_yaml("config/app.yaml")
    regions = load_yaml("config/regions.yaml")
    
    title_sub = app["window"]["title_substring"]
    
    # Find window
    print(f"Looking for window with title: '{title_sub}'...")
    hwnd, title = find_hwnd_by_title_substring(title_sub)
    
    if not hwnd:
        print("❌ Window not found!")
        print("\nTroubleshooting:")
        print("1. Is the panel running?")
        print("2. Is the window title correct in app.yaml?")
        print("3. Try running: python src/debug_active_window.py")
        return
    
    print(f"✅ Found window: {title}")
    print(f"   HWND: {hwnd}")
    print()
    
    # Focus window
    print("🎯 Focusing window...")
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        print("✅ Window focused")
    except Exception as e:
        print(f"⚠️  Could not focus: {e}")
    print()
    
    # Get client dimensions
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    client_w = cr - cl
    client_h = cb - ct
    print(f"📐 Window client size: {client_w} x {client_h}")
    print()
    
    # Calculate log region
    if "log_region_pct" in regions:
        r = regions["log_region_pct"]
        log_region = {
            "x": int(r["x"] * client_w),
            "y": int(r["y"] * client_h),
            "w": int(r["w"] * client_w),
            "h": int(r["h"] * client_h),
        }
        print(f"📍 Using log_region_pct:")
        print(f"   Percentage: x={r['x']:.4f}, y={r['y']:.4f}, w={r['w']:.4f}, h={r['h']:.4f}")
    else:
        log_region = regions.get("log_region")
        if not log_region:
            print("❌ No log_region or log_region_pct in regions.yaml!")
            return
        print(f"📍 Using log_region (absolute):")
    
    print(f"   Pixel coords: x={log_region['x']}, y={log_region['y']}, w={log_region['w']}, h={log_region['h']}")
    print()
    
    # Capture screenshot
    print("📸 Capturing screenshot...")
    cx, cy = client_origin_screen(hwnd)
    left = cx + log_region["x"]
    top = cy + log_region["y"]
    w = log_region["w"]
    h = log_region["h"]
    
    print(f"   Screen coords: ({left}, {top}) size {w}x{h}")
    
    shot = pyautogui.screenshot(region=(left, top, w, h))
    img = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
    
    # Save raw screenshot
    os.makedirs("logs", exist_ok=True)
    cv2.imwrite("logs/diagnostic_raw.png", img)
    print(f"✅ Saved raw screenshot: logs/diagnostic_raw.png")
    print()
    
    # Run OCR
    print("🔍 Running OCR...")
    text = ocr_log_text(img, debug_dir="logs/ocr_debug")
    
    print("=" * 70)
    print("📄 OCR OUTPUT:")
    print("=" * 70)
    print(text if text else "(empty)")
    print("=" * 70)
    print()
    
    # Test regex pattern
    print("🔍 Testing regex pattern for 'HH:MM | msg'...")
    matches = list(ENTRY_RE.finditer(text))
    
    if matches:
        print(f"✅ Found {len(matches)} matching entries:")
        print()
        for i, m in enumerate(matches, 1):
            hh = m.group("hh")
            mm = m.group("mm")
            msg = m.group("msg")[:50]  # First 50 chars
            print(f"   Match {i}: {hh}:{mm} | {msg}...")
    else:
        print("❌ NO MATCHES FOUND!")
        print()
        print("Possible issues:")
        print("1. Log region coordinates are wrong (capturing wrong area)")
        print("2. OCR is reading text incorrectly")
        print("3. Log format doesn't match 'HH:MM | msg' pattern")
        print("4. Text is too blurry/small for OCR")
        print()
        print("Next steps:")
        print("1. Open logs/diagnostic_raw.png - is this the log area?")
        print("2. If NO: Use calibration.py to recalibrate log_region_pct")
        print("3. If YES but text is blurry: Try increasing log region size")
        print("4. Check logs/ocr_debug/ folder for OCR preprocessing steps")
    
    print()
    print("=" * 70)
    print("DIAGNOSIS COMPLETE")
    print("=" * 70)
    print()
    print("Files created:")
    print("  - logs/diagnostic_raw.png (raw screenshot)")
    print("  - logs/ocr_debug/ (OCR processing steps)")
    print()
    
    # Show what the log should look like
    print("Expected log format examples:")
    print("  12:34 | warm up successful")
    print("  09:15 | processing complete")
    print("  23:59 | error occurred")
    print()
    print("If your log looks different, the regex might not match.")
    print("Check the actual log format in your panel window.")


if __name__ == "__main__":
    main()