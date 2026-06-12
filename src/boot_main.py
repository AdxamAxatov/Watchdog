import logging
import os
import sys
import traceback
import time
from datetime import datetime

from steps.memreduct import run as mem_run
from steps.rdp import run as rdp_run
from utils import load_yaml, exe_dir
from auto_updater import check_updates
from heartbeat import write_heartbeat


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
    log.info("=== BOOT COMPLETE — one-shot bootstrap done, exiting ===")
    print("✅ Boot complete. Ongoing RDP-window health is handled by "
          "WindowChecker.exe (its own Task Scheduler task).\n")
    # Boot is now a ONE-SHOT bootstrapper: update check -> MemReduct -> RDP
    # launch, then exit. The continuous RDP black-screen/hung recovery loop
    # lives in WindowChecker.exe. NOTE: because Boot exits, the heartbeat
    # health-checker should NOT monitor Boot.exe (it monitors WindowChecker
    # instead) — see health_check.bat.


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