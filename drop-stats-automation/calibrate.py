"""Calibration app.

Captures the current mouse pointer coordinates for each step and writes
them into config.yaml. Run this once after any UI/layout change.

Usage:
    python calibrate.py

For each step, hover the mouse over the target UI element and press ENTER.
Press Ctrl+C at any time to abort without saving.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    import pyautogui
    import yaml
except ImportError:
    print("Missing dependency. Install with:")
    print("    pip install pyautogui pyyaml")
    sys.exit(1)

CONFIG_PATH = Path(__file__).with_name("config.yaml")

STEPS = [
    ("drop_stats_button", "Hover over the 'Drop Stats' button"),
    ("week_dropdown", "Hover over the week dropdown (the control you click to open it)"),
    ("week_option", "Open the dropdown manually, then hover over the week to pick"),
    ("generate_report_button", "Hover over the 'Generate Report' button"),
]


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"steps": {}}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("steps", {})
    return data


def save_config(data: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def capture_step(label: str, instruction: str) -> tuple[int, int]:
    print()
    print(f"[{label}] {instruction}")
    input("    Position the mouse, then press ENTER to capture... ")
    # Tiny pause so the user can settle the cursor after pressing Enter.
    time.sleep(0.2)
    x, y = pyautogui.position()
    print(f"    Captured: x={x}, y={y}")
    return x, y


def main() -> int:
    print("Drop Stats Automation - Calibration")
    print("===================================")
    print(f"Config file: {CONFIG_PATH}")
    print()
    print("Tip: open the target application now, before you start.")
    print("     For 'week_option' you will need to open the dropdown first")
    print("     so the option is visible when you press ENTER.")

    config = load_config()
    steps = config.get("steps") or {}

    try:
        for label, instruction in STEPS:
            x, y = capture_step(label, instruction)
            steps[label] = {"x": int(x), "y": int(y)}
    except KeyboardInterrupt:
        print("\nAborted. config.yaml was NOT modified.")
        return 1

    config["steps"] = steps
    config.setdefault("click_delay", 1.0)
    config.setdefault("startup_delay", 2.0)
    config.setdefault("week_selection", "previous")

    save_config(config)
    print()
    print(f"Saved coordinates to {CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
