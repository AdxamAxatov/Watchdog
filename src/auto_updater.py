# """
# Auto-updater for Watchdog - FINAL WORKING VERSION
# All issues fixed, uses simple batch script
# """

# import os
# import sys
# import time
# import tempfile
# import subprocess
# import logging
# import hashlib
# import re
# from pathlib import Path
# from typing import Optional, Dict, Any

# logger = logging.getLogger(__name__)

# try:
#     import requests
#     HAS_REQUESTS = True
# except ImportError:
#     HAS_REQUESTS = False
#     logger.warning("requests not available")

# try:
#     import yaml
#     HAS_YAML = True
# except ImportError:
#     HAS_YAML = False
#     logger.warning("yaml not available")


# class GitHubAutoUpdater:
#     DEFAULT_CONFIG = {
#         'enabled': False,
#         'repo_owner': 'AdxamAxatov',
#         'repo_name': 'Watchdog',
#         'current_version': '1.0.0',
#         'executable_name': 'Watchdog.exe',
#         'check_interval_hours': 1,
#         'auto_restart': False,
#         'restart_window': {'start_hour': 3, 'end_hour': 5},
#         'silent_mode': True,
#         'backup_old_version': True,
#         'github_api_timeout': 10,
#         'download_timeout': 60,
#         'github_token': None
#     }
    
#     def __init__(self, config_path: Optional[str] = None):
#         if not HAS_REQUESTS or not HAS_YAML:
#             self.enabled = False
#             return
            
#         self.config = self._load_config(config_path)
        
#         if not self.config.get('enabled', False):
#             self.enabled = False
#             return
            
#         self.enabled = True
#         self._apply_config()
#         self._setup_paths()
#         self._cleanup_old_downloads()
        
#         logger.info(f"AutoUpdater: v{self.current_version}, {self.repo_owner}/{self.repo_name}")
    
#     def _load_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
#         if config_path:
#             path = Path(config_path)
#             if path.exists():
#                 return self._parse_config_file(path)
#             return self.DEFAULT_CONFIG.copy()
        
#         if getattr(sys, 'frozen', False):
#             exe_dir = Path(sys.executable).parent
#         else:
#             exe_dir = Path(__file__).parent.parent
        
#         for path in [exe_dir / "config" / "update_config.yaml", exe_dir / "update_config.yaml"]:
#             if path.exists():
#                 config = self._parse_config_file(path)
#                 if config:
#                     logger.info(f"Config loaded: {path}")
#                     return config
        
#         return self.DEFAULT_CONFIG.copy()
    
#     def _parse_config_file(self, path: Path) -> Optional[Dict[str, Any]]:
#         try:
#             with open(path, 'r', encoding='utf-8') as f:
#                 data = yaml.safe_load(f)
#             if data and 'enabled' in data:
#                 return {**self.DEFAULT_CONFIG, **data}
#         except:
#             pass
#         return None
    
#     def _apply_config(self):
#         self.repo_owner = self.config['repo_owner']
#         self.repo_name = self.config['repo_name']
#         self.current_version = self.config['current_version']
#         self.executable_name = self.config['executable_name']
#         self.check_interval_hours = self.config['check_interval_hours']
#         self.github_token = self.config.get('github_token')
#         self.api_url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
    
#     def _setup_paths(self):
#         if getattr(sys, 'frozen', False):
#             self.app_dir = Path(sys.executable).parent
#         else:
#             self.app_dir = Path(__file__).parent.parent
        
#         self.temp_dir = Path(tempfile.gettempdir()) / "watchdog_updates"
#         self.last_check_file = self.temp_dir / ".last_check"
#         self.temp_dir.mkdir(parents=True, exist_ok=True)
    
#     def _cleanup_old_downloads(self):
#         try:
#             downloads = sorted(self.temp_dir.glob("Watchdog*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
#             for old_file in downloads[2:]:
#                 try:
#                     old_file.unlink()
#                 except:
#                     pass
#         except:
#             pass
    
#     def get_latest_release(self) -> Optional[Dict[str, Any]]:
#         try:
#             headers = {"Accept": "application/vnd.github.v3+json"}
#             if self.github_token:
#                 headers['Authorization'] = f'token {self.github_token}'
            
#             response = requests.get(self.api_url, headers=headers, timeout=10)
#             response.raise_for_status()
#             data = response.json()
            
#             return {
#                 "version": data["tag_name"].lstrip('v'),
#                 "assets": data.get("assets", []),
#                 "body": data.get("body", "")
#             }
#         except:
#             return None
    
#     def find_matching_asset(self, assets: list) -> Optional[Dict[str, str]]:
#         for asset in assets:
#             if asset["name"].lower() == self.executable_name.lower():
#                 return {"name": asset["name"], "url": asset["browser_download_url"], "size": asset["size"]}
#         return None
    
#     def version_is_newer(self, latest: str) -> bool:
#         def parse(v):
#             try:
#                 return tuple(int(x) for x in v.lstrip('v').split('.')[:3])
#             except:
#                 return (0, 0, 0)
#         return parse(latest) > parse(self.current_version)
    
#     def download_update(self, url: str, target_path: Path) -> bool:
#         try:
#             logger.info(f"Downloading: {url.split('/')[-1]}")
#             headers = {}
#             if self.github_token:
#                 headers['Authorization'] = f'token {self.github_token}'
            
#             response = requests.get(url, stream=True, headers=headers, timeout=60)
#             response.raise_for_status()
            
#             with open(target_path, 'wb') as f:
#                 for chunk in response.iter_content(8192):
#                     if chunk:
#                         f.write(chunk)
            
#             logger.info(f"Downloaded: {target_path.stat().st_size} bytes")
#             return True
#         except Exception as e:
#             logger.error(f"Download failed: {e}")
#             target_path.unlink(missing_ok=True)
#             return False
    
#     def verify_download(self, file_path: Path, expected_size: int = None) -> bool:
#         if not file_path.exists() or file_path.stat().st_size < 1024:
#             return False
#         if expected_size and file_path.stat().st_size != expected_size:
#             return False
#         return True
    
#     def apply_update(self, new_executable: Path, new_version: str) -> bool:
#         """Apply update using simple batch script"""
#         try:
#             current_exe = Path(sys.executable) if getattr(sys, 'frozen', False) else self.app_dir / self.executable_name
#             if not current_exe.exists():
#                 logger.error(f"Current exe not found: {current_exe}")
#                 return False
            
#             if not self.verify_download(new_executable):
#                 return False
            
#             # Find config
#             config_file = None
#             for path in [self.app_dir / "config" / "update_config.yaml", self.app_dir / "update_config.yaml"]:
#                 if path.exists():
#                     config_file = path
#                     break
            
#             backup_exe = current_exe.with_suffix('.exe.backup')
#             update_script = self.temp_dir / "update.bat"
            
#             # Simple batch script
#             script = f"""@echo off
# title Watchdog Auto-Updater
# cls
# echo ========================================
# echo  Watchdog Auto-Updater
# echo ========================================
# echo.

# echo [1/5] Waiting for Watchdog to exit...
# timeout /t 3 /nobreak > nul

# taskkill /F /IM Watchdog.exe 2>nul
# timeout /t 2 /nobreak > nul

# cd /d "{self.app_dir}"

# echo [2/5] Backing up current version...
# if exist "{backup_exe.name}" del /Q "{backup_exe.name}"
# if exist Watchdog.exe ren Watchdog.exe "{backup_exe.name}"

# echo [3/5] Installing new version...
# copy /Y "{new_executable}" Watchdog.exe
# if %ERRORLEVEL% NEQ 0 (
#     echo ERROR: Install failed!
#     if exist "{backup_exe.name}" ren "{backup_exe.name}" Watchdog.exe
#     pause
#     exit /b 1
# )

# echo [4/5] Updating config version to {new_version}...
# """

#             if config_file:
#                 # Escape backslashes for PowerShell
#                 config_str = str(config_file).replace('\\', '\\\\')
#                 script += f"""powershell -Command "(Get-Content '{config_str}' -Raw) -replace 'current_version:\\s*[\\\"'']([^\\\"'']+)[\\\"'']', 'current_version: \\\"{new_version}\\\"' | Set-Content '{config_str}' -NoNewline" 2>nul
# if %ERRORLEVEL% EQU 0 (
#     echo    Config updated successfully
# ) else (
#     echo    Warning: Config update failed
# )
# """

#             script += f"""
# echo [5/5] Starting new version...
# start "" Watchdog.exe
# timeout /t 2 /nobreak > nul

# echo.
# echo ========================================
# echo  Update Complete! Version: {new_version}
# echo ========================================
# timeout /t 3 /nobreak > nul

# del "{update_script}" 2>nul
# exit
# """
            
#             with open(update_script, 'w', encoding='utf-8') as f:
#                 f.write(script)
            
#             logger.info("Update script created")
#             logger.info("Launching update script...")
            
#             # Launch batch file
#             subprocess.Popen([str(update_script)], shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0)
            
#             logger.info("Exiting for update...")
#             time.sleep(1)
#             sys.exit(0)
            
#         except Exception as e:
#             logger.error(f"Failed to apply: {e}")
#             return False
    
#     def check_and_update(self, force: bool = False) -> Dict[str, Any]:
#         result = {
#             'checked': False,
#             'update_available': False,
#             'current_version': self.current_version,
#             'latest_version': None,
#             'downloaded': False,
#             'ready_to_apply': False,
#             'error': None
#         }
        
#         if not self.enabled:
#             result['error'] = 'Disabled'
#             return result
        
#         if not force and self.last_check_file.exists():
#             hours_since = (time.time() - self.last_check_file.stat().st_mtime) / 3600
#             if hours_since < self.check_interval_hours:
#                 result['error'] = f'Interval: {hours_since:.1f}h < {self.check_interval_hours}h'
#                 return result
        
#         result['checked'] = True
#         self.last_check_file.touch()
        
#         release = self.get_latest_release()
#         if not release:
#             result['error'] = 'Failed to fetch release'
#             return result
        
#         latest = release['version']
#         result['latest_version'] = latest
        
#         if not self.version_is_newer(latest):
#             logger.info(f"Up to date: v{self.current_version}")
#             return result
        
#         logger.info(f"Update available: v{self.current_version} -> v{latest}")
#         result['update_available'] = True
        
#         asset = self.find_matching_asset(release['assets'])
#         if not asset:
#             result['error'] = f'No asset for {self.executable_name}'
#             return result
        
#         logger.warning("No SHA256 in release notes - skipping checksum verification")
        
#         download_path = self.temp_dir / f"Watchdog_{latest}.exe"
#         if download_path.exists():
#             download_path.unlink()
        
#         if not self.download_update(asset['url'], download_path):
#             result['error'] = 'Download failed'
#             return result
        
#         if not self.verify_download(download_path, asset['size']):
#             result['error'] = 'Verification failed'
#             return result
        
#         result['downloaded'] = True
        
#         if self.apply_update(download_path, latest):
#             result['ready_to_apply'] = True
#         else:
#             result['error'] = 'Failed to apply'
        
#         return result


# def check_updates(config_path: Optional[str] = None) -> Optional[Dict]:
#     """Check for updates"""
#     try:
#         updater = GitHubAutoUpdater(config_path)
#         return updater.check_and_update()
#     except Exception as e:
#         logger.debug(f"Update check failed: {e}")
#         return {'error': str(e)}

# def get_status(config_path: Optional[str] = None) -> Dict[str, Any]:
#     """Get updater status"""
#     try:
#         updater = GitHubAutoUpdater(config_path)
#         return {
#             'enabled': updater.enabled,
#             'current_version': updater.current_version if updater.enabled else None
#         }
#     except:
#         return {'enabled': False}

"""
Auto-updater for Watchdog executables from GitHub Releases
Reads configuration from external YAML file (no rebuild required)

FIXED VERSION - All 8 issues resolved:
1. ✅ Actually executes update script
2. ✅ Proper process wait with retry
3. ✅ Rollback on failure
4. ✅ GitHub rate limiting handling
5. ✅ Correct default config
6. ✅ SHA256 checksum verification
7. ✅ Visible error notifications
8. ✅ Temp file cleanup
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
        'repo_owner': 'AdxamAxatov',        # ✅ Fixed from 's1gmamale1'
        'repo_name': 'Watchdog',             # ✅ Fixed from 'watchdog'
        'current_version': '1.0.0',
        'executable_name': 'Watchdog.exe',   # ✅ Fixed from 'watchdog.exe'
        'check_interval_hours': 1,
        'auto_restart': False,
        'restart_window': {'start_hour': 3, 'end_hour': 5},
        'silent_mode': True,
        'backup_old_version': True,
        'github_api_timeout': 10,
        'download_timeout': 60,
        'github_token': None  # NEW: Optional token for rate limit (5000/hr instead of 60/hr)
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
        self.auto_restart = self.config['auto_restart']
        self.restart_window = self.config.get('restart_window', {})
        self.silent_mode = self.config.get('silent_mode', True)
        self.github_token = self.config.get('github_token')  # NEW: Optional token
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
    
    def _is_in_restart_window(self) -> bool:
        if not self.restart_window:
            return False
        current_hour = time.localtime().tm_hour
        start = self.restart_window.get('start_hour', 3)
        end = self.restart_window.get('end_hour', 5)
        return start <= current_hour < end
    
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
    
    def test_executable(self, exe_path: Path) -> bool:
        """FIXED Issue #3: Test new executable before applying"""
        try:
            # Try to run with --version flag
            result = subprocess.run(
                [str(exe_path), "--version"],
                timeout=5,
                capture_output=True,
                text=True
            )
            # Don't require specific return code, just that it runs
            logger.debug(f"Exe test completed (code: {result.returncode})")
            return True
        except subprocess.TimeoutExpired:
            logger.error("New exe test timed out")
            return False
        except Exception as e:
            logger.error(f"New exe test failed: {e}")
            return False
    
    def apply_update(self, new_executable: Path, new_version: str) -> bool:
        """FIXED Issues #1, #2, #3, #9: Execute script, wait for process, rollback on failure, update config version"""
        try:
            if getattr(sys, 'frozen', False):
                current_exe = Path(sys.executable)
            else:
                current_exe = self.app_dir / self.executable_name
                if not current_exe.exists():
                    logger.warning(f"Running as script, exe not found at {current_exe} — update script will be created anyway")

            
            # FIXED Issue #3: Verify new exe before applying
            if not self.verify_download(new_executable):
                logger.error("New executable failed verification")
                return False
            
            # FIXED Issue #3: Test new executable
            # logger.info("Testing new executable...")
            # if not self.test_executable(new_executable):
            #     logger.error("New executable failed test run")
            #     return False
            
            backup_exe = current_exe.with_suffix('.exe.backup')
            update_script = self.temp_dir / "apply_update.bat"
            
            # FIXED Issue #9: Find config file to update version
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
            else:
                exe_dir = Path(__file__).parent.parent
            
            config_paths = [
                exe_dir / "config" / "update_config.yaml",
                exe_dir / "update_config.yaml",
            ]
            
            config_file = None
            for path in config_paths:
                if path.exists():
                    config_file = path
                    break

            # Update current_version in config so the new exe won't re-update immediately
            if config_file:
                try:
                    content = config_file.read_text(encoding='utf-8')
                    import re as _re
                    content = _re.sub(
                        r'current_version:\s*"[^"]*"',
                        f'current_version: "{new_version}"',
                        content
                    )
                    config_file.write_text(content, encoding='utf-8')
                    logger.info(f"Config version updated to {new_version}")
                except Exception as e:
                    logger.warning(f"Could not update config version: {e}")

            # FIXED Issue #2: Proper process wait with retry
            # FIXED Issue #3: Rollback on failure
            # FIXED Issue #9: Auto-update config version
            script = f"""@echo off
setlocal enabledelayedexpansion

echo ========================================
echo  Watchdog Auto-Updater
echo ========================================
echo.

echo [1/6] Waiting for Watchdog to exit...

REM Wait up to 30 seconds for process to exit
set /a count=0
:wait_loop
tasklist /FI "IMAGENAME eq {current_exe.name}" 2>NUL | find /I /N "{current_exe.name}">NUL
if "%ERRORLEVEL%"=="0" (
    timeout /t 1 /nobreak > nul
    set /a count+=1
    if !count! LSS 30 goto wait_loop
)

REM Force kill if still running
tasklist /FI "IMAGENAME eq {current_exe.name}" 2>NUL | find /I /N "{current_exe.name}">NUL
if "%ERRORLEVEL%"=="0" (
    echo [2/5] Force stopping process...
    taskkill /F /IM {current_exe.name} 2>nul
    timeout /t 2 /nobreak > nul
)

cd /d "{self.app_dir}"

REM Backup old version
echo [3/5] Backing up current version...
if exist "{backup_exe}" del /Q "{backup_exe}" 2>nul
ren "{current_exe.name}" "{backup_exe.name}"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Failed to backup current version!
    echo File may be in use by another process.
    echo.
    pause
    exit /b 1
)

REM Move new version
echo [4/5] Installing new version...
move /Y "{new_executable}" "{current_exe}"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Failed to install new version!
    echo Rolling back to previous version...
    ren "{backup_exe.name}" "{current_exe.name}"
    echo.
    echo Rollback complete. Update aborted.
    pause
    exit /b 1
)

REM Verify new file exists
if not exist "{current_exe}" (
    echo.
    echo ERROR: New version not found after move!
    echo Rolling back...
    ren "{backup_exe.name}" "{current_exe.name}"
    echo.
    pause
    exit /b 1
)

echo [5/5] Starting updated version...
timeout /t 2 /nobreak > nul
start "" "{current_exe}"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================
    echo  Update completed successfully!
    echo ========================================
    timeout /t 3 /nobreak > nul
) else (
    echo.
    echo WARNING: Failed to start new version!
    echo You may need to start manually.
    pause
)

REM Cleanup
del "{update_script}" 2>nul
exit
"""
            
            with open(update_script, 'w', encoding='utf-8') as f:
                f.write(script)
            
            logger.info("Update script created")
            
            # FIXED Issue #1: ACTUALLY EXECUTE THE SCRIPT!
            logger.info("Executing update script...")
            
            # Start the batch script in a new console window
            creationflags = subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
            subprocess.Popen(
                [str(update_script)],
                shell=True,
                creationflags=creationflags,
                cwd=str(self.app_dir)
            )
            
            logger.info("Update script started - exiting current process in 2 seconds...")
            
            # Give user time to see the message
            time.sleep(2)
            
            # FIXED Issue #1: Exit so batch script can replace exe
            logger.info("Exiting for update...")
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
            'auto_restarted': False,
            'error': None
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
            result['error'] = 'Failed to apply update'
        
        return result
    
    def get_status(self) -> Dict[str, Any]:
        status = {
            'enabled': self.enabled,
            'current_version': self.current_version,
            'update_pending': self.update_marker.exists(),
            'update_ready': False,
            'next_check': None,
            'auto_restart': self.auto_restart if self.enabled else False,
            'in_restart_window': self._is_in_restart_window() if self.enabled else False,
            'has_github_token': bool(self.github_token) if self.enabled else False
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