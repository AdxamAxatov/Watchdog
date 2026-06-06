"""Heartbeat writer for stuck-process detection.

Writes logs/<name>_heartbeat_<USERNAME>.txt with the current UNIX timestamp.
An external health_check.bat reads the file's mtime — if older than a
threshold, the exe is assumed stuck and restarted via Task Scheduler.
"""

import os
import time
from pathlib import Path
from typing import Optional

from utils import exe_dir


def write_heartbeat(name: str, subfolder: Optional[str] = None) -> None:
    """Write a heartbeat marker. By default lands in <exe_dir>/logs/.
    Pass `subfolder` to nest it (e.g. subfolder="drop_stats" →
    <exe_dir>/logs/drop_stats/<name>_heartbeat_<USER>.txt)."""
    try:
        username = os.environ.get("USERNAME", "unknown")
        logs_dir = Path(exe_dir()) / "logs"
        if subfolder:
            logs_dir = logs_dir / subfolder
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / f"{name}_heartbeat_{username}.txt"
        path.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def sleep_with_heartbeat(name: str, total_seconds: float, chunk_seconds: float = 30,
                         subfolder: Optional[str] = None) -> None:
    """Sleep for total_seconds, writing a heartbeat at the start of every
    chunk_seconds. Lets the external health checker detect a freeze within
    chunk_seconds, not at the program's natural loop cadence."""
    end = time.time() + total_seconds
    while True:
        write_heartbeat(name, subfolder=subfolder)
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(chunk_seconds, remaining))
