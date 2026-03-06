"""
Auto-updater for Watchdog executables from GitHub Releases
Reads configuration from external YAML file (no rebuild required)
"""

import os
import sys
import time
import tempfile
import subprocess
import logging
import hashlib
import re
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Optional imports - don't crash if missing
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    logger.warning("requests module not available - auto-updater disabled")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    logger.warning("yaml module not available - auto-updater disabled")


class GitHubAutoUpdater:
    # FIXED Issue #5: Correct defaults matching your repo
    DEFAULT_CONFIG = {
        'enabled': False,
        'repo_owner': 'AdxamAxatov',
        'repo_name': 'Watchdog',
        'current_version': '1.0.0',
        'executable_name': 'Watchdog.exe',
        'check_interval_hours': 1,
        'silent_mode': True,
        'github_api_timeout': 10,
        'download_timeout': 60,
        'github_token': None,
    }
    
    def __init__(self, config_path: Optional[str] = None):
        # Check dependencies
        if not HAS_REQUESTS or not HAS_YAML:
            logger.error("Missing dependencies (requests/yaml) - auto-updater disabled")
            self.enabled = False
            return
            
        self.config = self._load_config(config_path)
        
        if not self.config.get('enabled', False):
            logger.info("Auto-updater disabled in config")
            self.enabled = False
            return
            
        self.enabled = True
        self._apply_config()
        self._setup_paths()
        
        # FIXED Issue #8: Cleanup old downloads on startup
        self._cleanup_old_downloads()
        
        logger.info(f"AutoUpdater: v{self.current_version}, "
                   f"{self.repo_owner}/{self.repo_name}, "
                   f"last_check={self.last_check_file}, exists={self.last_check_file.exists()}")
    
    def _load_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """Load configuration - Prioritizes config/update_config.yaml"""
        
        if config_path:
            path = Path(config_path)
            if path.exists():
                return self._parse_config_file(path)
            logger.error(f"Config not found: {config_path}")
            return self.DEFAULT_CONFIG.copy()
        
        # Determine base directory
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).parent.parent
        
        # Priority order - dedicated update config FIRST
        search_paths = [
            exe_dir / "config" / "update_config.yaml",
            exe_dir / "update_config.yaml",
            exe_dir / "config" / "regions.yaml",
            exe_dir / "regions.yaml",
        ]
        
        for path in search_paths:
            if path.exists():
                config = self._parse_config_file(path)
                if config:
                    logger.info(f"Config loaded: {path}")
                    return config
        
        logger.warning("No config found, using defaults (disabled)")
        return self.DEFAULT_CONFIG.copy()
    
    def _parse_config_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """Parse YAML - handles BOTH flat and nested formats"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data:
                return None
            
            filename = path.name.lower()
            
            # Case 1: Dedicated update_config.yaml (can be flat OR nested)
            if 'update_config' in filename:
                if 'auto_update' in data:
                    return {**self.DEFAULT_CONFIG, **data['auto_update']}
                if 'enabled' in data:
                    return {**self.DEFAULT_CONFIG, **data}
            
            # Case 2: regions.yaml with auto_update section
            if 'auto_update' in data:
                return {**self.DEFAULT_CONFIG, **data['auto_update']}
            
            # Case 3: Flat config in any file
            if 'enabled' in data and 'repo_owner' in data:
                return {**self.DEFAULT_CONFIG, **data}
                
        except Exception as e:
            logger.warning(f"Failed to parse {path}: {e}")
        
        return None
    
    def _apply_config(self):
        self.repo_owner = self.config['repo_owner']
        self.repo_name = self.config['repo_name']
        self.current_version = self.config['current_version']
        self.executable_name = self.config['executable_name']
        self.check_interval_hours = self.config['check_interval_hours']
        self.silent_mode = self.config.get('silent_mode', True)
        self.github_token = self.config.get('github_token')
        self.api_url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
    
    def _setup_paths(self):
        if getattr(sys, 'frozen', False):
            self.app_dir = Path(sys.executable).parent
        else:
            self.app_dir = Path(__file__).parent.parent
        
        self.temp_dir = Path(tempfile.gettempdir()) / "watchdog_updates"
        self.update_marker = self.app_dir / ".update_pending"
        self.last_check_file = self.temp_dir / ".last_check"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    def _cleanup_old_downloads(self):
        """FIXED Issue #8: Clean up old download files (keep last 2)"""
        try:
            downloads = sorted(
                self.temp_dir.glob("Watchdog_*.exe"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            # Keep last 2, delete rest
            for old_file in downloads[2:]:
                try:
                    old_file.unlink()
                    logger.debug(f"Cleaned up: {old_file.name}")
                except:
                    pass
        except Exception as e:
            logger.debug(f"Cleanup error: {e}")
    
    def get_latest_release(self) -> Optional[Dict[str, Any]]:
        """FIXED Issue #4: Added GitHub token support and rate limit handling"""
        try:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": f"Watchdog-Updater/{self.current_version}"
            }
            
            # FIXED Issue #4: Add token if available (5000 req/hr instead of 60)
            if self.github_token:
                headers['Authorization'] = f'token {self.github_token}'
            
            timeout = self.config.get('github_api_timeout', 10)
            response = requests.get(self.api_url, headers=headers, timeout=timeout)
            
            # FIXED Issue #4: Handle rate limiting
            if response.status_code == 403:
                reset_time = response.headers.get('X-RateLimit-Reset')
                if reset_time:
                    reset_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(reset_time)))
                    logger.error(f"GitHub rate limit exceeded. Resets at: {reset_dt}")
                    logger.error("Add 'github_token' to config for 5000 req/hr limit")
                else:
                    logger.error("GitHub API rate limited")
                return None
            
            response.raise_for_status()
            data = response.json()
            
            return {
                "version": data["tag_name"].lstrip('v'),
                "published_at": data["published_at"],
                "assets": data.get("assets", []),
                "body": data.get("body", "")
            }
        except Exception as e:
            if not self.silent_mode:
                logger.error(f"GitHub API error: {e}")
            return None
    
    def find_matching_asset(self, assets: list) -> Optional[Dict[str, str]]:
        exe_base = self.executable_name.lower().replace('.exe', '')
        
        for asset in assets:
            name = asset["name"]
            
            # Priority 1: Exact match (case-insensitive)
            if name.lower() == self.executable_name.lower():
                return {
                    "name": asset["name"],
                    "url": asset["browser_download_url"],
                    "size": asset["size"]
                }
            
            # Priority 2: Contains base name (case-insensitive)
            if exe_base in name.lower() and name.lower().endswith('.exe'):
                return {
                    "name": asset["name"],
                    "url": asset["browser_download_url"],
                    "size": asset["size"]
                }
        
        return None
    
    def extract_sha256_from_release(self, release_body: str) -> Optional[str]:
        """FIXED Issue #6: Extract SHA256 from release notes"""
        if not release_body:
            return None
        
        # Look for SHA256: abc123... or sha256: abc123...
        match = re.search(r'SHA256:\s*([a-fA-F0-9]{64})', release_body, re.IGNORECASE)
        if match:
            return match.group(1).lower()
        
        return None
    
    def version_is_newer(self, latest: str) -> bool:
        def parse(v: str):
            try:
                v = v.lstrip('v')
                parts = v.split('.')
                return tuple(int(x) for x in parts[:3])
            except:
                return (0, 0, 0)
        try:
            return parse(latest) > parse(self.current_version)
        except:
            return latest != self.current_version
    
    def download_update(self, url: str, target_path: Path) -> bool:
        try:
            logger.info(f"Downloading: {url.split('/')[-1]}")
            timeout = self.config.get('download_timeout', 60)
            
            # FIXED Issue #4: Use token for downloads too
            headers = {}
            if self.github_token:
                headers['Authorization'] = f'token {self.github_token}'
            
            response = requests.get(url, stream=True, timeout=timeout, headers=headers)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(target_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            
            logger.info(f"Downloaded: {downloaded} bytes")
            return True
        except Exception as e:
            logger.error(f"Download failed: {e}")
            target_path.unlink(missing_ok=True)
            return False
    
    def verify_download(self, file_path: Path, expected_size: int = None, expected_sha256: str = None) -> bool:
        """FIXED Issue #6: Added SHA256 verification"""
        if not file_path.exists():
            logger.error("Download file not found")
            return False
        
        actual_size = file_path.stat().st_size
        
        # Size check
        if actual_size < 1024:
            logger.error(f"File too small: {actual_size} bytes")
            return False
        
        if expected_size and actual_size != expected_size:
            logger.error(f"Size mismatch: expected {expected_size}, got {actual_size}")
            return False
        
        # FIXED Issue #6: SHA256 verification
        if expected_sha256:
            logger.info("Verifying SHA256 checksum...")
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    sha256.update(chunk)
            
            actual_hash = sha256.hexdigest()
            if actual_hash != expected_sha256:
                logger.error(f"SHA256 mismatch!")
                logger.error(f"  Expected: {expected_sha256}")
                logger.error(f"  Actual:   {actual_hash}")
                return False
            
            logger.info("SHA256 verified ✓")
        
        return True
    
    def apply_update(self, new_executable: Path, new_version: str) -> bool:
        """Download verified. Replace exe via batch script and restart.

        Multi-user safe: uses a lock file next to the exe so only one
        instance across all user sessions performs the replacement.  The
        batch script kills ALL Watchdog.exe processes (not just ours)
        before moving files, so the exe is never locked.
        """
        if not getattr(sys, 'frozen', False):
            logger.info(f"Dev mode (.py): update v{new_version} available but skipping apply")
            return False

        try:
            current_exe = Path(sys.executable)
            exe_dir = current_exe.parent
            backup_exe = current_exe.with_suffix('.exe.backup')
            lock_file = exe_dir / ".update_lock"

            # ── Multi-instance guard ──────────────────────────────
            # The lock file sits next to the exe (shared by all users).
            # First instance to create it wins; others bail out.
            if lock_file.exists():
                try:
                    lock_age = time.time() - lock_file.stat().st_mtime
                    if lock_age < 300:          # < 5 min  → another instance is handling it
                        logger.info("Another instance is already applying the update, skipping")
                        return False
                    # Stale lock (> 5 min) → previous attempt crashed, take over
                    logger.warning("Stale update lock detected (%.0fs old), taking over", lock_age)
                except OSError:
                    pass

            lock_file.write_text(str(os.getpid()), encoding='utf-8')

            # ── Update config version ─────────────────────────────
            for cfg_path in [exe_dir / "config" / "update_config.yaml", exe_dir / "update_config.yaml"]:
                if cfg_path.exists():
                    try:
                        content = cfg_path.read_text(encoding='utf-8')
                        content = re.sub(
                            r'current_version:\s*"[^"]*"',
                            f'current_version: "{new_version}"',
                            content
                        )
                        cfg_path.write_text(content, encoding='utf-8')
                        logger.info(f"Config version updated to {new_version}")
                    except Exception as e:
                        logger.warning(f"Could not update config version: {e}")
                    break

            # ── Build batch script ────────────────────────────────
            update_script = self.temp_dir / "apply_update.bat"
            exe_name = current_exe.name          # e.g. "Watchdog.exe"

            cur = str(current_exe).replace('%', '%%')
            new = str(new_executable).replace('%', '%%')
            bak = str(backup_exe).replace('%', '%%')
            lck = str(lock_file).replace('%', '%%')

            bat = f"""@echo off
title Watchdog Auto-Updater
echo ========================================
echo   Watchdog Auto-Updater
echo ========================================
echo.

echo [1/5] Stopping ALL {exe_name} instances...
taskkill /F /IM "{exe_name}" >NUL 2>&1

echo   Waiting for processes to release file lock...
:wait
tasklist /FI "IMAGENAME eq {exe_name}" 2>NUL | find /I "{exe_name}" >NUL
if %ERRORLEVEL%==0 (
    timeout /t 1 /nobreak >NUL
    goto wait
)
echo   All instances stopped.

echo [2/5] Backing up current version...
if exist "{bak}" del /Q "{bak}"
move /Y "{cur}" "{bak}"
if %ERRORLEVEL% NEQ 0 (
    echo   Exe still locked, retrying in 3s...
    timeout /t 3 /nobreak >NUL
    move /Y "{cur}" "{bak}"
    if %ERRORLEVEL% NEQ 0 (
        echo FATAL: Backup failed. Aborting.
        del /Q "{lck}" 2>NUL
        pause
        exit /b 1
    )
)

echo [3/5] Installing new version...
move /Y "{new}" "{cur}"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Install failed! Rolling back...
    move /Y "{bak}" "{cur}"
    del /Q "{lck}" 2>NUL
    pause
    exit /b 1
)

echo [4/5] Starting updated Watchdog...
start "" "{cur}"

echo [5/5] Cleanup...
del /Q "{lck}" 2>NUL

echo.
echo ========================================
echo   Update complete!
echo ========================================
timeout /t 3 /nobreak >NUL
del "%~f0"
exit
"""
            update_script.write_text(bat, encoding='utf-8')
            logger.info("Launching updater batch script...")

            proc = subprocess.Popen(
                ["cmd.exe", "/C", str(update_script)],
                creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            )

            if proc.poll() is not None:
                logger.error("Updater process failed to start")
                lock_file.unlink(missing_ok=True)
                return False

            logger.info(f"Updater running (PID {proc.pid}), exiting now...")
            time.sleep(1)
            sys.exit(0)

        except Exception as e:
            logger.error(f"Failed to apply update: {e}")
            return False
    
    def check_and_update(self, force: bool = False) -> Dict[str, Any]:
        """Main update check with enhanced error reporting"""
        result = {
            'checked': False,
            'update_available': False,
            'current_version': self.current_version,
            'latest_version': None,
            'downloaded': False,
            'ready_to_apply': False,
            'error': None,
        }
        
        if not self.enabled:
            result['error'] = 'Disabled'
            return result
        
        # Interval check
        if not force and self.last_check_file.exists():
            last_check = self.last_check_file.stat().st_mtime
            hours_since = (time.time() - last_check) / 3600
            if hours_since < self.check_interval_hours:
                result['error'] = f'Interval: {hours_since:.1f}h < {self.check_interval_hours}h'
                return result
        
        result['checked'] = True
        self.last_check_file.touch()
        
        release = self.get_latest_release()
        if not release:
            result['error'] = 'Failed to fetch release (check network/GitHub API)'
            return result
        
        latest = release['version']
        result['latest_version'] = latest
        
        if not self.version_is_newer(latest):
            logger.info(f"Up to date: v{self.current_version}")
            return result
        
        logger.info(f"Update available: v{self.current_version} -> v{latest}")
        result['update_available'] = True
        
        asset = self.find_matching_asset(release['assets'])
        if not asset:
            result['error'] = f'No matching asset for {self.executable_name}'
            return result
        
        # FIXED Issue #6: Extract SHA256 from release notes
        expected_sha256 = self.extract_sha256_from_release(release.get('body', ''))
        if expected_sha256:
            logger.info("SHA256 found in release notes - will verify")
        else:
            logger.warning("No SHA256 in release notes - skipping checksum verification")
        
        download_path = self.temp_dir / f"Watchdog_{latest}_{asset['name']}"
        if download_path.exists():
            download_path.unlink()
        
        if not self.download_update(asset['url'], download_path):
            result['error'] = 'Download failed'
            return result
        
        if not self.verify_download(download_path, asset['size'], expected_sha256):
            result['error'] = 'Verification failed (size/checksum mismatch)'
            download_path.unlink(missing_ok=True)
            return result
        
        result['downloaded'] = True

        if self.apply_update(download_path, latest):
            result['ready_to_apply'] = True
            # Note: apply_update() calls sys.exit(0) if successful
        else:
            if not getattr(sys, 'frozen', False):
                result['error'] = 'Dev mode: skipped apply (no exe to replace)'
            else:
                result['error'] = 'Failed to apply update'
        
        return result
    
    def get_status(self) -> Dict[str, Any]:
        status = {
            'enabled': self.enabled,
            'current_version': self.current_version,
            'update_pending': self.update_marker.exists(),
            'update_ready': False,
            'next_check': None,
            'has_github_token': bool(self.github_token) if self.enabled else False,
        }
        
        if status['update_pending']:
            try:
                pending = self.update_marker.read_text().strip()
                status['update_ready'] = True
                status['pending_file'] = Path(pending).name
            except:
                pass
        
        if self.enabled and self.last_check_file.exists():
            next_time = self.last_check_file.stat().st_mtime + (self.check_interval_hours * 3600)
            status['next_check'] = time.strftime("%Y-%m-%d %H:%M", time.localtime(next_time))
        
        return status


# Convenience functions
def check_updates(config_path: Optional[str] = None) -> Optional[Dict]:
    """Check for updates - safe to call even if dependencies missing"""
    try:
        updater = GitHubAutoUpdater(config_path)
        return updater.check_and_update()
    except Exception as e:
        logger.debug(f"Update check failed: {e}")
        return {'error': str(e)}

def get_status(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Get update status"""
    try:
        return GitHubAutoUpdater(config_path).get_status()
    except Exception as e:
        return {'enabled': False, 'error': str(e)}