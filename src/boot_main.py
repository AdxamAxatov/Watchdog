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
from heartbeat import write_heartbeat, sleep_with_heartbeat


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
    """Wrap focus_run's loop with hourly Boot.exe update checks.

    Each tick runs the RDP black-screen / hung recovery + (optional)
    unconditional close-and-reopen cycle. CS2 recovery is left to Watchdog.exe.
    """
    from utils import load_yaml
    from steps.windows_focuser import cycle_or_recover_rdp_windows
    from datetime import datetime as dt

    cfg = load_yaml("config/regions.yaml")
    rdp_config = cfg.get("rdp_windows", {})
    title_search = rdp_config.get("title_search", "SinFermera")
    focus_interval_minutes = rdp_config.get("focus_interval_minutes", 30)
    focus_interval_seconds = focus_interval_minutes * 60

    log.info("RDP maintenance with update checks started (every %d min)", focus_interval_minutes)
    cycle_count = 0

    while True:
        cycle_count += 1
        write_heartbeat("boot")
        now_str = dt.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now_str}] Cycle #{cycle_count}")
        log.info(f"RDP maintenance cycle #{cycle_count}")

        try:
            closed, relaunched = cycle_or_recover_rdp_windows(title_search, log)
            if closed > 0:
                print(f"!! Closed {closed} RDP window(s) this tick")
            if relaunched:
                print("!! Relaunched host RDP stack")
            if closed == 0 and not relaunched:
                print("OK RDP healthy — nothing to do")
        except Exception:
            # A bad tick must never kill the maintenance loop.
            log.exception("RDP maintenance tick failed — continuing")

        # Hourly Boot.exe update check
        if time.time() - last_update_check >= UPDATE_CHECK_INTERVAL:
            check_boot_update(log)
            last_update_check = time.time()

        sleep_with_heartbeat("boot", focus_interval_seconds)


def main():
    # Write heartbeat immediately so a startup hang is detectable
    write_heartbeat("boot")

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
    # MemReduct is a nice-to-have memory cleanup, NOT mission-critical.
    # If it fails (most commonly because it's set to "run as administrator"
    # and UIPI blocks our click from a Medium-integrity Boot.exe), we log
    # the traceback and continue. RDP launch and the focus loop still run.
    try:
        log.info("Step 1: MemReduct")
        print("1️⃣  Starting MemReduct...")
        mem_run({"exe_path": paths["memreduct_exe"][0]})
        print("   ✅ MemReduct complete\n")
        log.info("MemReduct done")
    except Exception as e:
        log.exception("MemReduct step failed — continuing without memory cleanup")
        print(f"   ⚠️  MemReduct failed: {e} — continuing\n")

    write_heartbeat("boot")  # post-MemReduct progress beat
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

    write_heartbeat("boot")  # post-RDP progress beat
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