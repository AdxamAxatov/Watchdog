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
    cfg = config or {}
    exe_path = cfg.get("exe_path")  # set this in your config later

    # 1) Find or launch
    try:
        win = _get_window(timeout_s=2)
    except Exception:
        if not exe_path:
            raise RuntimeError("MemReduct: exe_path not provided and window not found.")
        subprocess.Popen(exe_path, shell=False)
        win = _get_window(timeout_s=10)

    # 2) Focus
    win.restore()
    win.set_focus()
    time.sleep(0.3)

    win.child_window(title=CLEAN_TITLE, class_name="Button").click_input()
    time.sleep(1.0)

    return True


if __name__ == "__main__":
    run({"exe_path": r"C:\Program Files\Mem Reduct\MemReduct.exe"})

