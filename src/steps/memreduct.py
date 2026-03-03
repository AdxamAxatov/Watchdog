"""
steps/memreduct.py - Launch MemReduct and click Clean button

FIXED: Removed set_focus() which was causing SetForegroundWindow errors
"""

import time
import subprocess
from pywinauto import Desktop


TITLE = "Mem Reduct"
CLASS = "#32770"
CLEAN_TITLE = "Clean memory"


def _get_window(timeout_s=5):
    w = Desktop(backend="win32").window(title=TITLE, class_name=CLASS)
    w.wait("visible", timeout=timeout_s)
    return w


def run(config=None, context=None):
    """
    Launch MemReduct (if not running) and click Clean button.
    """
    cfg = config or {}
    exe_path = cfg.get("exe_path")

    # 1) Find or launch
    try:
        win = _get_window(timeout_s=2)
        print("   Found MemReduct window")
    except Exception:
        if not exe_path:
            raise RuntimeError("MemReduct: exe_path not provided and window not found.")
        
        print(f"   Launching MemReduct: {exe_path}")
        subprocess.Popen(exe_path, shell=False)
        win = _get_window(timeout_s=10)
        print("   MemReduct launched")

    # 2) Restore window if minimized (no focus needed)
    try:
        win.restore()
    except Exception:
        pass
    
    time.sleep(0.5)

    # 3) Click Clean button
    print("   Clicking Clean button...")
    win.child_window(title=CLEAN_TITLE, class_name="Button").click_input()
    time.sleep(1.0)
    print("   ✅ Clean clicked")

    return True


if __name__ == "__main__":
    run({"exe_path": r"C:\Program Files\Mem Reduct\MemReduct.exe"})