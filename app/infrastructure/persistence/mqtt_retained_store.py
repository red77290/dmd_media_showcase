"""
Persistent Retained MQTT Client Configuration Store (`./data/mqtt_retained_clients.json`).

Ensures Mode 1 DMD engines (`dmd_showcase/clients/{dmd_id}/config` with `retain=True`)
survive `docker compose down && up` or server restarts without losing their `N / N+1`
pre-warming registration while their physical rotation is currently on another engine.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.persistence.mqtt_retained")


class RetainedClientConfigStore:
    """
    Thread-safe JSON store for retained `dmd_showcase/clients/{dmd_id}/config` payloads.
    Follows `get_data_dir()` dynamically so isolated test fixtures and Docker volumes work seamlessly.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._custom_path = file_path
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._loaded_from: Optional[Path] = None

    @property
    def file_path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "mqtt_retained_clients.json"

    def _ensure_loaded(self) -> None:
        path = self.file_path
        if self._loaded_from == path:
            return
        loaded: Dict[str, Dict[str, Any]] = {}
        if path.exists():
            try:
                raw_json = json.loads(path.read_text(encoding="utf-8"))
                raw_map = (
                    raw_json.get("clients")
                    if isinstance(raw_json, dict) and "clients" in raw_json
                    else raw_json
                )
                if isinstance(raw_map, dict):
                    for dmd_id, item in raw_map.items():
                        clean_id = str(dmd_id or "").strip()
                        if not clean_id or not isinstance(item, dict):
                            continue
                        payload = item.get("payload")
                        if not isinstance(payload, dict) or payload.get("unregister"):
                            continue
                        loaded[clean_id] = {
                            "dmd_id": clean_id,
                            "topic": str(item.get("topic") or f"dmd_showcase/clients/{clean_id}/config"),
                            "payload": {
                                str(k): v
                                for k, v in payload.items()
                                if not str(k).startswith("_")
                            },
                            "peer_ip": str(item.get("peer_ip") or "").strip(),
                            "saved_at": float(item.get("saved_at") or 0.0),
                        }
            except Exception as exc:
                logger.warning("[MQTT RETAINED] Could not read %s: %s", path, exc)
        self._entries = loaded
        self._loaded_from = path

    def _save_locked(self) -> None:
        path = self.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        doc = {"version": 1, "clients": self._entries}
        tmp_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
        self._loaded_from = path

    def list_entries(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return copy.deepcopy(self._entries)

    def upsert(
        self,
        dmd_id: str,
        topic: str,
        payload: Dict[str, Any],
        peer_ip: str = "",
    ) -> None:
        clean_id = str(dmd_id or "").strip()
        if not clean_id or not isinstance(payload, dict):
            return
        sanitized = {str(k): v for k, v in payload.items() if not str(k).startswith("_")}
        if sanitized.get("unregister"):
            self.remove(clean_id)
            return
        with self._lock:
            self._ensure_loaded()
            prev = self._entries.get(clean_id)
            eff_peer = str(peer_ip or (prev.get("peer_ip") if prev else "") or "").strip()
            self._entries[clean_id] = {
                "dmd_id": clean_id,
                "topic": str(topic or f"dmd_showcase/clients/{clean_id}/config"),
                "payload": sanitized,
                "peer_ip": eff_peer,
                "saved_at": time.time(),
            }
            self._save_locked()

    def remove(self, dmd_id: str) -> bool:
        clean_id = str(dmd_id or "").strip()
        if not clean_id:
            return False
        with self._lock:
            self._ensure_loaded()
            removed = self._entries.pop(clean_id, None)
            if removed is not None:
                self._save_locked()
                return True
            return False


mqtt_retained_store = RetainedClientConfigStore()
