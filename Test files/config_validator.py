"""
config_validator.py - Validates configuration files and paths

Run this before starting the automation system to catch configuration errors early.
"""
import os
import sys
from pathlib import Path
import yaml

def load_yaml(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def validate_config():
    """Validate all configuration files and paths."""
    errors = []
    warnings = []
    
    print("="*70)
    print("🔍 CONFIGURATION VALIDATOR")
    print("="*70)
    
    # Check if config files exist
    config_dir = Path("config")
    if not config_dir.exists():
        errors.append("config/ directory not found")
        return errors, warnings
    
    app_yaml = config_dir / "app.yaml"
    regions_yaml = config_dir / "regions.yaml"
    
    if not app_yaml.exists():
        errors.append("config/app.yaml not found")
    
    if not regions_yaml.exists():
        errors.append("config/regions.yaml not found")
    
    if errors:
        return errors, warnings
    
    # Load configs
    try:
        app = load_yaml(app_yaml)
        regions = load_yaml(regions_yaml)
    except Exception as e:
        errors.append(f"Failed to parse YAML: {e}")
        return errors, warnings
    
    print("\n✅ Configuration files loaded successfully")
    
    # Validate app.yaml structure
    print("\n📋 Validating app.yaml...")
    
    required_app_keys = ["window", "layout", "watchdog"]
    for key in required_app_keys:
        if key not in app:
            errors.append(f"app.yaml missing required key: {key}")
    
    if "window" in app:
        if not app["window"].get("title_substring"):
            warnings.append("app.yaml: window.title_substring is empty")
    
    if "watchdog" in app:
        wd = app["watchdog"]
        if wd.get("warm_timeout_minutes", 0) <= 0:
            errors.append("app.yaml: warm_timeout_minutes must be > 0")
        if wd.get("general_timeout_minutes", 0) <= 0:
            errors.append("app.yaml: general_timeout_minutes must be > 0")
        if wd.get("poll_seconds", 0) <= 0:
            errors.append("app.yaml: poll_seconds must be > 0")
    
    # Validate regions.yaml paths
    print("\n📋 Validating regions.yaml paths...")
    
    if "panel" in regions:
        panel = regions["panel"]
        panel_dir = panel.get("dir")
        
        if panel_dir:
            if not os.path.exists(panel_dir):
                errors.append(f"Panel directory not found: {panel_dir}")
            else:
                # Check for .exe files
                exes = list(Path(panel_dir).glob("*.exe"))
                if not exes:
                    errors.append(f"No .exe files found in panel directory: {panel_dir}")
                else:
                    print(f"   ✅ Found {len(exes)} .exe file(s) in panel directory")
                    print(f"      Latest: {sorted(exes, key=lambda p: p.stat().st_mtime, reverse=True)[0].name}")
        else:
            errors.append("regions.yaml: panel.dir not specified")
        
        # Check first_run configuration
        if "first_run" in panel:
            fr = panel["first_run"]
            if "clicks" in fr:
                clicks = fr["clicks"]
                for i, click in enumerate(clicks):
                    # Check if using absolute coordinates (should use percentages)
                    if "x" in click and "y" in click:
                        if click["x"] > 10 or click["y"] > 10:  # Likely absolute coords
                            warnings.append(
                                f"regions.yaml: panel.first_run.clicks[{i}] uses absolute coordinates "
                                f"(x={click['x']}, y={click['y']}). Consider using x_pct/y_pct for "
                                f"screen resolution independence."
                            )
    
    if "paths" in regions:
        paths = regions["paths"]
        
        # Validate ExpressVPN path
        if "expressvpn_exe" in paths:
            vpn_paths = paths["expressvpn_exe"]
            if isinstance(vpn_paths, list) and vpn_paths:
                vpn_path = vpn_paths[0]
                if not os.path.exists(vpn_path):
                    errors.append(f"ExpressVPN executable not found: {vpn_path}")
                else:
                    print(f"   ✅ ExpressVPN executable found")
        
        # Validate MemReduct path
        if "memreduct_exe" in paths:
            mem_paths = paths["memreduct_exe"]
            if isinstance(mem_paths, list) and mem_paths:
                mem_path = mem_paths[0]
                if not os.path.exists(mem_path):
                    errors.append(f"MemReduct executable not found: {mem_path}")
                else:
                    print(f"   ✅ MemReduct executable found")
        
        # Validate RDPClient path
        if "rdpclient_exe" in paths:
            rdp_paths = paths["rdpclient_exe"]
            if isinstance(rdp_paths, list) and rdp_paths:
                rdp_path = rdp_paths[0]
                if not os.path.exists(rdp_path):
                    errors.append(f"RDPClient executable not found: {rdp_path}")
                else:
                    print(f"   ✅ RDPClient executable found")
    else:
        warnings.append("regions.yaml: 'paths' section not found")
    
    # Validate steam_route
    if "steam_route" in regions:
        sr = regions["steam_route"]
        if sr.get("launch_with_panel"):
            exe = sr.get("exe")
            if exe and not os.path.exists(exe):
                errors.append(f"Steam Route executable not found: {exe}")
            elif exe:
                print(f"   ✅ Steam Route executable found")
    
    # Check for required region definitions
    print("\n📋 Validating region definitions...")
    
    if "log_region_pct" not in regions and "log_region" not in regions:
        errors.append("regions.yaml: Must define either log_region_pct or log_region")
    
    if "button_point_pct" not in regions and "button_point" not in regions:
        errors.append("regions.yaml: Must define either button_point_pct or button_point")
    
    if "log_region_pct" in regions:
        print("   ✅ Using percentage-based log region (resolution independent)")
    elif "log_region" in regions:
        warnings.append("Using absolute pixel log_region. Consider using log_region_pct for resolution independence.")
    
    if "button_point_pct" in regions:
        print("   ✅ Using percentage-based button point (resolution independent)")
    elif "button_point" in regions:
        warnings.append("Using absolute pixel button_point. Consider using button_point_pct for resolution independence.")
    
    return errors, warnings


def main():
    errors, warnings = validate_config()
    
    print("\n" + "="*70)
    print("📊 VALIDATION RESULTS")
    print("="*70)
    
    if warnings:
        print(f"\n⚠️  {len(warnings)} WARNING(S):")
        for w in warnings:
            print(f"   • {w}")
    
    if errors:
        print(f"\n❌ {len(errors)} ERROR(S):")
        for e in errors:
            print(f"   • {e}")
        print("\n❌ Configuration validation FAILED")
        print("Please fix the errors above before running the automation system.")
        sys.exit(1)
    else:
        print("\n✅ Configuration validation PASSED")
        if warnings:
            print("⚠️  Please review the warnings above")
        else:
            print("🎉 No errors or warnings found!")
    
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
