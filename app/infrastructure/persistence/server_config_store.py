"""
Centralized Server Configuration Store (`./data/server_config.json`).

Centralizes all scraper credentials (ScreenScraper, TMDB) and MQTT broker settings
on the server so DMD engines (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`) never need
to store or transmit API keys.
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

logger = logging.getLogger("dmd_showcase.persistence.config")


class ServerConfigStore:
    """
    Thread-safe store for `./data/server_config.json` with environment variable fallback.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._custom_path = file_path
        self._data: Dict[str, Any] = {}
        self._loaded_from: Optional[Path] = None

    @property
    def file_path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "server_config.json"

    def _defaults(self) -> Dict[str, Any]:
        return {
            "tmdb_api_key": settings.tmdb_api_key or "",
            "screenscraper_user": settings.screenscraper_user or "",
            "screenscraper_pass": settings.screenscraper_pass or "",
            "screenscraper_dev_id": settings.screenscraper_dev_id or "",
            "screenscraper_dev_pass": settings.screenscraper_dev_pass or "",
            "mqtt_mode": os.environ.get("MQTT_MODE", "embedded"),  # "embedded" | "external"
            "mqtt_port": int(os.environ.get("MQTT_PORT", "1883")),
            "mqtt_external_url": os.environ.get("MQTT_EXTERNAL_URL", ""),
            "default_lang": settings.default_lang or "fr",
            "showcase_api_key": settings.showcase_api_key or "",
        }

    def _ensure_loaded(self) -> None:
        path = self.file_path
        if self._loaded_from == path and self._data:
            return
        merged = self._defaults()
        if path.exists():
            try:
                disk_data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(disk_data, dict):
                    for k, v in disk_data.items():
                        if v is not None and v != "":
                            merged[k] = v
                        elif k in ("mqtt_external_url", "tmdb_api_key", "screenscraper_user", "screenscraper_pass"):
                            merged[k] = v if v != "" else merged.get(k, "")
            except Exception as exc:
                logger.warning("[CONFIG] Could not read %s: %s", path, exc)
        self._data = merged
        self._loaded_from = path

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            out = dict(self._data)
            # Always reflect runtime settings overrides if store value is empty
            if not out.get("tmdb_api_key") and settings.tmdb_api_key:
                out["tmdb_api_key"] = settings.tmdb_api_key
            if not out.get("screenscraper_user") and settings.screenscraper_user:
                out["screenscraper_user"] = settings.screenscraper_user
            if not out.get("screenscraper_pass") and settings.screenscraper_pass:
                out["screenscraper_pass"] = settings.screenscraper_pass
            if not out.get("screenscraper_dev_id") and settings.screenscraper_dev_id:
                out["screenscraper_dev_id"] = settings.screenscraper_dev_id
            if not out.get("screenscraper_dev_pass") and settings.screenscraper_dev_pass:
                out["screenscraper_dev_pass"] = settings.screenscraper_dev_pass
            return out

    def update(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            for key in (
                "tmdb_api_key",
                "screenscraper_user",
                "screenscraper_pass",
                "screenscraper_dev_id",
                "screenscraper_dev_pass",
                "mqtt_mode",
                "mqtt_port",
                "mqtt_external_url",
                "default_lang",
                "showcase_api_key",
            ):
                if key in updates and updates[key] is not None:
                    self._data[key] = updates[key]

            # Sync with in-memory settings for legacy callers
            settings.tmdb_api_key = str(self._data.get("tmdb_api_key") or "")
            settings.screenscraper_user = str(self._data.get("screenscraper_user") or "")
            settings.screenscraper_pass = str(self._data.get("screenscraper_pass") or "")
            settings.screenscraper_dev_id = str(self._data.get("screenscraper_dev_id") or "")
            settings.screenscraper_dev_pass = str(self._data.get("screenscraper_dev_pass") or "")

            path = self.file_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            os.replace(tmp_path, path)
            self._loaded_from = path
            logger.info("[CONFIG] Saved server_config.json to %s", path)
            return dict(self._data)


server_config_store = ServerConfigStore()
