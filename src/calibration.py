#!/usr/bin/env python3
"""
Enhanced Calibration Tool for FSM Panel First-Run Clicks

Features:
- Point calibration (single click position)
- Region calibration (rectangular area)
- Test mode (visually verify coords by clicking them)
- Copy-paste ready YAML output
- Visual feedback with mouse position tracking
- Validation and error checking
"""

import time
import ctypes
import win32gui
import win32con
import pyautogui
import sys

def set_dpi_awareness():
    """Set DPI awareness to get accurate pixel coordinates."""
    try:
        # Per-monitor v2 (best)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "Per-monitor v2"
    except Exception:
        try:
            # Per-monitor (Win 8.1+)
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return "Per-monitor"
        except Exception:
            try:
                # System DPI aware (legacy)
                ctypes.windll.user32.SetProcessDPIAware()
                return "System DPI"
            except Exception:
                return "None (might have issues)"

def get_active_window():
    """Get the currently focused window and its dimensions."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        raise SystemExit("❌ No active window found!")
    
    # Get window title for confirmation
    title = win32gui.GetWindowText(hwnd)
    
    # Get client area dimensions
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
    cw = cr - cl
    ch = cb - ct
    
    # Get client area origin in screen coordinates
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    
    return hwnd, cw, ch, cx, cy, title

def countdown(msg, s=3, show_mouse=True):
    """Countdown with optional mouse position display."""
    print(f"\n{msg}")
    for i in range(s, 0, -1):
        if show_mouse:
            mx, my = pyautogui.position()
            print(f"  {i}s... (mouse at screen: {mx}, {my})", end='\r')
        else:
            print(f"  {i}s...", end='\r')
        time.sleep(1)
    print()  # New line after countdown

def calibrate_point(cw, ch, cx, cy):
    """Calibrate a single point and return percentage coordinates."""
    countdown("🎯 Move mouse to TARGET POINT", 3, show_mouse=True)
    mx, my = pyautogui.position()
    
    # Calculate percentage
    x_pct = (mx - cx) / cw
    y_pct = (my - cy) / ch
    
    # Validate (should be between 0 and 1)
    if not (0 <= x_pct <= 1 and 0 <= y_pct <= 1):
        print(f"⚠️  WARNING: Coordinates outside window bounds!")
        print(f"   Mouse was at screen ({mx}, {my})")
        print(f"   Window client area: ({cx}, {cy}) to ({cx+cw}, {cy+ch})")
        print(f"   Calculated: x_pct={x_pct:.4f}, y_pct={y_pct:.4f}")
        if input("   Continue anyway? (y/n): ").lower() != 'y':
            return None
    
    return {
        "x_pct": round(x_pct, 4),
        "y_pct": round(y_pct, 4),
        "screen_x": mx,
        "screen_y": my,
        "client_x": mx - cx,
        "client_y": my - cy,
    }

def calibrate_region(cw, ch, cx, cy):
    """Calibrate a rectangular region and return percentage coordinates."""
    countdown("📍 Move mouse to TOP-LEFT corner", 3, show_mouse=True)
    tlx, tly = pyautogui.position()
    
    countdown("📍 Move mouse to BOTTOM-RIGHT corner", 3, show_mouse=True)
    brx, bry = pyautogui.position()
    
    # Calculate region
    x = min(tlx, brx) - cx
    y = min(tly, bry) - cy
    w = abs(brx - tlx)
    h = abs(bry - tly)
    
    # Convert to percentages
    x_pct = x / cw
    y_pct = y / ch
    w_pct = w / cw
    h_pct = h / ch
    
    # Validate
    if not (0 <= x_pct <= 1 and 0 <= y_pct <= 1):
        print(f"⚠️  WARNING: Region starts outside window bounds!")
    
    if w_pct <= 0 or h_pct <= 0:
        print(f"❌ ERROR: Invalid region size (width or height is 0 or negative)")
        return None
    
    return {
        "x": round(x_pct, 4),
        "y": round(y_pct, 4),
        "w": round(w_pct, 4),
        "h": round(h_pct, 4),
        "screen_coords": f"({min(tlx,brx)}, {min(tly,bry)}) to ({max(tlx,brx)}, {max(tly,bry)})",
        "pixel_size": f"{w}x{h}",
    }

def test_point(hwnd, cw, ch, cx, cy, x_pct, y_pct):
    """Test a point by clicking it visually (moves mouse but doesn't actually click)."""
    # Calculate screen coordinates from percentages
    screen_x = cx + int(cw * x_pct)
    screen_y = cy + int(ch * y_pct)
    
    print(f"\n🧪 Testing point ({x_pct:.4f}, {y_pct:.4f})")
    print(f"   Will move to screen coordinates: ({screen_x}, {screen_y})")
    
    # Make sure window is focused
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception as e:
        print(f"   ⚠️  Could not focus window: {e}")
    
    countdown("Moving mouse to calculated position", 2, show_mouse=False)
    
    # Move to the point (with smooth animation)
    pyautogui.moveTo(screen_x, screen_y, duration=0.5)
    
    print(f"   ✅ Mouse moved to ({screen_x}, {screen_y})")
    print(f"   👀 Check if this is the correct position!")
    print(f"   Press Enter when done viewing...")
    input()

def print_yaml_output(mode, data, name=""):
    """Print copy-paste ready YAML output."""
    print("\n" + "="*70)
    print("📋 COPY THIS TO YOUR regions.yaml:")
    print("="*70)
    
    if mode == "point":
        print(f"\n# {name or 'Calibrated Point'}")
        print(f"x_pct: {data['x_pct']}")
        print(f"y_pct: {data['y_pct']}")
        
        print(f"\n# For first_run clicks, use this format:")
        print(f"- {{ x_pct: {data['x_pct']}, y_pct: {data['y_pct']}, wait_s: 0.8 }}")
        
    elif mode == "region":
        print(f"\n# {name or 'Calibrated Region'}")
        print(f"detect_region:")
        print(f"  x: {data['x']}")
        print(f"  y: {data['y']}")
        print(f"  w: {data['w']}")
        print(f"  h: {data['h']}")
    
    print("\n" + "="*70)

def print_diagnostics(data):
    """Print diagnostic information."""
    print("\n📊 Diagnostic Info:")
    if 'screen_x' in data:
        print(f"   Screen position: ({data['screen_x']}, {data['screen_y']})")
        print(f"   Client position: ({data['client_x']}, {data['client_y']})")
        print(f"   Percentage: ({data['x_pct']:.4f}, {data['y_pct']:.4f})")
    elif 'screen_coords' in data:
        print(f"   Screen coords: {data['screen_coords']}")
        print(f"   Pixel size: {data['pixel_size']}")
        print(f"   Percentage: x={data['x']:.4f}, y={data['y']:.4f}, w={data['w']:.4f}, h={data['h']:.4f}")

def main():
    print("="*70)
    print("🎯 ENHANCED CALIBRATION TOOL - FSM Panel First-Run Setup")
    print("="*70)
    
    # Set DPI awareness
    dpi_mode = set_dpi_awareness()
    print(f"✅ DPI Awareness: {dpi_mode}")
    
    # Get window
    countdown("🪟 Focus the TARGET WINDOW", 3, show_mouse=False)
    
    try:
        hwnd, cw, ch, cx, cy, title = get_active_window()
    except SystemExit as e:
        print(str(e))
        return
    
    print(f"\n✅ Window captured:")
    print(f"   Title: {title}")
    print(f"   HWND: {hwnd}")
    print(f"   Client size: {cw}x{ch}")
    print(f"   Client origin: ({cx}, {cy})")
    
    # Main menu
    while True:
        print("\n" + "="*70)
        print("CALIBRATION MODE:")
        print("  p = Calibrate single POINT (for clicks)")
        print("  r = Calibrate REGION (for OCR detection)")
        print("  t = TEST existing coordinates")
        print("  m = Calibrate MULTIPLE points (for first-run sequence)")
        print("  q = QUIT")
        print("="*70)
        
        mode = input("Select mode: ").strip().lower()
        
        if mode == 'q':
            print("👋 Goodbye!")
            break
        
        elif mode == 'p':
            # Single point calibration
            result = calibrate_point(cw, ch, cx, cy)
            if result:
                name = input("\nName for this point (optional): ").strip()
                print_diagnostics(result)
                print_yaml_output("point", result, name)
                
                # Offer to test
                if input("\n🧪 Test this point? (y/n): ").lower() == 'y':
                    test_point(hwnd, cw, ch, cx, cy, result['x_pct'], result['y_pct'])
        
        elif mode == 'r':
            # Region calibration
            result = calibrate_region(cw, ch, cx, cy)
            if result:
                name = input("\nName for this region (optional): ").strip()
                print_diagnostics(result)
                print_yaml_output("region", result, name)
        
        elif mode == 't':
            # Test existing coordinates
            print("\n🧪 TEST MODE")
            try:
                x_pct = float(input("Enter x_pct: "))
                y_pct = float(input("Enter y_pct: "))
                test_point(hwnd, cw, ch, cx, cy, x_pct, y_pct)
            except ValueError:
                print("❌ Invalid input. Please enter numbers.")
        
        elif mode == 'm':
            # Multiple points for first-run sequence
            print("\n🎯 MULTIPLE POINTS MODE")
            print("   This will help you calibrate all first-run clicks at once.")
            
            try:
                num_clicks = int(input("\nHow many clicks in the sequence? "))
            except ValueError:
                print("❌ Invalid number")
                continue
            
            clicks = []
            for i in range(1, num_clicks + 1):
                print(f"\n--- Click {i}/{num_clicks} ---")
                result = calibrate_point(cw, ch, cx, cy)
                if result:
                    wait_s = input(f"Wait time after this click (default 0.8s): ").strip()
                    wait_s = float(wait_s) if wait_s else 0.8
                    
                    clicks.append({
                        'x_pct': result['x_pct'],
                        'y_pct': result['y_pct'],
                        'wait_s': wait_s
                    })
                else:
                    print("❌ Skipping this click")
            
            # Print complete YAML for first_run clicks
            print("\n" + "="*70)
            print("📋 COMPLETE FIRST-RUN CONFIGURATION:")
            print("="*70)
            print("\npanel:")
            print("  first_run:")
            print("    clicks:")
            for i, click in enumerate(clicks, 1):
                print(f"      - {{ x_pct: {click['x_pct']}, y_pct: {click['y_pct']}, wait_s: {click['wait_s']} }}  # Click {i}")
            print("\n" + "="*70)
            
            # Offer to test all clicks
            if input("\n🧪 Test all clicks in sequence? (y/n): ").lower() == 'y':
                for i, click in enumerate(clicks, 1):
                    print(f"\n--- Testing Click {i}/{len(clicks)} ---")
                    test_point(hwnd, cw, ch, cx, cy, click['x_pct'], click['y_pct'])
        
        else:
            print("❌ Invalid option. Please choose p, r, t, m, or q")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user. Goodbye!")
        sys.exit(0)