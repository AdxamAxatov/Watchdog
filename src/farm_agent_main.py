"""FarmAgent.exe — per-box supervisor + HTTP control plane. STDLIB + pyyaml only.

Deliberately does NOT import pywin32: every OS action is a subprocess call
(tasklist/schtasks/taskkill/shutdown), so this exe stays tiny and can never
deadlock on a hung window (ADR-001). Supervises WindowChecker + per-user
Watchdogs; Sherlock Homeless polls GET /status (a timeout IS the alert)."""
import glob
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

if not getattr(sys, "frozen", False):
    _SRC = os.path.dirname(os.path.abspath(__file__))
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)

from utils import exe_dir, load_yaml
from auto_updater import check_updates
from farm_agent_core import evaluate_health, EscalationLadder, make_api_server

UPDATE_CONFIG = os.path.join(exe_dir(), "config", "farm_agent_update_config.yaml")
LOOP_SECONDS = 60
VERSION = "1.0.0"


def setup_logger():
    logs = os.path.join(exe_dir(), "logs")
    os.makedirs(logs, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s",
                        handlers=[logging.FileHandler(os.path.join(logs, f"farmagent_{ts}.log"),
                                                      encoding="utf-8"),
                                  logging.StreamHandler()], force=True)
    return logging.getLogger("farmagent")


def _tasklist_count(image, username=None):
    cmd = ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image}"]
    if username:
        cmd += ["/FI", f"USERNAME eq {username}"]
    try:
        out = subprocess.check_output(cmd, text=True, errors="ignore", timeout=15)
        return sum(1 for line in out.splitlines() if image.lower() in line.lower())
    except Exception:
        return 0


def _wc_heartbeat_age():
    """Age (s) of the freshest WindowChecker heartbeat file, or None."""
    cands = glob.glob(os.path.join(exe_dir(), "logs", "windowchecker_heartbeat_*.txt"))
    # WindowChecker deploys to its own folder; allow a configured extra path too.
    if not cands:
        return None
    newest = max(os.stat(p).st_mtime for p in cands)
    return time.time() - newest


def collect_snapshot(cfg):
    watchdog_tasks = cfg.get("watchdog_users") or {}   # {"SinFermera11": "Watchdog11", ...}
    missing = [u for u in watchdog_tasks
               if _tasklist_count("Watchdog.exe", username=u) == 0]
    return {
        "wc_heartbeat_age_s": _wc_heartbeat_age(),
        "wc_running": _tasklist_count("WindowChecker.exe") > 0,
        "renderer_count": _tasklist_count("wfreerdp.exe"),
        "expected_sessions": int(cfg.get("expected_sessions", 2)),
        "missing_watchdog_users": missing,
        "cs2_count": _tasklist_count("cs2.exe"),
    }


class ActionExecutor:
    """Executes ladder/API actions via subprocess. Every action is appended to
    logs/farmagent_actions.log and kept in .recent for /status."""

    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        self.actions_log = os.path.join(exe_dir(), "logs", "farmagent_actions.log")
        self.recent = []

    def _record(self, entry):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {entry}"
        self.recent = (self.recent + [line])[-20:]
        try:
            with open(self.actions_log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        self.log.info("ACTION: %s", entry)

    def __call__(self, name, arg=None):
        if name == "restart_windowchecker":
            subprocess.run(["taskkill", "/F", "/IM", "WindowChecker.exe"],
                           capture_output=True, timeout=15)
            time.sleep(2)
            r = subprocess.run(["schtasks", "/Run", "/TN",
                                self.cfg.get("windowchecker_task", "WindowsChecker")],
                               capture_output=True, text=True, timeout=15)
            self._record(f"restart_windowchecker rc={r.returncode}")
            return {"ran": name, "rc": r.returncode}
        if name == "run_watchdog_task":
            task = (self.cfg.get("watchdog_users") or {}).get(arg)
            if not task:
                return {"error": f"unknown user {arg!r}"}
            r = subprocess.run(["schtasks", "/Run", "/TN", task],
                               capture_output=True, text=True, timeout=15)
            self._record(f"run_watchdog_task user={arg} task={task} rc={r.returncode}")
            return {"ran": name, "user": arg, "rc": r.returncode}
        if name == "run_health_check":
            self._record("manual health check requested")
            return {"ran": name}          # the 60s loop picks it up; cheap + safe
        if name == "reboot":
            self._record("REBOOT (requested)")
            subprocess.run(["shutdown", "/r", "/t", "30",
                            "/c", "FarmAgent recovery reboot"],
                           capture_output=True, timeout=15)
            return {"ran": name, "in_seconds": 30}
        return {"error": f"unknown action {name!r}"}


def main():
    log = setup_logger()
    log.info("=== FarmAgent v%s STARTED ===", VERSION)
    try:
        check_updates(config_path=UPDATE_CONFIG)
    except Exception as e:
        log.warning("update check failed: %s", e)

    cfg = load_yaml("config/farm_agent_config.yaml") or {}
    thresholds = {"wc_heartbeat_max_age_s": cfg.get("wc_heartbeat_max_age_s", 300)}
    ladder = EscalationLadder(
        os.path.join(exe_dir(), "logs", "ladder_state.json"),
        unhealthy_loops_before_reboot=cfg.get("unhealthy_loops_before_reboot", 3),
        reboot_min_interval_s=cfg.get("reboot_min_interval_s", 7200))
    executor = ActionExecutor(cfg, log)
    last = {"snapshot": {}, "checks": []}

    def status():
        return {"box": os.environ.get("COMPUTERNAME", "?"),
                "agent_version": VERSION,
                "snapshot": last["snapshot"], "checks": last["checks"],
                "recent_actions": executor.recent,
                "ladder": ladder.state,
                "ts": datetime.now().isoformat(timespec="seconds")}

    srv = make_api_server(cfg.get("bind", "0.0.0.0"), int(cfg.get("port", 8765)),
                          str(cfg.get("token", "")), status, executor)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("API listening on %s:%s", cfg.get("bind", "0.0.0.0"), cfg.get("port", 8765))

    hb_path = os.path.join(exe_dir(), "logs", "farmagent_heartbeat.txt")
    while True:
        try:
            with open(hb_path, "w") as f:
                f.write(datetime.now().isoformat())
            snap = collect_snapshot(cfg)
            checks = evaluate_health(snap, thresholds)
            actions = ladder.next_actions(checks)
            last.update(snapshot=snap, checks=checks)
            if actions:
                log.warning("Unhealthy: %s -> actions: %s",
                            [c["check"] for c in checks if not c["healthy"]], actions)
            for a in actions:
                name, _, arg = a.partition(":")
                executor(name, arg or None)
        except Exception:
            log.exception("agent loop error — continuing")
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
