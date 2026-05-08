"""Weekly drop-stats report runner.

Reads coordinates from config.yaml and performs:
    1. Click "Drop Stats" button.
    2. Click the week dropdown, then click the week option.
    3. Click "Generate Report".

Designed to be invoked by Windows Task Scheduler (taskschd.msc) on a
weekly trigger. See README.md for setup.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pyautogui
    import yaml
except ImportError:
    print("Missing dependency. Install with:")
    print("    pip install pyautogui pyyaml")
    sys.exit(1)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"
LOG_PATH = BASE_DIR / "run_report.log"

REQUIRED_STEPS = [
    "drop_stats_button",
    "week_dropdown",
    "week_option",
    "generate_report_button",
]


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Config not found: {CONFIG_PATH}. Run calibrate.py first.")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def get_point(steps: dict, name: str) -> tuple[int, int]:
    step = steps.get(name)
    if not step or step.get("x") is None or step.get("y") is None:
        raise SystemExit(
            f"Step '{name}' has no coordinates in config.yaml. "
            f"Run calibrate.py to set it."
        )
    return int(step["x"]), int(step["y"])


def click(name: str, x: int, y: int, delay: float) -> None:
    logging.info("Click %s at (%d, %d)", name, x, y)
    pyautogui.moveTo(x, y, duration=0.2)
    pyautogui.click()
    time.sleep(delay)


def main() -> int:
    setup_logging()
    logging.info("=== Drop stats run started: %s ===", datetime.now().isoformat())

    config = load_config()
    steps = config.get("steps") or {}
    delay = float(config.get("click_delay", 1.0))
    startup_delay = float(config.get("startup_delay", 2.0))

    missing = [s for s in REQUIRED_STEPS if not steps.get(s) or steps[s].get("x") is None]
    if missing:
        raise SystemExit(f"Missing coordinates for: {', '.join(missing)}. Run calibrate.py.")

    # Safety: if the user slams the mouse to a screen corner, pyautogui aborts.
    pyautogui.FAILSAFE = True

    logging.info("Startup delay: %.1fs", startup_delay)
    time.sleep(startup_delay)

    try:
        click("drop_stats_button", *get_point(steps, "drop_stats_button"), delay=delay)
        click("week_dropdown", *get_point(steps, "week_dropdown"), delay=delay)
        click("week_option", *get_point(steps, "week_option"), delay=delay)
        click("generate_report_button", *get_point(steps, "generate_report_button"), delay=delay)
    except pyautogui.FailSafeException:
        logging.error("Aborted by fail-safe (mouse moved to screen corner).")
        return 2
    except Exception:
        logging.exception("Run failed")
        return 1

    logging.info("=== Drop stats run completed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
