"""FarmAgent pure logic: health evaluation + escalation ladder + HTTP API.

STDLIB ONLY — no pywin32, no requests. This module never touches a window
handle or spawns a process; adapters live in farm_agent_main.py. That split is
deliberate (ADR-001): the supervisor must not share the failure modes of what
it supervises, and this half stays unit-testable on any OS."""
import hmac
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
                 unhealthy_loops_before_reboot=3, reboot_min_interval_s=7200,
                 renderers_unhealthy_loops_before_action=10):
        self.state_path = state_path
        self.clock = clock
        self.n_before_reboot = int(unhealthy_loops_before_reboot)
        self.reboot_min_interval_s = float(reboot_min_interval_s)
        # Renderer count dips every WindowChecker reconnect cycle; only a
        # SUSTAINED shortfall (this many consecutive loops) escalates.
        self.renderers_loops_before_action = int(renderers_unhealthy_loops_before_action)
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
        hb_bad = not by["wc_heartbeat"]["healthy"]
        proc_bad = not by["wc_process"]["healthy"]
        rend_bad = not by["renderers"]["healthy"]

        # Renderer debounce (I1): the reconnect cycle disconnects every session
        # for ~30-60s each pass, so a single low sample is a healthy dip — WC is
        # mid-recovery. Track a streak; only a SUSTAINED shortfall means WC is
        # alive-but-stuck. Killing a live WC mid-cycle would fight its own
        # recovery, so a lone dip must never trigger a restart.
        if rend_bad:
            self.state["renderers_bad_streak"] = int(self.state.get("renderers_bad_streak", 0)) + 1
        else:
            self.state["renderers_bad_streak"] = 0
        rend_sustained = self.state["renderers_bad_streak"] >= self.renderers_loops_before_action

        # WC is "broken" (restart it) only on an unambiguous signal: stale
        # heartbeat, dead process, or a sustained renderer shortfall.
        wc_broken = hb_bad or proc_bad or rend_sustained
        missing_users = by["watchdogs"].get("missing_users", [])
        unhealthy = wc_broken or bool(missing_users)

        actions = []
        if not unhealthy:
            self.state["consecutive_unhealthy"] = 0
            self._save()
            return actions
        self.state["consecutive_unhealthy"] = int(self.state.get("consecutive_unhealthy", 0)) + 1

        if wc_broken:
            actions.append("restart_windowchecker")
        for user in missing_users:
            actions.append(f"run_watchdog_task:{user}")

        # Reboot is only REQUESTED here; the rate limit is enforced at execution
        # by try_consume_reboot — the single gate shared with the API path (I2).
        if self.state["consecutive_unhealthy"] >= self.n_before_reboot:
            actions.append("reboot")
        self._save()
        return actions

    def try_consume_reboot(self, force=False):
        """Authorize a reboot NOW, or refuse it. Returns True (and stamps the
        timestamp + resets the unhealthy counter) when allowed: always if
        force, else at most once per reboot_min_interval_s (ADR-003 boot-loop
        guard). The SINGLE enforcement point — both the escalation loop and the
        API /action/reboot route call this, so neither can bypass the limit."""
        if not force:
            last = float(self.state.get("last_reboot_ts", 0.0))
            if last > 0 and (self.clock() - last) < self.reboot_min_interval_s:
                return False
        self.state["last_reboot_ts"] = self.clock()
        self.state["consecutive_unhealthy"] = 0
        self._save()
        return True


# ---- HTTP control plane (consumed by Sherlock Homeless) ---------------------

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ACTION_ROUTES = {
    "restart-windowchecker": ("restart_windowchecker", False),
    "run-health-check": ("run_health_check", False),
    "reboot": ("reboot", False),
    "restart-watchdog": ("run_watchdog_task", True),   # True -> takes <user> path arg
}


def make_api_server(host, port, token, status_provider, action_executor):
    """Token-authed JSON API. GET /status; POST /action/<route>[/<arg>].
    Silence-is-unhealthy contract: Sherlock treats a timeout as the alert,
    so this server never needs outbound connectivity.

    FAIL-CLOSED: an empty/blank token would let any request carrying an empty
    X-Farm-Token header through — refuse to build the server instead. The
    caller must run WITHOUT the API rather than with an open one."""
    if not (token or "").strip():
        raise ValueError("refusing to start API with empty token — set "
                         "farm_agent_config.yaml 'token' (box stays autonomous, "
                         "just unreachable remotely)")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet — agent has its own log
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            # Constant-time compare (M1) — token is guaranteed non-empty by the
            # fail-closed guard above.
            provided = self.headers.get("X-Farm-Token") or ""
            if not hmac.compare_digest(provided, token):
                self._send(401, {"error": "bad token"})
                return False
            return True

        def do_GET(self):
            if not self._authed():
                return
            if self.path.rstrip("/") == "/status":
                try:
                    self._send(200, status_provider())
                except Exception as e:
                    self._send(500, {"error": str(e)})
            else:
                self._send(404, {"error": "unknown route"})

        def do_POST(self):
            if not self._authed():
                return
            parts = [p for p in self.path.split("/") if p]
            if len(parts) >= 2 and parts[0] == "action" and parts[1] in _ACTION_ROUTES:
                name, takes_arg = _ACTION_ROUTES[parts[1]]
                # Parse an optional JSON body (used by /action/reboot: {"force":true}).
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length > 0 else b""
                    body = json.loads(raw) if raw else {}
                    if not isinstance(body, dict):
                        body = {}
                except Exception:
                    body = {}
                if takes_arg:
                    arg = parts[2] if len(parts) > 2 else None
                    if not arg:
                        self._send(400, {"error": "missing argument"})
                        return
                    call_arg = arg
                else:
                    # No-arg routes receive the parsed body dict as their arg.
                    call_arg = body
                try:
                    self._send(200, action_executor(name, call_arg))
                except Exception as e:
                    self._send(500, {"error": str(e)})
            else:
                self._send(404, {"error": "unknown route"})

    return ThreadingHTTPServer((host, port), Handler)
