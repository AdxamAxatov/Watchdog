"""Standalone entry point for DropStatsRunner.exe.

Drives the weekly drop-stats report flow on the panel. Scheduled via
Task Scheduler on the RDP user account (where the panel lives), e.g.
Tuesday 23:59 PC-local time.

This wrapper guarantees a crash log is written to disk on any failure,
including import-time exceptions that would otherwise vanish into a
closing console window.

Usage:
    DropStatsRunner.exe
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

if not getattr(sys, "frozen", False):
    _SRC = Path(__file__).resolve().parent
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))


def _exe_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_crash(exc: BaseException, where: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidates = [
        Path(_exe_dir()) / "logs" / "drop_stats",
        Path(os.environ.get("TEMP", ".")) / "drop_stats_crash",
    ]
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"startup_crash_{ts}.log"
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"=== DropStatsRunner crash @ {ts} ({where}) ===\n")
                f.write(f"executable: {sys.executable}\n")
                f.write(f"frozen:     {getattr(sys, 'frozen', False)}\n")
                f.write(f"cwd:        {os.getcwd()}\n")
                f.write(f"USERNAME:   {os.environ.get('USERNAME')}\n")
                f.write(f"sys.path:   {sys.path}\n\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
            return str(p)
        except Exception:
            continue
    return "(could not write any crash log)"


def main() -> int:
    try:
        from steps.drop_stats import run as drop_stats_run
    except BaseException as e:
        p = _write_crash(e, where="import")
        sys.stderr.write(f"DropStatsRunner startup IMPORT failed; see {p}\n")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return 99

    try:
        return drop_stats_run()
    except BaseException as e:
        p = _write_crash(e, where="run")
        sys.stderr.write(f"DropStatsRunner run failed; see {p}\n")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return 98


if __name__ == "__main__":
    sys.exit(main())
