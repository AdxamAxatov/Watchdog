"""Standalone entry point for CS2Validator.exe.

Triggers CS2 file integrity validation via Steam (`steam://validate/730`),
gated by a 24-hour marker so it's safe to run on any cadence — repeated
calls within 24h are no-ops. Intended to be scheduled via Task Scheduler
on the main user account (where Steam lives).

Usage:
    CS2Validator.exe              # respect 24h interval
    CS2Validator.exe --force      # ignore interval, validate now
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    _SRC = Path(__file__).resolve().parent
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

from steps.cs2_validate import run as cs2_validate_run


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    force = "--force" in sys.argv
    triggered = cs2_validate_run(force=force)
    return 0 if triggered or not force else 1


if __name__ == "__main__":
    sys.exit(main())
