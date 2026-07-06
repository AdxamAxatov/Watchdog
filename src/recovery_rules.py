"""Pure decision rules for RDP recovery. NO win32 imports — unit-testable anywhere.

These rules exist because the 7/4 + 7/6 host-67 outages traced to (a) killing
by hwnd-derived pid without checking the owning image (ghost windows belong to
dwm.exe) and (b) treating click-dispatch as proof of a disconnect."""

MIN_FOCUS_INTERVAL_MINUTES = 10.0
KILL_IMAGE_ALLOWLIST = frozenset({"wfreerdp.exe"})
NEVER_KILL_IMAGES = frozenset({"dwm.exe", "explorer.exe"})


def effective_focus_interval(value, default=30.0):
    """Clamp the cycle interval to the floor; garbage -> default."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(v, MIN_FOCUS_INTERVAL_MINUTES)


def disconnect_confirmed(dialog_seen, old_window_destroyed):
    """A session counts as disconnected ONLY on observed effect (R3)."""
    return bool(dialog_seen) or bool(old_window_destroyed)


def may_kill_process(image_name, allowlist=KILL_IMAGE_ALLOWLIST):
    """True only for explicitly allowlisted images; system processes never (R4)."""
    img = (image_name or "").strip().lower()
    if not img or img in NEVER_KILL_IMAGES:
        return False
    return img in allowlist
