"""
Test script to diagnose why panel .exe is not being found

Run this to see exactly what's happening with your panel directory
"""
import os
from pathlib import Path
import yaml

def load_yaml(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def test_panel_finding():
    print("="*70)
    print("🔍 PANEL EXE FINDER - DIAGNOSTIC TEST")
    print("="*70)
    
    # Load config
    print("\n1️⃣ Loading regions.yaml...")
    try:
        regions = load_yaml("config/regions.yaml")
        print("   ✅ Config loaded successfully")
    except FileNotFoundError:
        print("   ❌ config/regions.yaml not found!")
        return
    except Exception as e:
        print(f"   ❌ Error loading config: {e}")
        return
    
    # Get panel config
    print("\n2️⃣ Reading panel configuration...")
    panel = regions.get("panel", {})
    panel_dir = panel.get("dir")
    
    if not panel_dir:
        print("   ❌ panel.dir is not configured in regions.yaml")
        print("   Add this to regions.yaml:")
        print("   panel:")
        print('     dir: "C:/Users/user/Downloads/FSM_PANEL v.2.8.0"')
        return
    
    print(f"   📂 panel.dir = {panel_dir}")
    
    # Check if directory exists
    print("\n3️⃣ Checking if directory exists...")
    d = Path(panel_dir)
    
    if not d.exists():
        print(f"   ❌ Directory does NOT exist: {panel_dir}")
        print(f"\n   🔍 Let me check some common variations:")
        
        # Try to find similar paths
        parent = d.parent
        if parent.exists():
            print(f"\n   Parent directory exists: {parent}")
            print("   Contents:")
            for item in parent.iterdir():
                if item.is_dir():
                    print(f"      📁 {item.name}")
        else:
            print(f"   ❌ Parent directory also doesn't exist: {parent}")
        
        print("\n   💡 SOLUTION:")
        print("   1. Open File Explorer")
        print("   2. Navigate to your panel folder")
        print("   3. Click the address bar and copy the full path")
        print("   4. Paste it into regions.yaml under panel.dir")
        return
    
    print(f"   ✅ Directory exists!")
    
    # List all files
    print("\n4️⃣ Listing all files in directory...")
    all_files = list(d.iterdir())
    
    if not all_files:
        print("   ⚠️  Directory is empty!")
        return
    
    print(f"   Found {len(all_files)} item(s):")
    for item in all_files[:10]:  # Show first 10
        if item.is_file():
            size_mb = item.stat().st_size / (1024 * 1024)
            print(f"      📄 {item.name} ({size_mb:.1f} MB)")
        else:
            print(f"      📁 {item.name}/")
    
    if len(all_files) > 10:
        print(f"      ... and {len(all_files) - 10} more")
    
    # Find .exe files
    print("\n5️⃣ Searching for .exe files...")
    exes = list(d.glob("*.exe"))
    
    if not exes:
        print("   ❌ No .exe files found!")
        print("\n   Files with other extensions:")
        for item in d.iterdir():
            if item.is_file():
                print(f"      {item.name}")
        return
    
    print(f"   ✅ Found {len(exes)} .exe file(s):")
    
    # Sort by modification time (newest first)
    exes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    
    from datetime import datetime
    
    for i, exe in enumerate(exes, 1):
        mtime = datetime.fromtimestamp(exe.stat().st_mtime)
        size_mb = exe.stat().st_size / (1024 * 1024)
        marker = "⭐ [NEWEST]" if i == 1 else "  "
        print(f"      {marker} {exe.name}")
        print(f"         Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"         Size: {size_mb:.1f} MB")
        print(f"         Full path: {exe}")
    
    # Show what would be launched
    print("\n6️⃣ Result:")
    newest = exes[0]
    print(f"   ✅ Would launch: {newest.name}")
    print(f"   📍 Full path: {newest}")
    
    # Test if file is actually executable
    print("\n7️⃣ Checking if file is accessible...")
    if os.access(str(newest), os.X_OK):
        print("   ✅ File has execute permissions")
    else:
        print("   ⚠️  File might not be executable (could be normal on Windows)")
    
    print("\n" + "="*70)
    print("✅ DIAGNOSTIC COMPLETE")
    print("="*70)
    
    if exes:
        print(f"\n🎯 SUCCESS: Found panel exe: {newest.name}")
        print(f"   The watchdog will launch: {newest}")
    else:
        print("\n❌ PROBLEM: No .exe files found")
        print("   Check the directory path in regions.yaml")

if __name__ == "__main__":
    test_panel_finding()