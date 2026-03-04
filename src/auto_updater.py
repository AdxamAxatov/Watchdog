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
    DEFAULT_CONFIG = {
        'enabled': False,
        'repo_owner': 's1gmamale1',
        'repo_name': 'watchdog',
        'current_version': '1.0.0',
        'executable_name': 'watchdog.exe',
        'check_interval_hours': 1,
        'auto_restart': False,
        'restart_window': {'start_hour': 3, 'end_hour': 5},
        'silent_mode': True,
        'backup_old_version': True,
        'github_api_timeout': 10,
        'download_timeout': 60
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
        
        logger.info(f"AutoUpdater: v{self.current_version}, "
                   f"{self.repo_owner}/{self.repo_name}")
    
    def _load_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """Load configuration - FIXED: Prioritizes config/update_config.yaml"""
        
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
        
        # FIXED: Priority order - dedicated update config FIRST
        search_paths = [
            # Priority 1: config/update_config.yaml (YOUR SETUP)
            exe_dir / "config" / "update_config.yaml",
            
            # Priority 2: update_config.yaml next to exe
            exe_dir / "update_config.yaml",
            
            # Priority 3: regions.yaml with auto_update section
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
                # If it has 'auto_update' section, use that
                if 'auto_update' in data:
                    return {**self.DEFAULT_CONFIG, **data['auto_update']}
                # Otherwise assume flat structure
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
    
    def _is_in_restart_window(self) -> bool:
        if not self.restart_window:
            return False
        current_hour = time.localtime().tm_hour
        start = self.restart_window.get('start_hour', 3)
        end = self.restart_window.get('end_hour', 5)
        return start <= current_hour < end
    
    def get_latest_release(self) -> Optional[Dict[str, Any]]:
        try:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": f"Watchdog-Updater/{self.current_version}"
            }
            timeout = self.config.get('github_api_timeout', 10)
            response = requests.get(self.api_url, headers=headers, timeout=timeout)
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
            name = asset["name"]  # Don't lowercase here for case-sensitive compare
            
            # Priority 1: Exact match (case-insensitive)
            if name.lower() == self.executable_name.lower():
                return {"name": asset["name"], "url": asset["browser_download_url"], "size": asset["size"]}
            
            # Priority 2: Contains base name (case-insensitive)
            if exe_base in name.lower() and name.lower().endswith('.exe'):
                return {"name": asset["name"], "url": asset["browser_download_url"], "size": asset["size"]}
        
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
            response = requests.get(url, stream=True, timeout=timeout)
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
    
    def verify_download(self, file_path: Path, expected_size: int = None) -> bool:
        if not file_path.exists():
            return False
        actual_size = file_path.stat().st_size
        if expected_size and actual_size != expected_size:
            logger.error(f"Size mismatch: expected {expected_size}, got {actual_size}")
            return False
        if actual_size < 1024:
            logger.error(f"File too small: {actual_size} bytes")
            return False
        return True
    
    def apply_update(self, new_executable: Path) -> bool:
        try:
            current_exe = Path(sys.executable) if getattr(sys, 'frozen', False) else self.app_dir / self.executable_name
            if not current_exe.exists():
                logger.error(f"Current exe not found: {current_exe}")
                return False
            
            backup_exe = current_exe.with_suffix('.exe.backup')
            update_script = self.temp_dir / "apply_update.bat"
            
            script = f"""@echo off
echo [Updater] Waiting...
timeout /t 2 /nobreak > nul
cd /d "{self.app_dir}"
if exist "{backup_exe}" del "{backup_exe}"
ren "{current_exe}" "{backup_exe.name}"
move "{new_executable}" "{current_exe}"
del "{update_script}"
start "" "{current_exe}"
del "%~f0"
"""
            with open(update_script, 'w') as f:
                f.write(script)
            
            self.update_marker.write_text(str(new_executable))
            logger.info("Update staged - restart to apply")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stage update: {e}")
            return False
    
    def check_and_update(self, force: bool = False) -> Dict[str, Any]:
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
            result['error'] = 'Failed to fetch release'
            return result
        
        latest = release['version']
        result['latest_version'] = latest
        
        if not self.version_is_newer(latest):
            logger.info(f"Up to date: v{self.current_version}")
            return result
        
        logger.info(f"Update: v{self.current_version} -> v{latest}")
        result['update_available'] = True
        
        asset = self.find_matching_asset(release['assets'])
        if not asset:
            result['error'] = f'No asset for {self.executable_name}'
            return result
        
        download_path = self.temp_dir / asset['name']
        if download_path.exists():
            download_path.unlink()
        
        if not self.download_update(asset['url'], download_path):
            result['error'] = 'Download failed'
            return result
        
        if not self.verify_download(download_path, asset['size']):
            result['error'] = 'Verification failed'
            download_path.unlink(missing_ok=True)
            return result
        
        result['downloaded'] = True
        
        if self.apply_update(download_path):
            result['ready_to_apply'] = True
            if self.auto_restart and self._is_in_restart_window():
                logger.info("Auto-restarting...")
                self.restart_and_update()
                result['auto_restarted'] = True
        
        return result
    
    def restart_and_update(self):
        script = self.temp_dir / "apply_update.bat"
        if script.exists():
            logger.info("Restarting...")
            subprocess.Popen([str(script)], shell=True)
            sys.exit(0)
    
    def get_status(self) -> Dict[str, Any]:
        status = {
            'enabled': self.enabled,
            'current_version': self.current_version,
            'update_pending': self.update_marker.exists(),
            'update_ready': False,
            'next_check': None,
            'auto_restart': self.auto_restart if self.enabled else False,
            'in_restart_window': self._is_in_restart_window() if self.enabled else False
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
        return None

def get_status(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Get update status"""
    try:
        return GitHubAutoUpdater(config_path).get_status()
    except Exception as e:
        return {'enabled': False, 'error': str(e)}