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

def load_yaml(rel_path: str) -> dict:
    # 1) Prefer editable files next to the EXE (or project root in dev)
    abs_path = os.path.join(exe_dir(), rel_path)
    if not os.path.exists(abs_path):
        # 2) Fallback to bundled PyInstaller internal files
        abs_path = os.path.join(runtime_root(), rel_path)

    with open(abs_path, "r", encoding="utf-8") as f:
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



