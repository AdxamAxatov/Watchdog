"""Unit tests for src/farm_agent_core.py (stdlib-only — runs anywhere)."""
import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from farm_agent_core import evaluate_health, EscalationLadder

THRESH = {"wc_heartbeat_max_age_s": 300}
HEALTHY = {"wc_heartbeat_age_s": 30.0, "wc_running": True,
           "renderer_count": 2, "expected_sessions": 2, "missing_watchdog_users": []}

def sick(**over):
    d = dict(HEALTHY); d.update(over); return d

class TestEvaluateHealth(unittest.TestCase):
    def test_all_healthy(self):
        self.assertTrue(all(c["healthy"] for c in evaluate_health(HEALTHY, THRESH)))
    def test_stale_heartbeat_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(wc_heartbeat_age_s=999), THRESH)}
        self.assertFalse(checks["wc_heartbeat"]["healthy"])
    def test_missing_heartbeat_file_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(wc_heartbeat_age_s=None), THRESH)}
        self.assertFalse(checks["wc_heartbeat"]["healthy"])
    def test_missing_renderers_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(renderer_count=1), THRESH)}
        self.assertFalse(checks["renderers"]["healthy"])
    def test_missing_watchdog_flagged(self):
        checks = {c["check"]: c for c in evaluate_health(sick(missing_watchdog_users=["SinFermera11"]), THRESH)}
        self.assertFalse(checks["watchdogs"]["healthy"])

class FakeClock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t

class TestLadder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = str(Path(self.dir) / "ladder.json")
        self.clock = FakeClock()
        self.ladder = EscalationLadder(self.state, clock=self.clock)
    def unhealthy(self):
        return evaluate_health(sick(wc_heartbeat_age_s=999), THRESH)
    def test_healthy_resets_and_no_actions(self):
        self.assertEqual(self.ladder.next_actions(evaluate_health(HEALTHY, THRESH)), [])
    def test_unhealthy_restarts_wc_not_reboot(self):
        acts = self.ladder.next_actions(self.unhealthy())
        self.assertIn("restart_windowchecker", acts)
        self.assertNotIn("reboot", acts)
    def test_reboot_after_three_consecutive(self):
        for _ in range(2):
            self.assertNotIn("reboot", self.ladder.next_actions(self.unhealthy()))
        self.assertIn("reboot", self.ladder.next_actions(self.unhealthy()))
    def test_reboot_rate_limited(self):
        for _ in range(3): self.ladder.next_actions(self.unhealthy())
        # 3 more unhealthy loops right after: counter is there but rate limit blocks
        for _ in range(3):
            self.assertNotIn("reboot", self.ladder.next_actions(self.unhealthy()))
        self.clock.t += 7201
        seen = []
        for _ in range(3): seen += self.ladder.next_actions(self.unhealthy())
        self.assertIn("reboot", seen)  # fires on the first eligible loop, then counter resets
    def test_missing_watchdog_action_is_targeted(self):
        checks = evaluate_health(sick(missing_watchdog_users=["SinFermera11"]), THRESH)
        self.assertIn("run_watchdog_task:SinFermera11", self.ladder.next_actions(checks))
    def test_state_persists(self):
        for _ in range(2): self.ladder.next_actions(self.unhealthy())
        reloaded = EscalationLadder(self.state, clock=self.clock)
        self.assertIn("reboot", reloaded.next_actions(self.unhealthy()))

import http.client, threading
from farm_agent_core import make_api_server

class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls.srv = make_api_server(
            "127.0.0.1", 0, "sekret",
            status_provider=lambda: {"box": "test", "ok": True},
            action_executor=lambda name, arg=None: (cls.calls.append((name, arg)) or {"ran": name}))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
    def _req(self, method, path, token="sekret", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"X-Farm-Token": token} if token else {}
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    def test_status_ok(self):
        status, body = self._req("GET", "/status")
        self.assertEqual(status, 200); self.assertEqual(body["box"], "test")
    def test_bad_token_401(self):
        self.assertEqual(self._req("GET", "/status", token="wrong")[0], 401)
        self.assertEqual(self._req("GET", "/status", token=None)[0], 401)
    def test_action_dispatch(self):
        status, body = self._req("POST", "/action/restart-windowchecker")
        self.assertEqual(status, 200); self.assertEqual(body["ran"], "restart_windowchecker")
    def test_watchdog_action_carries_user(self):
        self._req("POST", "/action/restart-watchdog/SinFermera11")
        self.assertIn(("run_watchdog_task", "SinFermera11"), self.calls)
    def test_watchdog_action_missing_user_400(self):
        self.assertEqual(self._req("POST", "/action/restart-watchdog")[0], 400)
    def test_unknown_404(self):
        self.assertEqual(self._req("POST", "/action/nuke")[0], 404)
        self.assertEqual(self._req("GET", "/nope")[0], 404)

if __name__ == "__main__":
    unittest.main(verbosity=2)
