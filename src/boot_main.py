import logging
import os
import sys
import traceback
import time
from datetime import datetime

from steps.memreduct import run as mem_run
from steps.rdp import run as rdp_run
from steps.windows_focuser import run as focus_run
from utils import load_yaml, exe_dir
from auto_updater import check_updates


def setup_boot_logger() -> logging.Logger:
    logs_dir = os.path.join(exe_dir(), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, f"boot_{ts}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    log = logging.getLogger("boot")
    log.info("Log: %s", log_path)
    return log


BOOT_UPDATE_CONFIG = os.path.join(exe_dir(), "config", "boot_update_config.yaml")
UPDATE_CHECK_INTERVAL = 3600  # 1 hour


def check_boot_update(log):
    """Check for Boot.exe updates. Returns silently on any error."""
    try:
        log.info("Auto-update: checking for Boot.exe update...")
        result = check_updates(config_path=BOOT_UPDATE_CONFIG)
        log.info(f"Auto-update result: {result}")
        if result and result.get('error'):
            error_msg = result['error']
            if 'Interval' not in error_msg and 'Disabled' not in error_msg and 'Dev mode' not in error_msg:
                log.warning(f"Auto-update failed: {error_msg}")
    except Exception as e:
        log.warning(f"Auto-update exception: {e}")


def focus_run_with_updates(log, last_update_check):
    """Wrap focus_run's loop with hourly Boot.exe update checks."""
    from utils import load_yaml
    from steps.windows_focuser import check_and_focus_windows
    from datetime import datetime as dt

    cfg = load_yaml("config/regions.yaml")
    rdp_config = cfg.get("rdp_windows", {})
    title_search = rdp_config.get("title_search", "SinFermera")
    focus_interval_minutes = rdp_config.get("focus_interval_minutes", 15)
    focus_interval_seconds = focus_interval_minutes * 60

    log.info("Focus maintenance with update checks started (every %d min)", focus_interval_minutes)
    cycle_count = 0

    while True:
        cycle_count += 1
        now_str = dt.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now_str}] Cycle #{cycle_count}")
        log.info(f"Health check cycle #{cycle_count}")

        focused, closed = check_and_focus_windows(title_search, log)
        if closed > 0:
            print(f"!! Closed {closed} hung window(s)")
        if focused > 0:
            print(f"OK Focused {focused} window(s)")
        if focused == 0 and closed == 0:
            print(f"!! No windows to focus")

        # Hourly Boot.exe update check
        if time.time() - last_update_check >= UPDATE_CHECK_INTERVAL:
            check_boot_update(log)
            last_update_check = time.time()

        time.sleep(focus_interval_seconds)


def main():
    log = setup_boot_logger()
    log.info("=== BOOT STARTED ===")

    # Check for Boot.exe update on startup
    check_boot_update(log)

    try:
        cfg = load_yaml("config/regions.yaml")
        log.info("Config loaded")
    except Exception:
        log.exception("Failed to load regions.yaml")
        raise

    paths = cfg.get("paths", {})

    # Step 1: MemReduct
    try:
        log.info("Step 1: MemReduct")
        print("1️⃣  Starting MemReduct...")
        mem_run({"exe_path": paths["memreduct_exe"][0]})
        print("   ✅ MemReduct complete\n")
        log.info("MemReduct done")
    except Exception:
        log.exception("MemReduct step failed")
        raise

    time.sleep(2)

    # Step 2: RDP
    try:
        log.info("Step 2: RDPClient")
        print("2️⃣  Starting RDPClient...")
        rdp_run()
        print("   ✅ RDPClient complete\n")
        log.info("RDPClient done")
    except Exception:
        log.exception("RDP step failed")
        raise

    log.info("=== BOOT COMPLETE ===")
    
    # NEW: Step 3: Focus Maintenance (runs continuously, with periodic update checks)
    try:
        log.info("Step 3: Focus Maintenance (with hourly update check)")
        print("3️⃣  Starting Focus Maintenance...\n")
        last_update_check = time.time()
        focus_run_with_updates(log, last_update_check)
    except Exception:
        log.exception("Focus Maintenance step failed")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # main() already logged the traceback via log.exception().
        # Fallback: if logger setup itself failed, write a raw crash file.
        try:
            logs_dir = os.path.join(exe_dir(), "logs")
            os.makedirs(logs_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            crash_path = os.path.join(logs_dir, f"boot_crash_{ts}.log")
            with open(crash_path, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        sys.exit(1)