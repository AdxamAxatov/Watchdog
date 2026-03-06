import win32gui
import mss
import numpy as np
import cv2
import pytesseract
import os
import win32con
import ctypes
from ctypes import wintypes
import pyautogui
import time
from ocr import ocr_log_text



from utils import load_yaml, setup_logger
from window_connector import find_hwnd_by_title_substring

APP_CFG_PATH = "config/app.yaml"
REGIONS_CFG_PATH = "config/regions.yaml"

# 👉 Change this only if Tesseract is installed elsewhere
pytesseract.pytesseract.tesseract_cmd = r"C:\Users\Recruiter\Tesseract-OCR\tesseract.exe"

def client_origin_screen(hwnd):
    point = wintypes.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y

def main():
    log = setup_logger()
    app = load_yaml(APP_CFG_PATH)
    regions = load_yaml(REGIONS_CFG_PATH)
    mode = regions.get("capture_mode", "client")


    title_sub = app["window"]["title_substring"]
    hwnd, title = find_hwnd_by_title_substring(title_sub)
    if not hwnd:
        raise SystemExit("Window not found. Make sure the app is running.")

    log.info("Found window: hwnd=%s title=%r", hwnd, title)

    mode = regions.get("capture_mode", "client")

    if mode == "screen":
        sr = regions["log_region_screen"]
        capture_region = {
            "left": int(sr["left"]),
            "top": int(sr["top"]),
            "width": int(sr["width"]),
            "height": int(sr["height"]),
        }
    else:
        cx, cy = client_origin_screen(hwnd)
        lr = regions["log_region"]
        capture_region = {
            "left": cx + int(lr["x"]),
            "top":  cy + int(lr["y"]),
            "width": int(lr["w"]),
            "height": int(lr["h"]),
        }
    
    print("CAPTURE_MODE:", mode)
    print("CAPTURE_REGION:", capture_region)




    # Make sure window is visible and in front
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.2)

    os.makedirs("logs", exist_ok=True)

    # Full screen capture (always reliable)
    full = pyautogui.screenshot()
    full_bgr = cv2.cvtColor(np.array(full), cv2.COLOR_RGB2BGR)

    # Rectangle where we THINK the logbox is
    x = int(capture_region["left"])
    y = int(capture_region["top"])
    w = int(capture_region["width"])
    h = int(capture_region["height"])

    # Save a debug image with a green rectangle drawn on the full screen
    debug = full_bgr.copy()
    cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.imwrite("logs/full_debug.png", debug)
    print("Saved full debug image to logs/full_debug.png")

    # Crop safely (clamp to screen bounds)
    H, W = full_bgr.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(W, x + w)
    y2 = min(H, y + h)

    img = full_bgr[y1:y2, x1:x2]


    cv2.imwrite("logs/raw_capture.png", img)
    print("Saved raw capture to logs/raw_capture.png")
    print("mean pixel:", img.mean(), "min:", img.min(), "max:", img.max())

    text = ocr_log_text(img, debug_dir="logs")

    print("\n===== OCR OUTPUT START =====\n")
    print(text)
    print("\n===== OCR OUTPUT END =====\n")
    

if __name__ == "__main__":
    main()
