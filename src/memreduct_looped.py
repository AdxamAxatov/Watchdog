import os
import time

from steps.memreduct import run as mem_clean
from utils import load_yaml, exe_dir
from auto_updater import check_updates

# MemReductLooped.exe is a deployed app; it self-updates from releases/latest.
MEMREDUCT_UPDATE_CONFIG = os.path.join(exe_dir(), "config", "memreduct_update_config.yaml")


def _check_update() -> None:
    """Check for a MemReductLooped.exe update. Never lets an updater error
    kill the cleanup loop. When a newer build exists, check_updates() applies
    it and exits the process; the batch script relaunches the fresh exe."""
    try:
        check_updates(config_path=MEMREDUCT_UPDATE_CONFIG)
    except Exception as e:
        print(f"⚠️  Update check failed: {e} — continuing")


def main():
    # Detect a new build right after launch (matches Boot/Watchdog startup check).
    _check_update()

    cfg = load_yaml("config/regions.yaml")
    paths = cfg.get("paths", {})
    mem_exe = paths["memreduct_exe"][0]

    while True:
        # Loop runs every ~10 min, so one check per iteration is the update
        # cadence — cheap and guarded so it can never break the loop.
        _check_update()

        try:
            mem_clean({"exe_path": mem_exe})
        except Exception as e:
            print(f"⚠️  Error: {e}")
            print("   Continuing anyway...")

        print("💤 Sleeping 10 min...")
        time.sleep(10 * 60)

if __name__ == "__main__":
    main()
