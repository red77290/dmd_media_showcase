"""
Atomic JSON implementation of `ISeenHistoryRepository` (`./data/seen_history.json`).
Guarantees that once a title starts playing on a DMD, it is never served again until reset.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.domain.entities import DomainType
from app.domain.ports.seen_repository import ISeenHistoryRepository
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.persistence.seen")

ALL_DOMAINS: List[DomainType] = ["games", "movies", "series", "anime"]
_LIBRETRO_ID_RE = re.compile(r"^games_libretro_([a-z0-9_]+?)_([a-z0-9_]+)$")


class JsonSeenHistoryRepository(ISeenHistoryRepository):
    """
    Thread-safe and crash-atomic repository storing consumed media IDs in `./data/seen_history.json`,
    along with cross-source canonical game fingerprints so regional clones or fallback equivalents
    of an already-played game are never repeated across restarts.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._custom_path = file_path
        self._cache: Dict[str, Set[str]] = {d: set() for d in ALL_DOMAINS}
        self._fingerprints: Dict[str, Set[str]] = {d: set() for d in ALL_DOMAINS}
        self._staged_fingerprints: Dict[Tuple[str, str], Set[str]] = {}
        self._loaded_from: Optional[Path] = None
        self._loaded_mtime_ns: Optional[int] = None

    @property
    def file_path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "seen_history.json"

    @staticmethod
    def _derive_implicit_fingerprints(domain: DomainType, item_id: str) -> Set[str]:
        if domain != "games" or not item_id:
            return set()
        m = _LIBRETRO_ID_RE.match(item_id)
        if m:
            return {f"games_fp_{m.group(1)}:{m.group(2)}"}
        return set()

    def _ensure_loaded(self) -> None:
        path = self.file_path
        mtime_ns: Optional[int] = None
        if path.exists():
            try:
                mtime_ns = path.stat().st_mtime_ns
            except OSError:
                mtime_ns = None
        if self._loaded_from == path and path.exists() and self._loaded_mtime_ns == mtime_ns:
            return
        self._cache = {d: set() for d in ALL_DOMAINS}
        self._fingerprints = {d: set() for d in ALL_DOMAINS}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw_fps = raw.get("_fingerprints")
                    for d in ALL_DOMAINS:
                        items = raw.get(d)
                        if isinstance(items, list):
                            self._cache[d] = {str(x) for x in items if x}
                            for item_id in self._cache[d]:
                                self._fingerprints[d].update(
                                    self._derive_implicit_fingerprints(d, item_id)
                                )
                        if isinstance(raw_fps, dict):
                            fp_items = raw_fps.get(d)
                            if isinstance(fp_items, list):
                                self._fingerprints[d].update(str(x) for x in fp_items if x)
            except Exception as exc:
                logger.warning("[SEEN] Failed to load %s (%s), initializing empty history", path, exc)
        self._loaded_from = path
        self._loaded_mtime_ns = mtime_ns

    def _persist_locked(self) -> None:
        path = self.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        payload: Dict[str, Any] = {d: sorted(self._cache.get(d, set())) for d in ALL_DOMAINS}
        if any(self._fingerprints.get(d) for d in ALL_DOMAINS):
            payload["_fingerprints"] = {
                d: sorted(self._fingerprints.get(d, set())) for d in ALL_DOMAINS
            }
        try:
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, path)
            self._loaded_from = path
            try:
                self._loaded_mtime_ns = path.stat().st_mtime_ns
            except OSError:
                self._loaded_mtime_ns = None
        except Exception as exc:
            logger.error("[SEEN] Failed to persist seen_history.json: %s", exc)

    def stage_candidate_fingerprint(
        self,
        domain: DomainType,
        item_id: str,
        fingerprints: Iterable[str],
    ) -> None:
        """
        Stage canonical fingerprints for a prepared candidate `item_id` so they are persisted
        into `_fingerprints` if and when `mark_consumed(domain, item_id)` is called.
        """
        if not item_id:
            return
        cleaned = {str(fp) for fp in fingerprints if fp}
        if not cleaned:
            return
        with self._lock:
            bucket = self._staged_fingerprints.setdefault((domain, item_id), set())
            bucket.update(cleaned)

    def get_seen_fingerprints(
        self,
        domain: DomainType,
        include_item_ids: Optional[Iterable[str]] = None,
    ) -> Set[str]:
        """Return persisted consumed fingerprints plus staged fingerprints for `include_item_ids`."""
        with self._lock:
            self._ensure_loaded()
            out = set(self._fingerprints.get(domain, set()))
            for extra_id in include_item_ids or ():
                if not extra_id:
                    continue
                out.update(self._staged_fingerprints.get((domain, extra_id), set()))
                out.update(self._derive_implicit_fingerprints(domain, extra_id))
            return out

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
            fp_bucket = self._fingerprints.setdefault(domain, set())
            staged = self._staged_fingerprints.pop((domain, item_id), set())
            new_fps = (staged | self._derive_implicit_fingerprints(domain, item_id)) - fp_bucket
            changed = False
            if item_id not in bucket:
                bucket.add(item_id)
                changed = True
            if new_fps:
                fp_bucket.update(new_fps)
                changed = True
            if changed:
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
                self._fingerprints[domain] = set()
                for k in [k for k in self._staged_fingerprints if k[0] == domain]:
                    self._staged_fingerprints.pop(k, None)
            else:
                for d in ALL_DOMAINS:
                    cleared += len(self._cache.get(d, set()))
                    self._cache[d] = set()
                    self._fingerprints[d] = set()
                self._staged_fingerprints.clear()
            self._persist_locked()
            logger.info("[SEEN] Reset seen history (domain=%s, cleared=%d)", domain or "ALL", cleared)
            return cleared

    def stats(self) -> Dict[str, int]:
        with self._lock:
            self._ensure_loaded()
            return {d: len(self._cache.get(d, set())) for d in ALL_DOMAINS}


seen_repository = JsonSeenHistoryRepository()
