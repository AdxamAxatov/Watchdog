"""
cs2_validate.py - CS2 file integrity validation via Steam.

Hands `steam://validate/730` to Steam at most once per 24 hours.
A timestamped marker file (`logs/cs2_validate_last_run.txt`) gates re-runs.

Cross-user pause-flag:
    Before triggering Steam, writes a flag at C:\\ProgramData\\Watchdog\\
    that Watchdog (running in a different user session) reads. While the
    flag is fresh, Watchdog skips its CS2 instance-count fix so it doesn't
    kill+relaunch CS2 mid-validation. Flag has a 30-min TTL — if validator
    crashes or Steam hangs forever, Watchdog auto-resumes after 30 min.

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

# Cross-user shared flag dir. C:\ProgramData is writable by every user
# session, so Watchdog (running as the RDP user) can read what the validator
# (running as the main user) writes. Both sides hardcode this same path.
SHARED_DIR = Path(r"C:\ProgramData\Watchdog")
PAUSE_FLAG_PATH = SHARED_DIR / "cs2_validation_in_progress.flag"

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


def _set_pause_flag() -> None:
    """Write the cross-user pause flag so Watchdogs skip CS2 fixes."""
    try:
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        PAUSE_FLAG_PATH.touch()
    except Exception as e:
        log.warning("Could not write pause flag at %s: %s", PAUSE_FLAG_PATH, e)


def _clear_pause_flag() -> None:
    try:
        PAUSE_FLAG_PATH.unlink(missing_ok=True)
    except Exception:
        pass


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
    """Trigger CS2 validation if the 24h interval has elapsed.
    Returns True iff validation was actually triggered this call."""
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

    # Set the pause flag BEFORE handing off to Steam, so any Watchdog tick
    # that fires in the next milliseconds sees the flag and skips its fix.
    _set_pause_flag()

    if not trigger_validation():
        log.error("steam:// invocation failed; clearing pause flag, marker NOT updated")
        _clear_pause_flag()
        return False

    _touch_marker()
    log.info(
        "CS2 validation requested; pause flag set, next run allowed in %.0fh",
        INTERVAL_HOURS,
    )
    print(f"[cs2-validate] OK - pause flag set, next run allowed in {INTERVAL_HOURS:.0f}h")
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    force_run = "--force" in sys.argv
    run(force=force_run)
