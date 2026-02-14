import ctypes

def set_dpi_awareness():
    try:
        # Windows 10+ best option
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # system aware (legacy)
            except Exception:
                pass

set_dpi_awareness()

import sys
import os

print("frozen:", getattr(sys, "frozen", False))
print("_MEIPASS:", getattr(sys, "_MEIPASS", None))
print("exe:", sys.executable)
print("-" * 40)

# normal imports
from watchdog import run_watchdog
from utils import setup_logger

def main():
    logger = setup_logger()
    run_watchdog()

if __name__ == "__main__":
    main()
