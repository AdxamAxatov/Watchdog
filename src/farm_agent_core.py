"""FarmAgent pure logic: health evaluation + escalation ladder + HTTP API.

STDLIB ONLY — no pywin32, no requests. This module never touches a window
handle or spawns a process; adapters live in farm_agent_main.py. That split is
deliberate (ADR-001): the supervisor must not share the failure modes of what
it supervises, and this half stays unit-testable on any OS."""
import json
import os
import time


def evaluate_health(snapshot, thresholds):
    """snapshot -> list of {"check","healthy","detail"} (see plan Task 5)."""
    max_age = float(thresholds.get("wc_heartbeat_max_age_s", 300))
    checks = []
    age = snapshot.get("wc_heartbeat_age_s")
    checks.append({"check": "wc_heartbeat",
                   "healthy": age is not None and age <= max_age,
                   "detail": f"age={age}"})
    checks.append({"check": "wc_process",
                   "healthy": bool(snapshot.get("wc_running")),
                   "detail": f"running={snapshot.get('wc_running')}"})
    rc, exp = snapshot.get("renderer_count", 0), snapshot.get("expected_sessions", 2)
    checks.append({"check": "renderers", "healthy": rc >= exp,
                   "detail": f"{rc}/{exp}"})
    missing = list(snapshot.get("missing_watchdog_users") or [])
    checks.append({"check": "watchdogs", "healthy": not missing,
                   "detail": ",".join(missing) or "all running",
                   "missing_users": missing})
    return checks


class EscalationLadder:
    """Deterministic, persisted. Rung 1: targeted restarts every unhealthy
    loop. Rung 2: reboot after N consecutive unhealthy loops, rate-limited
    (ADR-003: max 1 reboot / reboot_min_interval_s — no boot loops)."""

    def __init__(self, state_path, clock=time.time,
                 unhealthy_loops_before_reboot=3, reboot_min_interval_s=7200):
        self.state_path = state_path
        self.clock = clock
        self.n_before_reboot = int(unhealthy_loops_before_reboot)
        self.reboot_min_interval_s = float(reboot_min_interval_s)
        self.state = self._load()

    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"consecutive_unhealthy": 0, "last_reboot_ts": 0.0}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
        except Exception:
            pass

    def next_actions(self, checks):
        by = {c["check"]: c for c in checks}
        unhealthy = [c for c in checks if not c["healthy"]]
        actions = []
        if not unhealthy:
            self.state["consecutive_unhealthy"] = 0
            self._save()
            return actions
        self.state["consecutive_unhealthy"] = int(self.state.get("consecutive_unhealthy", 0)) + 1

        if not by["wc_heartbeat"]["healthy"] or not by["wc_process"]["healthy"] \
                or not by["renderers"]["healthy"]:
            actions.append("restart_windowchecker")
        for user in by["watchdogs"].get("missing_users", []):
            actions.append(f"run_watchdog_task:{user}")

        if self.state["consecutive_unhealthy"] >= self.n_before_reboot:
            last = float(self.state.get("last_reboot_ts", 0.0))
            # last <= 0 == never rebooted: the rate limit must not block the
            # FIRST reboot (only re-reboots within the window).
            if last <= 0 or (self.clock() - last) >= self.reboot_min_interval_s:
                actions.append("reboot")
                self.state["last_reboot_ts"] = self.clock()
                self.state["consecutive_unhealthy"] = 0
        self._save()
        return actions
