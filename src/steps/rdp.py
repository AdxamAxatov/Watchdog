"""
rdp.py - RDPClient Automation with Working Directory Fix

FIXED: 
- Loads configuration directly from regions.yaml
- Sets working directory when launching (finds JSON config)
- Handles confirmation dialogs
"""
import os
import sys
from pathlib import Path
import time
import subprocess
import win32gui
import win32con

# Ensure ../ (src) is on sys.path
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from winops import (
    set_dpi_awareness,
    find_window,
    wait_for_window,
    force_foreground,
    assert_foreground,
    pct_to_screen_xy,
    safe_double_click,
)
from utils import load_yaml

TITLE_SUB = "RDP Session Manager"
RDP_EXE_DEFAULT = r"C:\Users\Recruiter\Downloads\RDPClient\RDPClient.exe"

# UI wait times
UI_STABILIZATION_WAIT_S = 5.0
DIALOG_WAIT_S = 1.0


def close_confirmation_dialog(verbose=True):
    """
    Close confirmation dialog by pressing Enter.
    """
    if verbose:
        print("   🔘 Pressing Enter to close dialog...")
    
    import pyautogui
    pyautogui.press('enter')
    time.sleep(0.2)
    pyautogui.press('enter')  # Press twice to be sure
    
    if verbose:
        print("   ✅ Sent Enter keypress")


def launch_rdp_with_workdir(exe_path, verbose=True):
    """
    Launch RDPClient with its directory as working directory.
    This ensures it finds config files (like user accounts JSON).
    """
    if not os.path.exists(exe_path):
        raise RuntimeError(f"RDP exe not found: {exe_path}")
    
    # Get the directory containing RDPClient.exe
    rdp_dir = os.path.dirname(os.path.abspath(exe_path))
    
    if verbose:
        print(f"🚀 Launching RDPClient")
        print(f"   Exe: {exe_path}")
        print(f"   Working dir: {rdp_dir}")
    
    # Launch with working directory set
    subprocess.Popen([exe_path], cwd=rdp_dir)


def run(config=None, context=None):
    """
    Launch RDPClient and double-click User1 and User2 entries.
    
    Automatically loads config from regions.yaml if not provided.
    
    Args:
        config: Optional config dict (for testing)
        context: Optional context (unused)
    
    Returns:
        True if successful
    """
    set_dpi_awareness()

    # Load regions.yaml if config not provided
    if config is None:
        print("📄 Loading configuration from regions.yaml...")
        try:
            regions = load_yaml("config/regions.yaml")
            rdp_config = regions.get("rdp", {})
            paths = regions.get("paths", {})
            
            # Get exe path from paths section
            rdpclient_exe_list = paths.get("rdpclient_exe", [])
            if rdpclient_exe_list:
                exe_path = rdpclient_exe_list[0]
            else:
                exe_path = RDP_EXE_DEFAULT
            
            # Build complete config
            config = {
                "exe_path": exe_path,
                "user1_point_pct": rdp_config.get("user1_point_pct"),
                "user2_point_pct": rdp_config.get("user2_point_pct"),
                "ui_wait_s": rdp_config.get("ui_wait_s", UI_STABILIZATION_WAIT_S),
                "dialog_wait_s": rdp_config.get("dialog_wait_s", DIALOG_WAIT_S),
            }
            
            print(f"✅ Loaded from regions.yaml:")
            print(f"   Exe: {config['exe_path']}")
            print(f"   User1: {config['user1_point_pct']}")
            print(f"   User2: {config['user2_point_pct']}")
            print(f"   UI wait: {config['ui_wait_s']}s")
            
        except Exception as e:
            raise RuntimeError(f"Failed to load regions.yaml: {e}")
    
    cfg = config or {}
    exe_path = cfg.get("exe_path", RDP_EXE_DEFAULT)
    
    # Get user click positions from config (REQUIRED!)
    user1 = cfg.get("user1_point_pct")
    user2 = cfg.get("user2_point_pct")
    
    if not user1 or not user2:
        raise RuntimeError(
            "RDP: user1_point_pct and user2_point_pct are required!\n"
            "Add them to regions.yaml under 'rdp' section:\n"
            "rdp:\n"
            "  user1_point_pct:\n"
            "    x: 0.1902\n"
            "    y: 0.2754\n"
            "  user2_point_pct:\n"
            "    x: 0.2366\n"
            "    y: 0.3300\n"
        )
    
    ui_wait_s = cfg.get("ui_wait_s", UI_STABILIZATION_WAIT_S)
    dialog_wait_s = cfg.get("dialog_wait_s", DIALOG_WAIT_S)

    print("\n" + "=" * 70)
    print("🖥️  RDPCLIENT AUTOMATION")
    print("=" * 70)

    # 1) Find or launch RDPClient
    m = find_window(TITLE_SUB, require_visible=True)
    if not m:
        print("⚠️  RDPClient window not found, launching...")
        
        # Launch with working directory set (CRITICAL FIX!)
        launch_rdp_with_workdir(exe_path, verbose=True)
        
        print("⏳ Waiting for RDPClient window (25s timeout)...")
        m = wait_for_window(TITLE_SUB, timeout_s=25.0, require_visible=True)

    if not m:
        print("❌ RDPClient window not found after launch")
        raise RuntimeError("RDP: window not found after launch.")

    print(f"\n✅ Found RDPClient Window")
    print(f"   HWND: {m.hwnd}")
    print(f"   Title: {m.title}")

    # 2) Wait for UI and user list to fully load
    print(f"\n⏳ Waiting {ui_wait_s}s for UI and user list to load...")
    print("   (This allows time for user accounts JSON to be read)")
    time.sleep(ui_wait_s)

    # 3) Force focus before first click
    print("\n🎯 Focusing RDPClient window...")
    if not force_foreground(m.hwnd):
        print("❌ Failed to focus RDPClient")
        raise RuntimeError("RDP: could not foreground (safety stop).")
    
    assert_foreground(m.hwnd)
    print("✅ Window focused")
    
    time.sleep(0.3)

    # 4) Double-click User1
    x1, y1 = pct_to_screen_xy(m.hwnd, float(user1["x"]), float(user1["y"]))
    
    print(f"\n🖱️  Double-clicking User1")
    print(f"   Position: ({user1['x']:.4f}, {user1['y']:.4f}) → screen ({x1}, {y1})")
    
    safe_double_click(x1, y1)
    print("✅ User1 double-clicked")
    
    # 4a) Close confirmation dialog
    print(f"\n⏳ Waiting {dialog_wait_s}s for confirmation dialog...")
    time.sleep(dialog_wait_s)
    close_confirmation_dialog(verbose=True)
    time.sleep(0.5)

    # 5) Refocus RDPClient for second click
    print("\n🎯 Re-focusing RDPClient window...")
    if not force_foreground(m.hwnd):
        print("❌ Failed to re-focus RDPClient after User1")
        raise RuntimeError("RDP: could not refocus after user1 (safety stop).")
    
    assert_foreground(m.hwnd)
    print("✅ Window re-focused")
    
    time.sleep(0.3)

    # 6) Double-click User2
    x2, y2 = pct_to_screen_xy(m.hwnd, float(user2["x"]), float(user2["y"]))
    
    print(f"\n🖱️  Double-clicking User2")
    print(f"   Position: ({user2['x']:.4f}, {user2['y']:.4f}) → screen ({x2}, {y2})")
    
    safe_double_click(x2, y2)
    print("✅ User2 double-clicked")
    
    # 6a) Close confirmation dialog
    print(f"\n⏳ Waiting {dialog_wait_s}s for confirmation dialog...")
    time.sleep(dialog_wait_s)
    close_confirmation_dialog(verbose=True)
    time.sleep(0.5)

    print("\n" + "=" * 70)
    print("✅ RDPCLIENT AUTOMATION COMPLETE")
    print("=" * 70)
    print("Both RDP sessions should now be opening\n")

    return True


if __name__ == "__main__":
    # Test - will automatically load from regions.yaml
    run()