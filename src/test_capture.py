import os
import time
import win32gui
import mss
import numpy as np
import cv2

from utils import load_yaml, setup_logger
from window_connector import find_hwnd_by_title_substring

APP_CFG_PATH = "config/app.yaml"
REGIONS_CFG_PATH = "config/regions.yaml"

def main():
    log = setup_logger()
    app = load_yaml(APP_CFG_PATH)
    regions = load_yaml(REGIONS_CFG_PATH)

    title_sub = app["window"]["title_substring"]
    hwnd, title = find_hwnd_by_title_substring(title_sub)
    if not hwnd:
        raise SystemExit("Window not found. Make sure the app is running.")

    log.info("Found window: hwnd=%s title=%r", hwnd, title)

    win32gui.ShowWindow(hwnd, 9)  

    try:
        cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        cx, cy = None, None

    if cx is None or cy is None or cx < -10000 or cy < -10000:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        BORDER_X = 8
        TITLEBAR_Y = 30
        cx = left + BORDER_X
        cy = top + TITLEBAR_Y

    lr = regions["log_region"]
    region = {
        "left": cx + int(lr["x"]),
        "top":  cy + int(lr["y"]),
        "width": int(lr["w"]),
        "height": int(lr["h"]),
    }

    os.makedirs("logs", exist_ok=True)
    out_path = os.path.join("logs", "last_log.png")

    with mss.mss() as sct:
        shot = sct.grab(region)
        img = np.array(shot)[:, :, :3]  # BGRA->BGR

    cv2.imwrite(out_path, img)
    print(f"✅ Saved: {out_path}")
    print("Open it and confirm it matches the log box exactly.")

if __name__ == "__main__":
    main()
