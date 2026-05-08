"""
cs2_validate.py - Daily CS2 file integrity validation via Steam.

Hands off `steam://validate/730` to Steam at most once per 24 hours.
A timestamped marker file (`logs/cs2_validate_last_run.txt`) gates re-runs,
so this is safe to call every Watchdog loop iteration — it only acts once
per day.

Standalone usage:
    python src/steps/cs2_validate.py            # respects the 24h marker
    python src/steps/cs2_validate.py --force    # ignore marker, run now
"""

from __future__ import annotations

import sys
import time
import subprocess
import logging
from pathlib import Path

# Make `from utils import ...` work when this file is launched directly.
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils import exe_dir

CS2_APPID = 730
MARKER_FILENAME = "cs2_validate_last_run.txt"
INTERVAL_HOURS = 24.0

log = logging.getLogger("cs2_validate")


def _marker_path() -> Path:
    return Path(exe_dir()) / "logs" / MARKER_FILENAME


def _hours_since_last_run() -> float | None:
    p = _marker_path()
    if not p.exists():
        return None
    try:
        return (time.time() - p.stat().st_mtime) / 3600.0
    except Exception:
        return None


def _touch_marker() -> None:
    p = _marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()


def trigger_validation(appid: int = CS2_APPID) -> bool:
    """Hand `steam://validate/<appid>` to the OS URL handler (Steam)."""
    url = f"steam://validate/{appid}"
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", url],
            shell=False,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except Exception as e:
        log.error("Failed to invoke %s: %s", url, e)
        return False


def run(force: bool = False) -> bool:
    """
    Trigger CS2 file validation if the 24h interval has elapsed.

    Returns True iff validation was actually triggered this call.
    """
    if not force:
        hours = _hours_since_last_run()
        if hours is not None and hours < INTERVAL_HOURS:
            log.debug(
                "CS2 validation skipped: last run %.1fh ago (< %.1fh)",
                hours, INTERVAL_HOURS,
            )
            return False

    log.info("Triggering CS2 file validation (appid=%d)", CS2_APPID)
    print(f"[cs2-validate] Asking Steam to validate appid {CS2_APPID}...")

    if not trigger_validation():
        log.error("steam:// invocation failed; marker NOT updated")
        return False

    _touch_marker()
    log.info("CS2 validation requested; next run allowed in %.0fh", INTERVAL_HOURS)
    print(f"[cs2-validate] OK - next run allowed in {INTERVAL_HOURS:.0f}h")
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    force_run = "--force" in sys.argv
    run(force=force_run)
