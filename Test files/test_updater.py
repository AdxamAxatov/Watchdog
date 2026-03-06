# """
# Quick test script to check if update detection works
# Run this to diagnose update detection issues
# """

# import sys
# import os
# from pathlib import Path

# # Add parent directory to path
# sys.path.insert(0, str(Path(__file__).parent))

# from auto_updater import GitHubAutoUpdater

# def test_update_detection():
#     print("="*70)
#     print("UPDATE DETECTION TEST")
#     print("="*70)
    
#     # Create updater
#     updater = GitHubAutoUpdater()
    
#     if not updater.enabled:
#         print("❌ Auto-updater is DISABLED")
#         print("   Check: update_config.yaml has 'enabled: true'")
#         return
    
#     print(f"✅ Auto-updater enabled")
#     print(f"   Repo: {updater.repo_owner}/{updater.repo_name}")
#     print(f"   Current version: {updater.current_version}")
#     print(f"   Executable: {updater.executable_name}")
#     print()
    
#     # Check if .last_check exists
#     if updater.last_check_file.exists():
#         import time
#         last_check = updater.last_check_file.stat().st_mtime
#         hours_since = (time.time() - last_check) / 3600
#         print(f"⚠️  Last check: {hours_since:.2f} hours ago")
#         print(f"   Interval: {updater.check_interval_hours} hour(s)")
        
#         if hours_since < updater.check_interval_hours:
#             print(f"   ❌ Too soon to check again!")
#             print(f"   Delete: {updater.last_check_file}")
#             print(f"   Then run again")
            
#             # Offer to delete
#             response = input("\n   Delete .last_check and force check now? (y/n): ")
#             if response.lower() == 'y':
#                 updater.last_check_file.unlink()
#                 print("   ✅ Deleted! Checking now...")
#             else:
#                 return
    
#     print()
#     print("Fetching latest release from GitHub...")
    
#     # Get latest release
#     release = updater.get_latest_release()
    
#     if not release:
#         print("❌ Failed to fetch release from GitHub")
#         print("   Possible issues:")
#         print("   - No internet connection")
#         print("   - GitHub is down")
#         print("   - No releases published")
#         print("   - Rate limited (add github_token)")
#         return
    
#     latest_version = release['version']
#     print(f"✅ GitHub latest version: {latest_version}")
#     print()
    
#     # Version comparison
#     is_newer = updater.version_is_newer(latest_version)
#     print(f"Version comparison:")
#     print(f"   Current: {updater.current_version}")
#     print(f"   GitHub:  {latest_version}")
#     print(f"   Newer?   {is_newer}")
#     print()
    
#     if not is_newer:
#         print("✅ Already up to date - no update needed")
#         return
    
#     # Check for matching asset
#     print("Checking for Watchdog.exe in release assets...")
#     asset = updater.find_matching_asset(release['assets'])
    
#     if not asset:
#         print(f"❌ No matching executable found!")
#         print(f"   Looking for: {updater.executable_name}")
#         print(f"   Assets in release:")
#         for a in release['assets']:
#             print(f"      - {a['name']}")
#         return
    
#     print(f"✅ Found: {asset['name']}")
#     print(f"   Size: {asset['size']:,} bytes")
#     print(f"   URL: {asset['url'][:50]}...")
#     print()
    
#     # SHA256 check
#     sha256 = updater.extract_sha256_from_release(release.get('body', ''))
#     if sha256:
#         print(f"✅ SHA256 found in release notes")
#         print(f"   {sha256}")
#     else:
#         print(f"⚠️  No SHA256 in release notes (will skip verification)")
    
#     print()
#     print("="*70)
#     print("RESULT: Update should be detected!")
#     print("="*70)
#     print()
#     print("To trigger actual update:")
#     print("1. Run Watchdog.exe")
#     print("2. Should see: 'Update available: v{updater.current_version} -> v{latest_version}'")
#     print("3. Will download and install automatically")

# if __name__ == "__main__":
#     try:
#         test_update_detection()
#     except Exception as e:
#         print(f"\n❌ Error: {e}")
#         import traceback
#         traceback.print_exc()


# """
# Full diagnostic - run this to see exactly what's happening
# """

# import sys
# from pathlib import Path

# # Add to path
# sys.path.insert(0, str(Path.cwd() / "src"))

# from auto_updater import GitHubAutoUpdater

# print("="*70)
# print("FULL UPDATE DIAGNOSTIC")
# print("="*70)

# updater = GitHubAutoUpdater()

# if not updater.enabled:
#     print("\n❌ Auto-updater DISABLED")
#     print("Check: config/update_config.yaml has 'enabled: true'")
#     sys.exit(1)

# print(f"\n✅ Auto-updater enabled")
# print(f"   Repo: {updater.repo_owner}/{updater.repo_name}")
# print(f"   Current version: {updater.current_version}")
# print(f"   Executable: {updater.executable_name}")

# # Force check (ignore interval)
# print("\n" + "="*70)
# print("FORCING UPDATE CHECK (ignoring interval)")
# print("="*70)

# result = updater.check_and_update(force=True)

# print(f"\nResult:")
# print(f"  checked: {result.get('checked')}")
# print(f"  current_version: {result.get('current_version')}")
# print(f"  latest_version: {result.get('latest_version')}")
# print(f"  update_available: {result.get('update_available')}")
# print(f"  downloaded: {result.get('downloaded')}")
# print(f"  ready_to_apply: {result.get('ready_to_apply')}")
# print(f"  error: {result.get('error')}")

# if result.get('error'):
#     print(f"\n❌ ERROR: {result['error']}")
    
#     if 'Interval' in result['error']:
#         print("   This shouldn't happen with force=True!")
#     elif 'Failed to fetch' in result['error']:
#         print("   Check internet connection")
#         print("   Or GitHub API might be down")
#     elif 'No asset' in result['error']:
#         print("   GitHub release doesn't have Watchdog.exe")
#     elif 'Verification failed' in result['error']:
#         print("   Downloaded file is corrupted or wrong size")
    
# elif result.get('update_available'):
#     print(f"\n✅ Update available: {result['current_version']} -> {result['latest_version']}")
    
#     if result.get('downloaded'):
#         print("✅ Downloaded successfully")
        
#         if result.get('ready_to_apply'):
#             print("✅ Update should have started!")
#             print("\n⚠️  If you see this message, the update script didn't exit properly")
#             print("   The update helper script should have been launched")
            
#             # Check if helper script exists
#             helper = updater.temp_dir / "update_helper.py"
#             if helper.exists():
#                 print(f"\n   Helper script exists: {helper}")
#                 print("   Try running it manually:")
#                 print(f"   python {helper}")
#             else:
#                 print(f"\n   ❌ Helper script NOT found at: {helper}")
#         else:
#             print("❌ Failed to apply update")
#     else:
#         print("❌ Download failed")
        
# elif result.get('checked'):
#     print(f"\n✅ Already up to date: v{result['current_version']}")
# else:
#     print("\n❌ Update check didn't run")

# print("\n" + "="*70)

# test_check.py
import sys
sys.path.insert(0, 'src')

from auto_updater import GitHubAutoUpdater

updater = GitHubAutoUpdater()
print(f"Enabled: {updater.enabled}")
print(f"Current version: {updater.current_version}")

# Force check
result = updater.check_and_update(force=True)

print(f"\nResult:")
print(f"  Checked: {result.get('checked')}")
print(f"  Latest version: {result.get('latest_version')}")
print(f"  Update available: {result.get('update_available')}")
print(f"  Error: {result.get('error')}")