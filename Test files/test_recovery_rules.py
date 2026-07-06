"""Unit tests for src/recovery_rules.py (pure logic — runs anywhere)."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from recovery_rules import (effective_focus_interval, disconnect_confirmed,
                            may_kill_process, MIN_FOCUS_INTERVAL_MINUTES)

class TestFocusIntervalFloor(unittest.TestCase):
    def test_below_floor_clamped(self):
        self.assertEqual(effective_focus_interval(1), MIN_FOCUS_INTERVAL_MINUTES)
    def test_above_floor_kept(self):
        self.assertEqual(effective_focus_interval(30), 30.0)
    def test_garbage_falls_back_to_default(self):
        self.assertEqual(effective_focus_interval("bogus"), 30.0)
        self.assertEqual(effective_focus_interval(None), 30.0)

class TestDisconnectConfirmed(unittest.TestCase):
    def test_dialog_alone_confirms(self):
        self.assertTrue(disconnect_confirmed(True, False))
    def test_destroyed_window_alone_confirms(self):
        self.assertTrue(disconnect_confirmed(False, True))
    def test_click_dispatch_alone_never_confirms(self):
        self.assertFalse(disconnect_confirmed(False, False))

class TestKillAllowlist(unittest.TestCase):
    def test_renderer_killable(self):
        self.assertTrue(may_kill_process("wfreerdp.exe"))
        self.assertTrue(may_kill_process("WFreeRDP.EXE"))
    def test_dwm_never(self):
        self.assertFalse(may_kill_process("dwm.exe"))
    def test_explorer_never(self):
        self.assertFalse(may_kill_process("explorer.exe"))
    def test_unknown_and_empty_never(self):
        self.assertFalse(may_kill_process("cs2.exe"))
        self.assertFalse(may_kill_process(""))
        self.assertFalse(may_kill_process(None))

if __name__ == "__main__":
    unittest.main(verbosity=2)
