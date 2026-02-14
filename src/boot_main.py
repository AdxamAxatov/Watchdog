from steps.expressvpn import run as vpn_run
from steps.memreduct import run as mem_run
from steps.rdp import run as rdp_run
from utils import load_yaml
import time

def main():
    cfg = load_yaml("config/regions.yaml")
    paths = cfg.get("paths", {})

    # ExpressVPN
    vpn_run({"exe_path": paths["expressvpn_exe"][0]})
    time.sleep(5)  # Wait for VPN to connect
    
    # MemReduct
    mem_run({"exe_path": paths["memreduct_exe"][0]})
    time.sleep(2)
    
    # RDP - will auto-load coordinates from regions.yaml
    rdp_run()  # Simple! No config needed!

if __name__ == "__main__":
    main()