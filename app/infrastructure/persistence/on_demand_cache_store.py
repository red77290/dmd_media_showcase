"""
Persistent Request-Keyed On-Demand Conversion Cache (`app/infrastructure/persistence/on_demand_cache_store.py`).

Implements `IOnDemandCachePort` over `./data/on_demand_cache/`:
- Disabled by default (`on_demand_cache_enabled=False`) to preserve zero-disk-footprint defaults.
- Keyed strictly by the normalized On-Demand request `(domain, system, query, geometry, style)`
  so cache hits bypass BOTH upstream scraper lookups AND `ffmpeg` / `dmd_gif_converter` transcoding.
- Supports optional LRU disk quota (`on_demand_cache_max_mb > 0`) and atomic 1-click eviction.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.domain.ports.on_demand_cache import CachedOnDemandPackage, IOnDemandCachePort
from app.infrastructure.persistence.server_config_store import (
    ServerConfigStore,
    server_config_store,
)
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.persistence.on_demand_cache")
_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_META_FILENAME = "cache_meta.json"


class FileOnDemandCacheStore(IOnDemandCachePort):
    """Thread-safe filesystem store for persistent request-keyed On-Demand packages."""

    def __init__(
        self,
        cache_root: Optional[Path] = None,
        config_store: Optional[ServerConfigStore] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._custom_root = cache_root
        self._config_store = config_store or server_config_store

    @property
    def root_dir(self) -> Path:
        if self._custom_root is not None:
            return self._custom_root
        return get_data_dir() / "on_demand_cache"

    @staticmethod
    def _sanitize_key(cache_key: str) -> str:
        cleaned = _SAFE_KEY_RE.sub("_", str(cache_key or "").strip()).strip("._")
        return (cleaned or "entry")[:180]

    def is_enabled(self) -> bool:
        cfg = self._config_store.get_all()
        return bool(cfg.get("on_demand_cache_enabled", False))

    def get_max_mb(self) -> int:
        cfg = self._config_store.get_all()
        return max(0, int(cfg.get("on_demand_cache_max_mb") or 0))

    def update_settings(self, enabled: bool, max_mb: int = 0) -> Dict[str, Any]:
        clamped_mb = max(0, int(max_mb or 0))
        self._config_store.update(
            {
                "on_demand_cache_enabled": bool(enabled),
                "on_demand_cache_max_mb": clamped_mb,
            }
        )
        if enabled and clamped_mb > 0:
            self._enforce_quota_bytes(clamped_mb * 1024 * 1024)
        return self.get_stats()

    @staticmethod
    def _dir_size_bytes(entry_dir: Path) -> int:
        total = 0
        for p in entry_dir.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def checkout_to_dir(
        self,
        cache_key: str,
        dest_dir: Path,
        expected_signature: str = "",
    ) -> Optional[CachedOnDemandPackage]:
        if not self.is_enabled():
            return None
        safe_key = self._sanitize_key(cache_key)
        with self._lock:
            entry_dir = self.root_dir / safe_key
            meta_path = entry_dir / _META_FILENAME
            if not entry_dir.is_dir() or not meta_path.is_file():
                return None
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict):
                    return None
                sig = str(meta.get("render_signature") or "")
                if expected_signature and sig and sig != expected_signature:
                    return None
                cand_dict = meta.get("candidate")
                raw_files_map = meta.get("files_map")
                if not isinstance(cand_dict, dict) or not isinstance(raw_files_map, dict):
                    return None

                dest_dir.mkdir(parents=True, exist_ok=True)
                copied_bytes = 0
                for src_file in entry_dir.iterdir():
                    if src_file.name == _META_FILENAME or src_file.name.startswith("."):
                        continue
                    if src_file.is_file():
                        target_file = dest_dir / src_file.name
                        shutil.copy2(src_file, target_file)
                        copied_bytes += target_file.stat().st_size

                files_map: Dict[str, Any] = dict(raw_files_map)
                unified_rel = str(files_map.get("unified_stream_rel") or "")
                if unified_rel:
                    unified_dest = dest_dir / unified_rel
                    if not unified_dest.exists():
                        shutil.rmtree(dest_dir, ignore_errors=True)
                        return None
                    files_map["unified_stream_path"] = str(unified_dest)

                meta["last_accessed_at"] = time.time()
                meta["hits"] = int(meta.get("hits") or 0) + 1
                tmp_meta = entry_dir / f".{_META_FILENAME}.tmp"
                tmp_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                os.replace(tmp_meta, meta_path)

                return CachedOnDemandPackage(
                    cache_key=safe_key,
                    candidate_dict=cand_dict,
                    files_map=files_map,
                    render_signature=sig,
                    size_bytes=copied_bytes,
                )
            except Exception as exc:
                logger.warning("[ONDEMAND CACHE] Checkout failed for '%s': %s", safe_key, exc)
                shutil.rmtree(dest_dir, ignore_errors=True)
                return None

    def store_package(
        self,
        cache_key: str,
        source_dir: Path,
        files_map: Dict[str, Any],
        candidate_dict: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> bool:
        if not self.is_enabled() or not source_dir.is_dir():
            return False
        safe_key = self._sanitize_key(cache_key)
        with self._lock:
            root = self.root_dir
            tmp_dir = root / f".tmp_{safe_key}_{uuid.uuid4().hex[:8]}"
            final_dir = root / safe_key
            try:
                root.mkdir(parents=True, exist_ok=True)
                tmp_dir.mkdir(parents=True, exist_ok=True)
                total_bytes = 0
                for src_file in source_dir.iterdir():
                    if src_file.is_file() and not src_file.name.startswith("."):
                        dst = tmp_dir / src_file.name
                        shutil.copy2(src_file, dst)
                        total_bytes += dst.stat().st_size

                if total_bytes <= 0:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return False

                stored_files_map: Dict[str, Any] = {}
                for k, v in files_map.items():
                    if k == "unified_stream_path" and v:
                        stored_files_map["unified_stream_rel"] = Path(str(v)).name
                    else:
                        stored_files_map[k] = v

                now = time.time()
                meta_doc: Dict[str, Any] = {
                    "cache_key": safe_key,
                    "domain": str(meta.get("domain") or "games"),
                    "system": str(meta.get("system") or ""),
                    "query": str(meta.get("query") or ""),
                    "title": str(meta.get("title") or candidate_dict.get("title") or safe_key),
                    "width": int(meta.get("width") or 128),
                    "height": int(meta.get("height") or 32),
                    "duration_s": float(meta.get("duration_s") or 0.0),
                    "render_signature": str(meta.get("render_signature") or ""),
                    "size_bytes": total_bytes,
                    "created_at": now,
                    "last_accessed_at": now,
                    "hits": 0,
                    "candidate": candidate_dict,
                    "files_map": stored_files_map,
                }
                (tmp_dir / _META_FILENAME).write_text(
                    json.dumps(meta_doc, indent=2), encoding="utf-8"
                )

                if final_dir.exists():
                    old_dir = root / f".old_{safe_key}_{uuid.uuid4().hex[:6]}"
                    os.replace(final_dir, old_dir)
                    shutil.rmtree(old_dir, ignore_errors=True)
                os.replace(tmp_dir, final_dir)

                max_mb = self.get_max_mb()
                if max_mb > 0:
                    self._enforce_quota_bytes(max_mb * 1024 * 1024, protected_key=safe_key)
                return True
            except Exception as exc:
                logger.warning("[ONDEMAND CACHE] Store failed for '%s': %s", safe_key, exc)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return False

    def _remove_entry_dir(self, entry_dir: Path) -> int:
        if not entry_dir.exists():
            return 0
        freed = self._dir_size_bytes(entry_dir)
        evict_tmp = entry_dir.with_name(f".evict_{entry_dir.name}_{uuid.uuid4().hex[:6]}")
        try:
            os.replace(entry_dir, evict_tmp)
            shutil.rmtree(evict_tmp, ignore_errors=True)
        except OSError:
            shutil.rmtree(entry_dir, ignore_errors=True)
        return freed

    def _enforce_quota_bytes(
        self, max_bytes: int, protected_key: Optional[str] = None
    ) -> int:
        if max_bytes <= 0 or not self.root_dir.exists():
            return 0
        entries: List[Tuple[float, Path, int]] = []
        total_bytes = 0
        for d in self.root_dir.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            sz = self._dir_size_bytes(d)
            total_bytes += sz
            last_used = 0.0
            meta_file = d / _META_FILENAME
            if meta_file.is_file():
                try:
                    m = json.loads(meta_file.read_text(encoding="utf-8"))
                    last_used = float(m.get("last_accessed_at") or 0.0)
                except Exception:
                    last_used = 0.0
            entries.append((last_used, d, sz))

        if total_bytes <= max_bytes:
            return 0

        entries.sort(key=lambda item: item[0])
        evicted = 0
        for _ts, d, sz in entries:
            if total_bytes <= max_bytes:
                break
            if protected_key and d.name == protected_key:
                continue
            self._remove_entry_dir(d)
            total_bytes = max(0, total_bytes - sz)
            evicted += 1
        return evicted

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            enabled = self.is_enabled()
            max_mb = self.get_max_mb()
            root = self.root_dir
            items: List[Dict[str, Any]] = []
            total_bytes = 0
            if root.exists():
                for d in sorted(root.iterdir()):
                    if not d.is_dir() or d.name.startswith("."):
                        continue
                    meta_file = d / _META_FILENAME
                    meta: Dict[str, Any] = {}
                    if meta_file.is_file():
                        try:
                            parsed = json.loads(meta_file.read_text(encoding="utf-8"))
                            if isinstance(parsed, dict):
                                meta = parsed
                        except Exception:
                            meta = {}
                    sz = int(meta.get("size_bytes") or self._dir_size_bytes(d))
                    total_bytes += sz
                    items.append(
                        {
                            "cache_key": d.name,
                            "domain": str(meta.get("domain") or "games"),
                            "system": str(meta.get("system") or ""),
                            "query": str(meta.get("query") or ""),
                            "title": str(meta.get("title") or d.name),
                            "resolution": f"{int(meta.get('width') or 128)}x{int(meta.get('height') or 32)}",
                            "duration_s": round(float(meta.get("duration_s") or 0.0), 1),
                            "size_mb": round(sz / (1024 * 1024), 2),
                            "hits": int(meta.get("hits") or 0),
                            "last_accessed_at": float(meta.get("last_accessed_at") or 0.0),
                        }
                    )
            items.sort(key=lambda x: x["last_accessed_at"], reverse=True)
            return {
                "enabled": enabled,
                "max_mb": max_mb,
                "entry_count": len(items),
                "total_bytes": total_bytes,
                "total_mb": round(total_bytes / (1024 * 1024), 2),
                "cache_dir": str(root),
                "items": items[:50],
            }

    def evict_all(self) -> Dict[str, Any]:
        with self._lock:
            root = self.root_dir
            evicted_count = 0
            freed_bytes = 0
            if root.exists():
                for child in list(root.iterdir()):
                    if child.is_dir():
                        freed_bytes += self._remove_entry_dir(child)
                        if not child.name.startswith("."):
                            evicted_count += 1
                    elif child.is_file():
                        try:
                            freed_bytes += child.stat().st_size
                            child.unlink(missing_ok=True)
                        except OSError:
                            pass
            freed_mb = round(freed_bytes / (1024 * 1024), 2)
            logger.info(
                "[ONDEMAND CACHE] Evicted %d cached On-Demand conversion(s) (%.2f MiB freed)",
                evicted_count,
                freed_mb,
            )
            return {
                "status": "evicted",
                "evicted_count": evicted_count,
                "freed_bytes": freed_bytes,
                "freed_mb": freed_mb,
            }


on_demand_cache_store = FileOnDemandCacheStore()
