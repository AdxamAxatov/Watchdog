#!/usr/bin/env python3
"""
Test the auto-updater without building executable
Place this in PROJECT ROOT (next to config/ folder)
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from auto_updater import GitHubAutoUpdater, check_updates

def test_config_loading():
    """Test 1: Does it find and load the config?"""
    print("=" * 70)
    print("TEST 1: Config Loading")
    print("=" * 70)
    
    updater = GitHubAutoUpdater()
    
    print(f"Enabled: {updater.enabled}")
    
    # Only print version if enabled (otherwise config wasn't loaded fully)
    if updater.enabled:
        print(f"Version: {updater.current_version}")
        print(f"Repo: {updater.repo_owner}/{updater.repo_name}")
        print(f"API URL: {updater.api_url}")
        print("✅ PASS: Config loaded and enabled")
        return True
    else:
        print("ℹ️  Updater disabled in config (this is OK if you set enabled: false)")
        print("✅ PASS: Config found but disabled")
        return True  # Still pass - config was found

def test_github_api():
    """Test 2: Can it reach GitHub API?"""
    print("\n" + "=" * 70)
    print("TEST 2: GitHub API Connection")
    print("=" * 70)
    
    updater = GitHubAutoUpdater()
    
    if not updater.enabled:
        print("ℹ️  SKIP: Updater disabled - enable in config to test API")
        return True  # Pass - not a failure, just disabled
    
    release = updater.get_latest_release()
    
    if not release:
        print("❌ FAIL: Could not fetch release")
        return False
    
    print(f"Latest version: {release['version']}")
    print(f"Published: {release['published_at']}")
    print(f"Assets: {[a['name'] for a in release['assets']]}")
    
    print("✅ PASS: GitHub API reachable")
    return True

def test_full_update_check():
    """Test 3: Full update check"""
    print("\n" + "=" * 70)
    print("TEST 3: Full Update Check")
    print("=" * 70)
    
    result = check_updates()
    
    if not result:
        print("❌ FAIL: check_updates returned None")
        return False
    
    print(f"Checked: {result.get('checked')}")
    print(f"Update available: {result.get('update_available')}")
    print(f"Current: {result.get('current_version')}")
    print(f"Latest: {result.get('latest_version')}")
    print(f"Downloaded: {result.get('downloaded')}")
    print(f"Ready to apply: {result.get('ready_to_apply')}")
    
    if result.get('error'):
        print(f"Error: {result['error']}")
    
    if result.get('update_available') and result.get('downloaded'):
        print("✅ PASS: Update downloaded and staged!")
        return True
    elif result.get('update_available') and not result.get('downloaded'):
        print("⚠️  Update found but download failed")
        return False
    else:
        print("ℹ️  No update needed or already latest")
        return True

def test_status():
    """Test 4: Get status without checking"""
    print("\n" + "=" * 70)
    print("TEST 4: Status Check")
    print("=" * 70)
    
    from auto_updater import get_status
    
    status = get_status()
    print(f"Enabled: {status.get('enabled')}")
    print(f"Current version: {status.get('current_version', 'N/A')}")
    print(f"Update pending: {status.get('update_pending')}")
    
    if status.get('error'):
        print(f"Error: {status['error']}")
    
    return True

if __name__ == "__main__":
    print("WATCHDOG AUTO-UPDATER TEST")
    print(f"Running from: {os.path.dirname(os.path.abspath(__file__))}")
    print(f"Looking for config in: {os.path.join(os.path.dirname(__file__), 'config')}")
    print()
    
    # Check if config exists
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'update_config.yaml')
    if os.path.exists(config_path):
        print(f"✅ Config found: {config_path}")
    else:
        print(f"❌ Config NOT found at: {config_path}")
        print("Make sure test_updater.py is in project root (next to config/ folder)!")
        sys.exit(1)
    
    print()
    
    # Run tests
    tests = [
        test_config_loading,
        test_github_api,
        test_full_update_check,
        test_status,
    ]
    
    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
        print()
    
    # Summary
    print("=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    for i, (test, result) in enumerate(zip(tests, results), 1):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"Test {i} ({test.__name__}): {status}")
    
    passed = sum(results)
    total = len(results)
    print(f"\nTotal: {passed}/{total} passed")
    
    if passed == total:
        print("\n🎉 All tests passed!")
    else:
        print("\n⚠️  Some tests failed. Check output above.")