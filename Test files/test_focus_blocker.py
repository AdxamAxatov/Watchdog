"""Unit tests for the focus-blocker kill allowlist (watchdog.dismiss_focus_blocker).

The kill DECISION is pure set-membership; win32 calls are not exercised here
(covered by the live drill). We assert the allowlist/never-kill invariants that
keep the fix from ever taskkilling the panel/shell/farm stack."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# watchdog.py imports win32/cv2 at module load, which aren't present on macOS.
# Read the allowlist constants directly from source so the invariants are
# testable anywhere without importing the whole module.
import ast, re
SRC = (Path(__file__).resolve().parent.parent / "src" / "watchdog.py").read_text()

def _const_set(name):
    m = re.search(name + r"\s*=\s*(\{[^}]*\})", SRC)
    assert m, f"{name} not found in watchdog.py"
    return {s.lower() for s in ast.literal_eval(m.group(1))}

KILL = _const_set("_BLOCKER_KILL_ALLOWLIST")
NEVER = _const_set("_BLOCKER_NEVER_KILL")


class TestBlockerAllowlist(unittest.TestCase):
    def test_scoobe_broker_is_killable(self):
        # the confirmed host-62 culprit
        self.assertIn("useroobebroker.exe", KILL)

    def test_common_uwp_hosts_killable(self):
        for p in ("applicationframehost.exe", "systemsettings.exe"):
            self.assertIn(p, KILL)

    def test_farm_and_shell_never_killable(self):
        for p in ("explorer.exe", "dwm.exe", "winlogon.exe", "cs2.exe",
                  "steam.exe", "watchdog.exe", "windowchecker.exe"):
            self.assertIn(p, NEVER)

    def test_allowlist_and_neverkill_are_disjoint(self):
        # a process must never be both killable and protected
        self.assertEqual(KILL & NEVER, set())

    def test_dangerous_names_absent_from_kill(self):
        for p in ("cs2.exe", "steam.exe", "explorer.exe", "dwm.exe"):
            self.assertNotIn(p, KILL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
