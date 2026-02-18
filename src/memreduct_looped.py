import time
from steps.memreduct import run as mem_clean
from utils import load_yaml

def main():
    cfg = load_yaml("config/regions.yaml")
    paths = cfg.get("paths", {})
    mem_exe = paths["memreduct_exe"][0]
    
    while True:
        try:
            mem_clean({"exe_path": mem_exe})
        except Exception as e:
            print(f"⚠️  Error: {e}")
            print("   Continuing anyway...")
        
        print("💤 Sleeping 10 min...")
        time.sleep(10 * 60)

if __name__ == "__main__":
    main()