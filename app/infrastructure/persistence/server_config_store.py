"""
Centralized Server Configuration Store (`./data/server_config.json`).

Centralizes all scraper credentials (ScreenScraper, TMDB, TVDB) and MQTT broker settings
on the server so DMD engines (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`) never need
to store or transmit API keys.

3-Tier Credential Resolution Priority:
1. WebUI User Overrides (`./data/server_config.json`)
2. Local `.env` / `docker-compose.yml` / OS Environment Variables (plaintext, zero friction for local dev & forks)
3. CI/CD Production Native Vault (`app.security._prod_vault` compiled `.so`/`.pyd` in release builds)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.portable_env import get_data_dir
from app.security.vault import decrypt_prod_vault_secret, has_prod_vault_secret

logger = logging.getLogger("dmd_showcase.persistence.config")

MASKED_SECRET_PLACEHOLDERS = ("••••••••", "********")

USER_PERSISTED_KEYS = (
    "tmdb_api_key",
    "tvdb_api_key",
    "screenscraper_user",
    "screenscraper_pass",
    "mqtt_mode",
    "mqtt_port",
    "mqtt_external_url",
    "default_lang",
    "showcase_api_key",
    "max_concurrent_jobs",
    "use_process_pool",
    "ffmpeg_threads_per_job",
    "worker_nice_increment",
    "max_queue_size",
    "video_max_duration_limit_s",
    "server_master_clip_max_s",
    "video_max_fps_limit",
    "panorama_duration_s",
    "ytdlp_max_filesize_mb",
    "default_video_mode",
    "default_color_boost",
    "session_ttl_seconds",
    "batch_ttl_seconds",
    "cache_dir",
    "log_level",
    "mdns_enabled",
    "mdns_hostname",
    "trust_proxy_headers",
)

RUNTIME_SETTINGS_KEYS = (
    "default_lang",
    "max_concurrent_jobs",
    "use_process_pool",
    "ffmpeg_threads_per_job",
    "worker_nice_increment",
    "max_queue_size",
    "video_max_duration_limit_s",
    "server_master_clip_max_s",
    "video_max_fps_limit",
    "panorama_duration_s",
    "ytdlp_max_filesize_mb",
    "default_video_mode",
    "default_color_boost",
    "session_ttl_seconds",
    "batch_ttl_seconds",
    "cache_dir",
    "log_level",
    "mdns_enabled",
    "mdns_hostname",
    "trust_proxy_headers",
)

_ENV_VAR_NAMES = {
    "tmdb_api_key": "TMDB_API_KEY",
    "tvdb_api_key": "TVDB_API_KEY",
    "screenscraper_user": "SCREENSCRAPER_USER",
    "screenscraper_pass": "SCREENSCRAPER_PASS",
    "screenscraper_dev_id": "SCREENSCRAPER_DEV_ID",
    "screenscraper_dev_pass": "SCREENSCRAPER_DEV_PASS",
    "showcase_api_key": "SHOWCASE_API_KEY",
}


class ServerConfigStore:
    """
    Thread-safe store for `./data/server_config.json` with `.env`/`docker-compose`
    and Production Native Vault fallback.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._custom_path = file_path
        self._user_overrides: Dict[str, Any] = {}
        self._loaded_from: Optional[Path] = None

    @property
    def file_path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "server_config.json"

    @staticmethod
    def _resolve_env_or_prod_vault(key: str) -> str:
        """
        Tier 2 (.env / docker-compose environment) -> Tier 3 (Compiled _prod_vault binary).
        """
        env_name = _ENV_VAR_NAMES.get(key, key.upper())
        env_val = str(os.environ.get(env_name) or getattr(settings, key, "") or "").strip()
        if env_val:
            return env_val
        return decrypt_prod_vault_secret(key)

    def _ensure_loaded(self) -> None:
        path = self.file_path
        if self._loaded_from == path:
            return
        loaded: Dict[str, Any] = {}
        if path.exists():
            try:
                disk_data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(disk_data, dict):
                    for k in USER_PERSISTED_KEYS:
                        if k in disk_data and disk_data[k] is not None:
                            loaded[k] = disk_data[k]
            except Exception as exc:
                logger.warning("[CONFIG] Could not read %s: %s", path, exc)
        self._user_overrides = loaded
        self._loaded_from = path

    def apply_to_settings(self) -> None:
        """
        Mirror persisted runtime tuning overrides from `server_config.json` onto `app.config.settings`
        so worker pools, video limits, cache paths, TTLs, mDNS, and logging honor WebUI changes live.
        """
        with self._lock:
            self._ensure_loaded()
            for key in RUNTIME_SETTINGS_KEYS:
                if key not in self._user_overrides or self._user_overrides[key] is None:
                    continue
                val = self._user_overrides[key]
                if key == "cache_dir":
                    raw_dir = str(val).strip()
                    if raw_dir:
                        new_dir = Path(raw_dir).expanduser()
                        new_dir.mkdir(parents=True, exist_ok=True)
                        settings.cache_dir = new_dir
                elif key == "mdns_hostname":
                    raw_host = str(val).strip().removesuffix(".local").strip(".")
                    if raw_host:
                        settings.mdns_hostname = raw_host
                elif key == "log_level":
                    lvl_name = str(val).strip().upper()
                    if lvl_name in ("DEBUG", "INFO", "WARNING", "ERROR"):
                        settings.log_level = lvl_name
                elif isinstance(getattr(settings, key, None), bool):
                    setattr(settings, key, bool(val))
                elif isinstance(getattr(settings, key, None), int):
                    setattr(settings, key, int(val))
                elif isinstance(getattr(settings, key, None), float):
                    setattr(settings, key, float(val))
                else:
                    setattr(settings, key, str(val))

    def get_all(self) -> Dict[str, Any]:
        """
        Return effective runtime configuration for internal server components & scrapers.
        Priority: 1. `server_config.json` -> 2. `.env` / `docker-compose` -> 3. `_prod_vault`.
        """
        with self._lock:
            self._ensure_loaded()
            user_tmdb = str(self._user_overrides.get("tmdb_api_key") or "").strip()
            user_tvdb = str(self._user_overrides.get("tvdb_api_key") or "").strip()
            user_ss_user = str(self._user_overrides.get("screenscraper_user") or "").strip()
            user_ss_pass = str(self._user_overrides.get("screenscraper_pass") or "").strip()
            user_showcase_key = str(
                self._user_overrides.get("showcase_api_key")
                if "showcase_api_key" in self._user_overrides
                else self._resolve_env_or_prod_vault("showcase_api_key")
            ).strip()

            def _eff(k: str, fallback: Any) -> Any:
                return self._user_overrides[k] if k in self._user_overrides and self._user_overrides[k] is not None else fallback

            return {
                "tmdb_api_key": user_tmdb or self._resolve_env_or_prod_vault("tmdb_api_key"),
                "tvdb_api_key": user_tvdb or self._resolve_env_or_prod_vault("tvdb_api_key"),
                "screenscraper_user": user_ss_user or self._resolve_env_or_prod_vault("screenscraper_user"),
                "screenscraper_pass": user_ss_pass or self._resolve_env_or_prod_vault("screenscraper_pass"),
                "screenscraper_dev_id": self._resolve_env_or_prod_vault("screenscraper_dev_id"),
                "screenscraper_dev_pass": self._resolve_env_or_prod_vault("screenscraper_dev_pass"),
                "mqtt_mode": str(
                    self._user_overrides.get("mqtt_mode") or os.environ.get("MQTT_MODE", "embedded")
                ),
                "mqtt_port": int(
                    self._user_overrides.get("mqtt_port") or os.environ.get("MQTT_PORT", "1883")
                ),
                "mqtt_external_url": str(
                    self._user_overrides.get("mqtt_external_url")
                    if "mqtt_external_url" in self._user_overrides
                    else os.environ.get("MQTT_EXTERNAL_URL", "")
                ),
                "default_lang": str(
                    self._user_overrides.get("default_lang") or settings.default_lang or "fr"
                ),
                "showcase_api_key": user_showcase_key,
                "max_concurrent_jobs": int(_eff("max_concurrent_jobs", settings.max_concurrent_jobs)),
                "use_process_pool": bool(_eff("use_process_pool", settings.use_process_pool)),
                "ffmpeg_threads_per_job": int(_eff("ffmpeg_threads_per_job", settings.ffmpeg_threads_per_job)),
                "worker_nice_increment": int(_eff("worker_nice_increment", settings.worker_nice_increment)),
                "max_queue_size": int(_eff("max_queue_size", settings.max_queue_size)),
                "video_max_duration_limit_s": float(
                    _eff("video_max_duration_limit_s", settings.video_max_duration_limit_s)
                ),
                "server_master_clip_max_s": float(
                    _eff("server_master_clip_max_s", settings.server_master_clip_max_s)
                ),
                "video_max_fps_limit": int(_eff("video_max_fps_limit", settings.video_max_fps_limit)),
                "panorama_duration_s": float(_eff("panorama_duration_s", settings.panorama_duration_s)),
                "ytdlp_max_filesize_mb": int(_eff("ytdlp_max_filesize_mb", settings.ytdlp_max_filesize_mb)),
                "default_video_mode": str(_eff("default_video_mode", settings.default_video_mode)),
                "default_color_boost": str(_eff("default_color_boost", settings.default_color_boost)),
                "session_ttl_seconds": int(_eff("session_ttl_seconds", settings.session_ttl_seconds)),
                "batch_ttl_seconds": int(_eff("batch_ttl_seconds", settings.batch_ttl_seconds)),
                "cache_dir": str(_eff("cache_dir", str(settings.cache_dir))),
                "log_level": str(_eff("log_level", settings.log_level)).upper(),
                "mdns_enabled": bool(_eff("mdns_enabled", settings.mdns_enabled)),
                "mdns_hostname": str(_eff("mdns_hostname", settings.mdns_hostname)),
                "trust_proxy_headers": bool(_eff("trust_proxy_headers", settings.trust_proxy_headers)),
            }

    def get_vault_status(self) -> Dict[str, bool]:
        """
        Return boolean indicators of active developer/environment credentials and user overrides
        without exposing any secret value.
        """
        with self._lock:
            self._ensure_loaded()
            effective = self.get_all()
            return {
                "screenscraper_dev_active": bool(
                    effective.get("screenscraper_dev_id") and effective.get("screenscraper_dev_pass")
                ),
                "screenscraper_dev_from_prod_vault": bool(
                    has_prod_vault_secret("screenscraper_dev_id")
                    and has_prod_vault_secret("screenscraper_dev_pass")
                ),
                "tmdb_builtin_active": bool(self._resolve_env_or_prod_vault("tmdb_api_key")),
                "tmdb_from_prod_vault": has_prod_vault_secret("tmdb_api_key"),
                "tmdb_user_override": bool(str(self._user_overrides.get("tmdb_api_key") or "").strip()),
                "tvdb_builtin_active": bool(self._resolve_env_or_prod_vault("tvdb_api_key")),
                "tvdb_from_prod_vault": has_prod_vault_secret("tvdb_api_key"),
                "tvdb_user_override": bool(str(self._user_overrides.get("tvdb_api_key") or "").strip()),
                "screenscraper_user_configured": bool(
                    effective.get("screenscraper_user") and effective.get("screenscraper_pass")
                ),
                "showcase_api_key_configured": bool(effective.get("showcase_api_key")),
            }

    def get_public_admin_config(self) -> Dict[str, Any]:
        """
        Return sanitized configuration safe for `GET /api/v1/admin/config` and the Web UI:
        - Never includes `screenscraper_dev_id` or `screenscraper_dev_pass`.
        - Never leaks `.env` or `_prod_vault` `tmdb_api_key` / `tvdb_api_key`.
        - Masks user-saved passwords/keys with `••••••••`.
        """
        with self._lock:
            self._ensure_loaded()
            effective = self.get_all()
            user_tmdb = str(self._user_overrides.get("tmdb_api_key") or "").strip()
            user_tvdb = str(self._user_overrides.get("tvdb_api_key") or "").strip()
            user_ss_pass = str(
                self._user_overrides.get("screenscraper_pass")
                or self._resolve_env_or_prod_vault("screenscraper_pass")
            ).strip()
            user_showcase_key = str(effective.get("showcase_api_key") or "").strip()

            return {
                "tmdb_api_key": "••••••••" if user_tmdb else "",
                "tvdb_api_key": "••••••••" if user_tvdb else "",
                "screenscraper_user": str(effective.get("screenscraper_user") or ""),
                "screenscraper_pass": "••••••••" if user_ss_pass else "",
                "mqtt_mode": effective["mqtt_mode"],
                "mqtt_port": effective["mqtt_port"],
                "mqtt_external_url": effective["mqtt_external_url"],
                "default_lang": effective["default_lang"],
                "showcase_api_key": "••••••••" if user_showcase_key else "",
                "max_concurrent_jobs": effective["max_concurrent_jobs"],
                "effective_max_workers": (
                    max(1, min(16, int(effective["max_concurrent_jobs"])))
                    if int(effective["max_concurrent_jobs"]) > 0
                    else settings.effective_max_workers
                ),
                "detected_cpu_cores": settings.detected_cpu_cores,
                "use_process_pool": effective["use_process_pool"],
                "ffmpeg_threads_per_job": effective["ffmpeg_threads_per_job"],
                "worker_nice_increment": effective["worker_nice_increment"],
                "max_queue_size": effective["max_queue_size"],
                "video_max_duration_limit_s": effective["video_max_duration_limit_s"],
                "server_master_clip_max_s": effective["server_master_clip_max_s"],
                "video_max_fps_limit": effective["video_max_fps_limit"],
                "panorama_duration_s": effective["panorama_duration_s"],
                "ytdlp_max_filesize_mb": effective["ytdlp_max_filesize_mb"],
                "default_video_mode": effective["default_video_mode"],
                "default_color_boost": effective["default_color_boost"],
                "session_ttl_seconds": effective["session_ttl_seconds"],
                "batch_ttl_seconds": effective["batch_ttl_seconds"],
                "cache_dir": effective["cache_dir"],
                "log_level": effective["log_level"],
                "mdns_enabled": effective["mdns_enabled"],
                "mdns_hostname": effective["mdns_hostname"],
                "trust_proxy_headers": effective["trust_proxy_headers"],
            }

    def update(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Persist user-editable settings into `./data/server_config.json` and apply live to `settings`.
        Developer keys (`screenscraper_dev_id`, `screenscraper_dev_pass`) are never written to disk.
        """
        with self._lock:
            self._ensure_loaded()
            for key in USER_PERSISTED_KEYS:
                if key not in updates or updates[key] is None:
                    continue
                val = updates[key]
                if isinstance(val, str) and val.strip() in MASKED_SECRET_PLACEHOLDERS:
                    continue
                if key == "cache_dir" and not str(val).strip():
                    continue
                if key == "mdns_hostname" and not str(val).strip():
                    continue
                self._user_overrides[key] = val

            if "showcase_api_key" in updates and updates["showcase_api_key"] == "":
                settings.showcase_api_key = ""

            self.apply_to_settings()

            path = self.file_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(self._user_overrides, indent=2), encoding="utf-8")
            os.replace(tmp_path, path)
            self._loaded_from = path
            logger.info("[CONFIG] Saved user overrides to %s", path)
            return self.get_public_admin_config()


server_config_store = ServerConfigStore()
