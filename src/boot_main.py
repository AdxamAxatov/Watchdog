from steps.memreduct import run as mem_run
from steps.rdp import run as rdp_run
from utils import load_yaml
import time

def main():
    cfg = load_yaml("config/regions.yaml")
    paths = cfg.get("paths", {})
    
    # MemReduct
    mem_run({"exe_path": paths["memreduct_exe"][0]})
    time.sleep(2)
    
    # RDP - will auto-load coordinates from regions.yaml
    print("2️⃣  Starting RDPClient...")
    rdp_run()  # Simple! No config needed!
    print("   ✅ RDPClient complete\n")

if __name__ == "__main__":
    main()