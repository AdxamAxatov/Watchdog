# """
# expressvpn.py - ExpressVPN Auto-Connect with UI Automation

# PROPER FIX: Uses UI Automation to read the actual button text
# - Button says "Connect" → Click it (disconnected)
# - Button says "Disconnect" → Skip (already connected)
# - Button says "Connecting..." → Wait and skip (in progress)
# """
# import os
# import sys
# from pathlib import Path
# import time

# # Ensure ../ (src) is on sys.path
# _THIS_DIR = Path(__file__).resolve().parent
# _SRC_DIR = _THIS_DIR.parent
# if str(_SRC_DIR) not in sys.path:
#     sys.path.insert(0, str(_SRC_DIR))

# from winops import (
#     set_dpi_awareness,
#     find_window,
#     wait_for_window,
#     launch_exe,
#     force_foreground,
#     assert_foreground,
#     pct_to_screen_xy,
#     safe_click,
# )

# TITLE_SUB = "ExpressVPN"
# EXPRESSVPN_EXE = r"C:\Program Files (x86)\ExpressVPN\expressvpn-ui\ExpressVPN.exe"

# # Calibrated click point (percentage of client area)
# CONNECT_X_PCT = 0.4918
# CONNECT_Y_PCT = 0.3053

# # UI wait time
# UI_WAIT_SECONDS = 12.0


# def get_button_state_uia(hwnd, verbose=True):
#     """
#     Use UI Automation to read the actual button text.
    
#     Returns:
#         "connect" - Button says "Connect" (disconnected)
#         "disconnect" - Button says "Disconnect" (connected)
#         "connecting" - Button says "Connecting..." (in progress)
#         None - Could not determine
#     """
#     if verbose:
#         print("\n🔍 Reading button state via UI Automation...")
    
#     try:
#         from pywinauto import Application
        
#         # Connect to the window
#         app = Application(backend="uia").connect(handle=hwnd)
#         window = app.window(handle=hwnd)
        
#         # Wait for window to be ready
#         window.wait("visible", timeout=5)
        
#         if verbose:
#             print("   ✅ Connected to ExpressVPN window")
        
#         # Search for button elements
#         # ExpressVPN typically has a large button with text
#         buttons = window.descendants(control_type="Button")
        
#         if verbose:
#             print(f"   Found {len(buttons)} button(s)")
        
#         # Look for the main connect/disconnect button
#         for i, button in enumerate(buttons):
#             try:
#                 button_text = button.window_text().lower()
                
#                 if verbose:
#                     print(f"   Button {i+1}: '{button_text}'")
                
#                 # Check for connect/disconnect keywords
#                 if "disconnect" in button_text:
#                     if verbose:
#                         print(f"   ✅ Found DISCONNECT button - VPN is CONNECTED")
#                     return "disconnect"
                
#                 if "connect" in button_text and "disconnect" not in button_text:
#                     if "connecting" in button_text or "..." in button_text:
#                         if verbose:
#                             print(f"   ⏳ Found CONNECTING button - VPN is connecting")
#                         return "connecting"
#                     else:
#                         if verbose:
#                             print(f"   ⚠️  Found CONNECT button - VPN is DISCONNECTED")
#                         return "connect"
                
#             except Exception as e:
#                 if verbose:
#                     print(f"   ⚠️  Error reading button {i+1}: {e}")
#                 continue
        
#         # If no clear button found, try text elements
#         if verbose:
#             print("   Searching text elements...")
        
#         texts = window.descendants(control_type="Text")
#         for i, text_elem in enumerate(texts):
#             try:
#                 text = text_elem.window_text().lower()
#                 if text and len(text) > 3:  # Ignore empty or very short
#                     if verbose and i < 10:  # Print first 10 text elements
#                         print(f"   Text {i+1}: '{text[:50]}'")
                    
#                     if "disconnect" in text or "connected to" in text:
#                         if verbose:
#                             print(f"   ✅ Found 'connected' text - VPN is CONNECTED")
#                         return "disconnect"
                    
#                     if "not connected" in text or "connect to" in text:
#                         if verbose:
#                             print(f"   ⚠️  Found 'not connected' text - VPN is DISCONNECTED")
#                         return "connect"
#             except:
#                 continue
        
#         if verbose:
#             print("   ⚠️  Could not determine button state from UI elements")
        
#         return None
        
#     except Exception as e:
#         if verbose:
#             print(f"   ❌ UI Automation error: {e}")
#         return None


# def get_button_state_inspect(hwnd, verbose=True):
#     """
#     Alternative: Use inspect.exe style deep inspection
#     """
#     if verbose:
#         print("\n🔍 Deep UI inspection...")
    
#     try:
#         from pywinauto import Application
        
#         app = Application(backend="uia").connect(handle=hwnd)
#         window = app.window(handle=hwnd)
        
#         # Print entire UI tree for debugging
#         if verbose:
#             print("   Full UI tree:")
#             window.print_control_identifiers()
        
#         return None
        
#     except Exception as e:
#         if verbose:
#             print(f"   ❌ Inspect error: {e}")
#         return None


# def run(config=None):
#     """
#     Launch ExpressVPN and connect if not already connected.
#     Uses UI Automation to read actual button state.
    
#     Args:
#         config: Optional dict with:
#             - exe_path: Path to ExpressVPN.exe
#             - ui_wait_seconds: How long to wait for UI (default 12s)
#             - force_click: If True, always click regardless of state (default False)
#             - debug_ui: If True, print full UI tree (default False)
#     """
#     cfg = config or {}
#     exe_path = cfg.get("exe_path", EXPRESSVPN_EXE)
#     ui_wait = cfg.get("ui_wait_seconds", UI_WAIT_SECONDS)
#     force_click = cfg.get("force_click", False)
#     debug_ui = cfg.get("debug_ui", False)

#     set_dpi_awareness()

#     print("\n" + "=" * 70)
#     print("🔍 EXPRESSVPN AUTO-CONNECT (UI Automation)")
#     print("=" * 70)

#     # Find or launch ExpressVPN
#     m = find_window(TITLE_SUB, require_visible=True, exe_name_contains="expressvpn.exe")
#     if not m:
#         print("⚠️  ExpressVPN window not found.")
#         if not os.path.exists(exe_path):
#             raise SystemExit(f"❌ Cannot launch - EXE path invalid:\n{exe_path}")

#         print(f"🚀 Launching ExpressVPN: {exe_path}")
#         launch_exe(exe_path)

#         print("⏳ Waiting for ExpressVPN window to appear (30s timeout)...")
#         m = wait_for_window(
#             TITLE_SUB, 
#             timeout_s=30.0, 
#             require_visible=True, 
#             exe_name_contains="expressvpn.exe"
#         )

#     if not m:
#         raise SystemExit("❌ ExpressVPN window still not found after launch.")

#     print("\n✅ Found ExpressVPN Window")
#     print(f"   HWND: {m.hwnd}")
#     print(f"   Title: '{m.title}'")

#     # Wait for UI to load
#     print(f"\n{'='*70}")
#     print(f"⏳ WAITING FOR UI TO LOAD")
#     print("=" * 70)
#     print(f"   Waiting {ui_wait:.1f} seconds for UI to fully load...")
    
#     force_foreground(m.hwnd)
#     time.sleep(0.5)
#     time.sleep(ui_wait)
#     print("✅ UI loaded!")

#     # Focus window
#     print(f"\n{'='*70}")
#     print("🎯 FOCUSING EXPRESSVPN WINDOW")
#     print("=" * 70)
    
#     force_foreground(m.hwnd)
#     assert_foreground(m.hwnd)
#     print("✅ Window focused")
#     time.sleep(0.5)

#     # Debug: Print full UI tree if requested
#     if debug_ui:
#         get_button_state_inspect(m.hwnd, verbose=True)

#     # Read button state via UI Automation
#     button_state = None
    
#     if not force_click:
#         button_state = get_button_state_uia(m.hwnd, verbose=True)
        
#         if button_state == "disconnect":
#             print("\n" + "=" * 70)
#             print("✅ VPN ALREADY CONNECTED")
#             print("=" * 70)
#             print("Button says 'Disconnect' - VPN is connected!")
#             print("Skipping connect button click")
#             print(f"\n{'='*70}")
#             print("✅ DONE")
#             print("=" * 70)
#             print()
#             return
        
#         elif button_state == "connecting":
#             print("\n" + "=" * 70)
#             print("⏳ VPN IS CONNECTING")
#             print("=" * 70)
#             print("Button says 'Connecting...' - VPN connection in progress")
#             print("Skipping connect button click")
#             print(f"\n{'='*70}")
#             print("✅ DONE")
#             print("=" * 70)
#             print()
#             return
        
#         elif button_state == "connect":
#             print("\n" + "=" * 70)
#             print("⚠️  VPN IS DISCONNECTED")
#             print("=" * 70)
#             print("Button says 'Connect' - need to click!")
        
#         else:
#             print("\n" + "=" * 70)
#             print("⚠️  BUTTON STATE UNCLEAR")
#             print("=" * 70)
#             print("Could not read button state from UI")
#             print("Will click connect button to be safe...")
    
#     # Click connect button
#     x, y = pct_to_screen_xy(m.hwnd, CONNECT_X_PCT, CONNECT_Y_PCT)

#     print(f"\n{'='*70}")
#     print("🖱️  CLICKING CONNECT BUTTON")
#     print("=" * 70)
#     print(f"Screen coordinates: ({x}, {y})")
#     print(f"Percentage: ({CONNECT_X_PCT:.4f}, {CONNECT_Y_PCT:.4f})")

#     safe_click(x, y)
    
#     print("\n✅ Connect button clicked!")

#     print(f"\n{'='*70}")
#     print("✅ DONE")
#     print("=" * 70)
#     print()


# if __name__ == "__main__":
#     # For debugging, enable UI tree printing
#     run({"debug_ui": False})  # Set to True to see full UI tree