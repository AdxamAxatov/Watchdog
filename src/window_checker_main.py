"""Standalone entry point for WindowChecker.exe.

Runs on the MAIN user session and, every cycle, disconnects+reconnects each
RDP session through the RDP Session Manager UI (no hung detection — the
reconnect heals frozen and healthy windows alike). A renderer that survives
its disconnect is FROZEN: it gets force-killed, the session reconnected, and
the affected user's Watchdog restarted. The host RDP stack is relaunched only
if no session window exists and the host process is dead.

This logic used to live inside Boot's focus-maintenance loop; it's now its own
executable so it runs, self-updates, and recovers independently of Boot. Boot
is now a one-shot bootstrapper (MemReduct + RDP launch) that exits.

Scheduled via Task Scheduler on the main user session (one instance per PC).

Cadence:
  - RDP health cycle : every `focus_interval_minutes` (regions.yaml, default 30)
  - Self-update check: every UPDATE_CHECK_INTERVAL (120s) + once at startup
  - Heartbeat        : written continuously so health_check.bat can detect a
                       hang and restart this exe via Task Scheduler

Usage:
    WindowChecker.exe
"""
from winops import set_dpi_awareness
set_dpi_awareness()  # MUST run before any window-coordinate work

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if not getattr(sys, "frozen", False):
    _SRC = Path(__file__).resolve().parent
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

from utils import load_yaml, exe_dir
from auto_updater import check_updates
from heartbeat import write_heartbeat, sleep_with_heartbeat
from steps.windows_focuser import cycle_or_recover_rdp_windows

HEARTBEAT_NAME = "windowchecker"
UPDATE_CONFIG = os.path.join(exe_dir(), "config", "windowchecker_update_config.yaml")
UPDATE_CHECK_INTERVAL = 120   # self-update check cadence (seconds)
BASE_TICK_SECONDS = 120       # loop granularity; update check runs each tick


def setup_logger() -> logging.Logger:
    logs_dir = os.path.join(exe_dir(), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, f"windowchecker_{ts}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return logging.getLogger("windowchecker")


def check_update(log: logging.Logger) -> None:
    """Check for WindowChecker.exe updates. Returns silently on any error."""
    try:
        log.info("Auto-update: checking for WindowChecker.exe update...")
        result = check_updates(config_path=UPDATE_CONFIG)
        log.info(f"Auto-update result: {result}")
        if result and result.get('error'):
            msg = result['error']
            if 'Interval' not in msg and 'Disabled' not in msg and 'Dev mode' not in msg:
                log.warning(f"Auto-update failed: {msg}")
    except Exception as e:
        log.warning(f"Auto-update exception: {e}")


def main() -> int:
    # Heartbeat immediately so a startup hang is detectable.
    write_heartbeat(HEARTBEAT_NAME)

    log = setup_logger()
    log.info("=== WindowChecker STARTED ===")

    # Detect a new build right after launch (matches Boot/Watchdog startup check).
    check_update(log)

    cfg = load_yaml("config/regions.yaml") or {}
    rdp_cfg = cfg.get("rdp_windows", {}) or {}
    title_search = rdp_cfg.get("title_search", "SinFermera")
    focus_interval_minutes = rdp_cfg.get("focus_interval_minutes", 30)
    focus_interval_seconds = focus_interval_minutes * 60

    log.info("RDP health cycle every %d min; self-update check every %ds",
             focus_interval_minutes, UPDATE_CHECK_INTERVAL)
    print(f"\nWindowChecker started | RDP cycle={focus_interval_minutes}m "
          f"update={UPDATE_CHECK_INTERVAL}s\n")

    last_update_check = time.time()   # startup check just ran
    # Do NOT run the first RDP cycle immediately: at boot, Boot.exe is still
    # launching RDP, and an immediate cycle could find 0 windows and relaunch
    # RDPClient itself — racing Boot and fighting over the mouse. Wait one full
    # interval so the host is settled before the first cycle.
    last_rdp_cycle = time.time()
    cycle_count = 0

    # Beat the heartbeat from inside the (potentially multi-minute) recovery
    # cycle so health_check.bat can't kill us mid-recovery.
    def _beat():
        write_heartbeat(HEARTBEAT_NAME)

    while True:
        write_heartbeat(HEARTBEAT_NAME)
        now = time.time()

        # --- RDP health cycle (every focus_interval_minutes) ---
        if now - last_rdp_cycle >= focus_interval_seconds:
            cycle_count += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] RDP cycle #{cycle_count}")
            log.info("RDP maintenance cycle #%d", cycle_count)
            try:
                closed, relaunched = cycle_or_recover_rdp_windows(title_search, log, beat=_beat)
                if closed > 0:
                    print(f"!! Closed {closed} RDP window(s) this cycle")
                if relaunched:
                    print("!! Relaunched host RDP stack")
                if closed == 0 and not relaunched:
                    print("OK RDP healthy — nothing to do")
            except Exception:
                # A bad cycle must never kill the loop.
                log.exception("RDP maintenance cycle failed — continuing")
            last_rdp_cycle = time.time()

        # --- Self-update check (every UPDATE_CHECK_INTERVAL) ---
        if time.time() - last_update_check >= UPDATE_CHECK_INTERVAL:
            check_update(log)
            last_update_check = time.time()

        # Sleep one base tick, beating the heartbeat every 30s.
        sleep_with_heartbeat(HEARTBEAT_NAME, BASE_TICK_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
