"""
Atomic JSON implementation of `ISeenHistoryRepository` (`./data/seen_history.json`).
Guarantees that once a title starts playing on a DMD, it is never served again until reset.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.domain.entities import DomainType
from app.domain.ports.seen_repository import ISeenHistoryRepository
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.persistence.seen")

ALL_DOMAINS: List[DomainType] = ["games", "movies", "series", "anime"]


class JsonSeenHistoryRepository(ISeenHistoryRepository):
    """
    Thread-safe and crash-atomic repository storing consumed media IDs in `./data/seen_history.json`.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._custom_path = file_path
        self._cache: Dict[str, Set[str]] = {d: set() for d in ALL_DOMAINS}
        self._loaded_from: Optional[Path] = None

    @property
    def file_path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "seen_history.json"

    def _ensure_loaded(self) -> None:
        path = self.file_path
        if self._loaded_from == path and path.exists():
            return
        self._cache = {d: set() for d in ALL_DOMAINS}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for d in ALL_DOMAINS:
                        items = raw.get(d)
                        if isinstance(items, list):
                            self._cache[d] = {str(x) for x in items if x}
            except Exception as exc:
                logger.warning("[SEEN] Failed to load %s (%s), initializing empty history", path, exc)
        self._loaded_from = path

    def _persist_locked(self) -> None:
        path = self.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        payload = {d: sorted(self._cache.get(d, set())) for d in ALL_DOMAINS}
        try:
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, path)
            self._loaded_from = path
        except Exception as exc:
            logger.error("[SEEN] Failed to persist seen_history.json: %s", exc)

    def get_seen_ids(self, domain: DomainType) -> Set[str]:
        with self._lock:
            self._ensure_loaded()
            return set(self._cache.get(domain, set()))

    def is_seen(self, domain: DomainType, item_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return item_id in self._cache.get(domain, set())

    def mark_consumed(self, domain: DomainType, item_id: str) -> None:
        if not item_id:
            return
        with self._lock:
            self._ensure_loaded()
            bucket = self._cache.setdefault(domain, set())
            if item_id not in bucket:
                bucket.add(item_id)
                self._persist_locked()
                logger.info(
                    "[SEEN] Marked '%s' as CONSUMED in domain '%s' (total_seen=%d)",
                    item_id,
                    domain,
                    len(bucket),
                )

    def reset(self, domain: Optional[DomainType] = None) -> int:
        with self._lock:
            self._ensure_loaded()
            cleared = 0
            if domain is not None:
                cleared = len(self._cache.get(domain, set()))
                self._cache[domain] = set()
            else:
                for d in ALL_DOMAINS:
                    cleared += len(self._cache.get(d, set()))
                    self._cache[d] = set()
            self._persist_locked()
            logger.info("[SEEN] Reset seen history (domain=%s, cleared=%d)", domain or "ALL", cleared)
            return cleared

    def stats(self) -> Dict[str, int]:
        with self._lock:
            self._ensure_loaded()
            return {d: len(self._cache.get(d, set())) for d in ALL_DOMAINS}


seen_repository = JsonSeenHistoryRepository()
