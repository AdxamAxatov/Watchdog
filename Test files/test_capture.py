"""
CRITICAL FIX: capture_logbox_client with full debugging

This version will show you EXACTLY what's happening:
- Window state (minimized? visible?)
- Exact coordinates being captured
- Screenshot validation
- Multiple fallback methods
"""

import win32gui
import win32con
import win32ui
import pyautogui
import cv2
import numpy as np
import time
from ctypes import windll

def client_origin_screen(hwnd):
    return win32gui.ClientToScreen(hwnd, (0, 0))


def capture_window_region_api(hwnd: int, x: int, y: int, w: int, h: int) -> np.ndarray:
    """Windows API screenshot (works on minimized windows)."""
    try:
        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)
        
        result = windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 3)
        
        if result == 0:
            saveDC.BitBlt((0, 0), (w, h), mfcDC, (x, y), win32con.SRCCOPY)
        else:
            saveDC.BitBlt((0, 0), (w, h), mfcDC, (x, y), win32con.SRCCOPY)
        
        bmpstr = saveBitMap.GetBitmapBits(True)
        img = np.frombuffer(bmpstr, dtype=np.uint8)
        img.shape = (h, w, 4)
        
        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)
        
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    except Exception as e:
        print(f"⚠️  Windows API capture failed: {e}")
        return None


def capture_logbox_client_BULLETPROOF(hwnd: int, log_region: dict) -> np.ndarray:
    """
    BULLETPROOF capture with extensive debugging and multiple fallbacks.
    """
    x = int(log_region["x"])
    y = int(log_region["y"])
    w = int(log_region["w"])
    h = int(log_region["h"])
    
    print("\n" + "=" * 70)
    print("📸 SCREENSHOT CAPTURE DEBUG")
    print("=" * 70)
    
    # 1. Check window state
    try:
        is_visible = win32gui.IsWindowVisible(hwnd)
        is_iconic = win32gui.IsIconic(hwnd)  # Minimized?
        
        print(f"Window state:")
        print(f"  HWND: {hwnd}")
        print(f"  Visible: {is_visible}")
        print(f"  Minimized: {is_iconic}")
        
        # Get window position
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        print(f"  Window rect: ({left}, {top}) to ({right}, {bottom})")
        print(f"  Window size: {right-left} x {bottom-top}")
        
        # Get client area
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        client_w = cr - cl
        client_h = cb - ct
        print(f"  Client size: {client_w} x {client_h}")
        
    except Exception as e:
        print(f"❌ Error checking window state: {e}")
        return None
    
    # 2. FORCE window to be visible and focused
    print("\n🎯 Forcing window to foreground...")
    try:
        # Restore if minimized
        if is_iconic:
            print("   Window was minimized, restoring...")
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.5)
        
        # Show and focus
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        
        # Verify focus
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            print("   ✅ Window is now in foreground")
        else:
            print(f"   ⚠️  Window not in foreground (fg={fg})")
        
    except Exception as e:
        print(f"   ⚠️  Focus failed: {e}")
    
    # 3. Calculate screen coordinates
    cx, cy = client_origin_screen(hwnd)
    screen_x = cx + x
    screen_y = cy + y
    
    print(f"\n📐 Capture coordinates:")
    print(f"  Client origin: ({cx}, {cy})")
    print(f"  Region offset: ({x}, {y})")
    print(f"  Region size: {w} x {h}")
    print(f"  Screen coords: ({screen_x}, {screen_y})")
    
    # 4. Validate coordinates are on screen
    screen_w = win32gui.GetSystemMetrics(0)  # SM_CXSCREEN
    screen_h = win32gui.GetSystemMetrics(1)  # SM_CYSCREEN
    
    print(f"  Screen size: {screen_w} x {screen_h}")
    
    if screen_x < 0 or screen_y < 0 or screen_x + w > screen_w or screen_y + h > screen_h:
        print(f"  ⚠️  WARNING: Coordinates outside screen bounds!")
        print(f"     Requested: ({screen_x}, {screen_y}) + {w}x{h}")
        print(f"     Screen: 0,0 to {screen_w},{screen_h}")
    
    # 5. Try capture methods
    img = None
    
    # METHOD 1: Windows API (works on minimized)
    print("\n🔍 Method 1: Windows API screenshot...")
    img = capture_window_region_api(hwnd, x, y, w, h)
    if img is not None:
        # Check if image is not all black
        mean_val = img.mean()
        if mean_val > 5:  # Not completely black
            print(f"   ✅ Success! (mean pixel value: {mean_val:.1f})")
            print("=" * 70 + "\n")
            return img
        else:
            print(f"   ⚠️  Image is too dark (mean: {mean_val:.1f}), trying fallback...")
    else:
        print("   ❌ Failed")
    
    # METHOD 2: pyautogui with forced focus
    print("\n🔍 Method 2: pyautogui screenshot (after focus)...")
    try:
        # Extra focus attempt
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        
        shot = pyautogui.screenshot(region=(screen_x, screen_y, w, h))
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
        
        mean_val = img.mean()
        if mean_val > 5:
            print(f"   ✅ Success! (mean pixel value: {mean_val:.1f})")
            print("=" * 70 + "\n")
            return img
        else:
            print(f"   ⚠️  Image is too dark (mean: {mean_val:.1f})")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
    
    # METHOD 3: Full window capture + crop
    print("\n🔍 Method 3: Full window capture + crop...")
    try:
        # Capture entire client area
        full_img = capture_window_region_api(hwnd, 0, 0, client_w, client_h)
        if full_img is not None:
            # Crop to log region
            img = full_img[y:y+h, x:x+w]
            mean_val = img.mean()
            if mean_val > 5:
                print(f"   ✅ Success! (mean pixel value: {mean_val:.1f})")
                print("=" * 70 + "\n")
                return img
            else:
                print(f"   ⚠️  Image is too dark (mean: {mean_val:.1f})")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
    
    print("\n❌ ALL CAPTURE METHODS FAILED!")
    print("=" * 70 + "\n")
    
    # Return a blank image so we don't crash
    print("⚠️  Returning blank image to prevent crash")
    return np.zeros((h, w, 3), dtype=np.uint8)


# USAGE EXAMPLE:
if __name__ == "__main__":
    import sys
    sys.path.insert(0, 'src')
    
    from utils import load_yaml
    from window_connector import find_hwnd_by_title_substring
    
    app = load_yaml("config/app.yaml")
    regions = load_yaml("config/regions.yaml")
    
    title_sub = app["window"]["title_substring"]
    hwnd, title = find_hwnd_by_title_substring(title_sub)
    
    if not hwnd:
        print("❌ Window not found!")
        exit(1)
    
    print(f"✅ Found window: {title}")
    
    # Get log region
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    client_w = cr - cl
    client_h = cb - ct
    
    r = regions["log_region_pct"]
    log_region = {
        "x": int(r["x"] * client_w),
        "y": int(r["y"] * client_h),
        "w": int(r["w"] * client_w),
        "h": int(r["h"] * client_h),
    }
    
    # Try capture
    img = capture_logbox_client_BULLETPROOF(hwnd, log_region)
    
    # Save result
    import os
    os.makedirs("logs", exist_ok=True)
    cv2.imwrite("logs/debug_capture.png", img)
    print(f"\n💾 Saved to: logs/debug_capture.png")
    print(f"   Image size: {img.shape}")
    print(f"   Mean pixel: {img.mean():.1f}")
    print(f"   Min/Max: {img.min()} / {img.max()}")