"""
drop_stats.py — Weekly drop-stats report runner.

Flow (triggered Tuesdays at 23:59 PC-local by Task Scheduler, runs as RDP user):

  1. Enable + trigger the dedicated drop-stats health checker (safety net).
  2. Disable the general health checker so it can't relaunch Watchdog.
  3. Kill Watchdog.exe for the current user (it would otherwise interfere
     with the panel mid-report).
  4. Find the panel window + focus it.
  5. Click kill_all_cs2 to close CS2 cleanly.
  6. Walk the panel through:  Drop Stats button → week dropdown
                              → previous-week option → Generate report.
  7. OCR-poll the logbox region waiting for the configured completion
     phrase ("Case report sent" by default). Hard timeout caps the wait.
  8. (finally) Re-enable + trigger the general health checker (this is
     what brings Watchdog back up) and disable the dedicated safety-net
     task. Runs on every exit path including crashes.

If the script itself dies mid-flow, the dedicated safety-net task fires
within ~5 min and runs the same restore sequence externally — so the
general health checker always gets re-enabled.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import win32gui

_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils import exe_dir, load_yaml
from winops import (
    set_dpi_awareness,
    find_window,
    force_foreground,
    pct_to_screen_xy,
    safe_click,
)
from vision import capture_window_region_pct
from ocr import ocr_log_text
from heartbeat import write_heartbeat, sleep_with_heartbeat


DROP_STATS_CFG = "config/drop_stats.yaml"
APP_CFG = "config/app.yaml"
REGIONS_CFG = "config/regions.yaml"

HEARTBEAT_NAME = "drop_stats"


def _setup_logger() -> logging.Logger:
    logs_dir = Path(exe_dir()) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"drop_stats_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(str(log_path), encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return logging.getLogger("drop_stats")


def _is_placeholder(value: str) -> bool:
    return not value or value.startswith("CHANGE_ME")


def _schtasks_change(task: str, enable: bool, log: logging.Logger) -> bool:
    flag = "/Enable" if enable else "/Disable"
    try:
        r = subprocess.run(
            ["schtasks", "/Change", "/TN", task, flag],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            log.info("schtasks %s OK: %s", flag, task)
            return True
        log.error("schtasks %s failed (rc=%d) for %r: %s",
                  flag, r.returncode, task, (r.stderr or r.stdout).strip())
        return False
    except Exception as e:
        log.error("schtasks %s exception for %r: %s", flag, task, e)
        return False


def _schtasks_run(task: str, log: logging.Logger) -> bool:
    try:
        r = subprocess.run(
            ["schtasks", "/Run", "/TN", task],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            log.info("schtasks /Run OK: %s", task)
            return True
        log.error("schtasks /Run failed (rc=%d) for %r: %s",
                  r.returncode, task, (r.stderr or r.stdout).strip())
        return False
    except Exception as e:
        log.error("schtasks /Run exception for %r: %s", task, e)
        return False


def _kill_watchdog(log: logging.Logger) -> None:
    user = os.environ.get("USERNAME", "")
    cmd = ["taskkill", "/F", "/IM", "Watchdog.exe"]
    if user:
        cmd += ["/FI", f"USERNAME eq {user}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        log.info("taskkill Watchdog.exe (user=%s) rc=%d: %s",
                 user or "<all>", r.returncode,
                 (r.stdout or r.stderr).strip())
    except Exception as e:
        log.error("taskkill Watchdog.exe failed: %s", e)


def _click_pct(hwnd: int, pct: Optional[dict], label: str,
               log: logging.Logger) -> bool:
    if not pct or "x" not in pct or "y" not in pct:
        log.error("%s coords missing/invalid in config", label)
        return False
    x_pct, y_pct = float(pct["x"]), float(pct["y"])
    if x_pct == 0.0 and y_pct == 0.0:
        log.error("%s coords are placeholders (0, 0) — calibrate regions.yaml", label)
        return False
    try:
        x, y = pct_to_screen_xy(hwnd, x_pct, y_pct)
        log.info("Click %s at (%d, %d)  (pct=%.4f, %.4f)",
                 label, x, y, x_pct, y_pct)
        safe_click(x, y)
        return True
    except Exception as e:
        log.error("Click %s failed: %s", label, e)
        return False


def _watch_logbox_for(hwnd: int, region: dict, phrase: str,
                      timeout_s: float, poll_s: float,
                      log: logging.Logger) -> bool:
    needle = phrase.lower()
    deadline = time.time() + timeout_s
    iteration = 0
    last_snippet: str = ""
    while time.time() < deadline:
        iteration += 1
        write_heartbeat(HEARTBEAT_NAME)
        img = capture_window_region_pct(
            hwnd,
            float(region["x"]), float(region["y"]),
            float(region["w"]), float(region["h"]),
        )
        if img is None:
            log.warning("Logbox capture returned None (poll %d)", iteration)
        else:
            try:
                text = ocr_log_text(img) or ""
            except Exception as e:
                log.warning("OCR failed on poll %d: %s", iteration, e)
                text = ""
            if needle in text.lower():
                log.info("Completion phrase %r found after %d polls",
                         phrase, iteration)
                return True
            snippet = re.sub(r"\s+", " ", text)[:120]
            if snippet != last_snippet:
                log.info("Logbox poll %d: %r", iteration, snippet)
                last_snippet = snippet
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(poll_s, remaining))
    log.warning("Completion phrase %r not seen within %.0fs",
                phrase, timeout_s)
    return False


def _restore(general_task: str, self_task: str, log: logging.Logger) -> None:
    """Always runs (finally): re-enable + run general HC; disable self."""
    if not _is_placeholder(general_task):
        _schtasks_change(general_task, enable=True, log=log)
        _schtasks_run(general_task, log=log)
    else:
        log.error("health_check_task not configured — cannot re-enable general HC")
    if not _is_placeholder(self_task):
        _schtasks_change(self_task, enable=False, log=log)
    else:
        log.warning("self_task not configured — leaving safety-net task as-is")


def run() -> int:
    set_dpi_awareness()
    log = _setup_logger()
    log.info("=== drop_stats run start ===")
    write_heartbeat(HEARTBEAT_NAME)

    cfg = load_yaml(DROP_STATS_CFG) or {}
    regions = load_yaml(REGIONS_CFG) or {}
    app = load_yaml(APP_CFG) or {}

    general_task = cfg.get("health_check_task", "") or ""
    self_task = cfg.get("self_task", "") or ""
    post_kill_wait = float(cfg.get("post_kill_wait_seconds", 8))
    click_delay = float(cfg.get("click_delay_seconds", 1.5))
    timeout_min = float(cfg.get("logbox_watch_timeout_minutes", 10))
    poll_s = float(cfg.get("logbox_poll_seconds", 10))
    completion_phrase = cfg.get("completion_phrase", "Case report sent")

    title_sub = (app.get("window") or {}).get("title_substring", "")
    drop = regions.get("drop_stats") or {}
    logbox = regions.get("logbox_full_pct") or {}
    kill_all = regions.get("kill_all_cs2_point_pct") or {}

    # Required-config sanity checks; bail without touching anything if bad.
    if not title_sub:
        log.error("app.yaml window.title_substring missing — aborting")
        return 2
    if not logbox or not all(k in logbox for k in ("x", "y", "w", "h")):
        log.error("regions.yaml logbox_full_pct missing — aborting")
        return 2
    if _is_placeholder(general_task):
        log.error("drop_stats.yaml health_check_task not configured — aborting")
        return 2

    try:
        # 1. Bring up the safety-net task FIRST. If a later step crashes
        # before we get to the cleanup, that task picks up the slack.
        if not _is_placeholder(self_task):
            _schtasks_change(self_task, enable=True, log=log)
            _schtasks_run(self_task, log=log)
        else:
            log.warning("self_task not set — no safety-net checker active")

        # 2. Disable general HC so it doesn't immediately relaunch Watchdog
        # after we kill it.
        if not _schtasks_change(general_task, enable=False, log=log):
            log.error("Could not disable general HC; bailing to cleanup")
            return 7

        # 3. Kill Watchdog so it can't interfere.
        _kill_watchdog(log)
        time.sleep(2.0)
        write_heartbeat(HEARTBEAT_NAME)

        # 4. Find + focus panel.
        m = find_window(title_substring=title_sub)
        if not m:
            log.error("Panel window with title containing %r not found", title_sub)
            return 3
        hwnd = m.hwnd
        log.info("Panel hwnd=%d title=%r", hwnd, m.title)

        if not force_foreground(hwnd, tries=8, sleep_s=0.2):
            try:
                fg_title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            except Exception:
                fg_title = "<unknown>"
            log.error("Could not focus panel (foreground=%r)", fg_title)
            return 4
        time.sleep(0.5)

        # 5. Kill-all-CS2 click.
        if kill_all and "x" in kill_all and "y" in kill_all:
            _click_pct(hwnd, kill_all, "kill_all_cs2", log)
            sleep_with_heartbeat(HEARTBEAT_NAME, post_kill_wait)
        else:
            log.warning("kill_all_cs2_point_pct missing — skipping kill click")

        # 6. Refocus (CS2 dying may have stolen focus briefly) then the
        # drop-stats click sequence.
        if not force_foreground(hwnd, tries=4, sleep_s=0.15):
            log.warning("Re-focus before drop-stats clicks failed; continuing anyway")

        steps = [
            ("drop_stats_button_pct",         drop.get("drop_stats_button_pct")),
            ("week_dropdown_pct",             drop.get("week_dropdown_pct")),
            ("previous_week_option_pct",      drop.get("previous_week_option_pct")),
            ("generate_report_button_pct",    drop.get("generate_report_button_pct")),
        ]
        for label, pct in steps:
            if not _click_pct(hwnd, pct, label, log):
                log.error("Step %r failed; aborting click sequence", label)
                return 5
            sleep_with_heartbeat(HEARTBEAT_NAME, click_delay)

        # 7. Watch logbox for completion.
        log.info("Watching logbox for %r (timeout %.0fs, poll %.0fs)",
                 completion_phrase, timeout_min * 60, poll_s)
        found = _watch_logbox_for(
            hwnd, logbox, completion_phrase,
            timeout_s=timeout_min * 60,
            poll_s=poll_s,
            log=log,
        )
        if not found:
            log.error("Completion phrase never appeared — falling through to cleanup")
            return 6

        log.info("=== drop_stats run complete ===")
        return 0

    except Exception:
        log.exception("drop_stats unhandled exception")
        return 1
    finally:
        _restore(general_task, self_task, log)
        write_heartbeat(HEARTBEAT_NAME)
        log.info("Cleanup done; exiting")


if __name__ == "__main__":
    sys.exit(run())
