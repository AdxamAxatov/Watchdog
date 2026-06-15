import os
import sys
import yaml
import logging
from datetime import datetime

def runtime_root() -> str:
    # Where PyInstaller puts bundled files
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    # Running from source: project root is parent of /src
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def exe_dir() -> str:
    # Folder where Watchdog.exe lives (good for logs)
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def disable_quick_edit() -> None:
    """Disable Windows console QuickEdit + Insert mode for this process.

    QuickEdit is ON by default in Windows consoles. A stray click inside the
    window puts the console into "mark"/selection mode, which BLOCKS the process
    on its next write to stdout/stderr until someone presses Enter/Esc. For an
    unattended long-running loop (Watchdog / Boot / WindowChecker / MemReduct /
    DropStats) that single click silently freezes the whole process — no
    heartbeat, no work, until a human notices. The health checker froze the same
    way, which is why a frozen WindowChecker never got restarted.

    Clearing ENABLE_QUICK_EDIT_MODE (and ENABLE_INSERT_MODE, with the required
    ENABLE_EXTENDED_FLAGS) removes that trap. No-op on non-Windows and when no
    console is attached (headless / redirected — GetConsoleMode then fails), so
    it is safe to call unconditionally at startup.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        STD_INPUT_HANDLE = -10
        ENABLE_EXTENDED_FLAGS = 0x0080
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_INSERT_MODE = 0x0020

        kernel32 = ctypes.windll.kernel32
        # Set restype/argtypes so the 64-bit console HANDLE isn't truncated.
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if not handle:
            return
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return  # no console attached (headless / redirected) — nothing to do
        new_mode = (mode.value | ENABLE_EXTENDED_FLAGS) \
            & ~ENABLE_QUICK_EDIT_MODE & ~ENABLE_INSERT_MODE
        kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        # Hardening only — never let it break startup.
        pass


def load_yaml(rel_path: str) -> dict:
    # 1) Prefer editable files next to the EXE (or project root in dev)
    abs_path = os.path.join(exe_dir(), rel_path)
    if not os.path.exists(abs_path):
        # 2) Fallback to bundled PyInstaller internal files
        abs_path = os.path.join(runtime_root(), rel_path)

    for enc in ("utf-8", "cp1252", "cp1251"):
        try:
            with open(abs_path, "r", encoding=enc) as f:
                return yaml.safe_load(f)
        except UnicodeDecodeError:
            continue
    # Last resort: ignore bad bytes
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        return yaml.safe_load(f)

def setup_logger():
    logs_dir = os.path.join(exe_dir(), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, f"watchdog_{ts}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("watchdog")



