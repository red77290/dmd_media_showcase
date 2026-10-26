"""
Domain Port: `IOnDemandCachePort` (`app/domain/ports/on_demand_cache.py`).

Decouples `OnDemandQueueService` from the filesystem persistence layer storing optional
pre-converted On-Demand packages keyed by `(domain, system, query, geometry, criteria)`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CachedOnDemandPackage:
    """Restored On-Demand package metadata and rehydrated files map."""

    cache_key: str
    candidate_dict: Dict[str, Any]
    files_map: Dict[str, Any]
    render_signature: str
    size_bytes: int


class IOnDemandCachePort(ABC):
    """Hexagonal port for optional persistent request-keyed On-Demand conversion caching."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return `True` when persistent On-Demand conversion caching is enabled."""

    @abstractmethod
    def get_max_mb(self) -> int:
        """Return maximum cache quota in MiB (`0` = unlimited when enabled)."""

    @abstractmethod
    def update_settings(self, enabled: bool, max_mb: int = 0) -> Dict[str, Any]:
        """Persist `enabled` and `max_mb` settings and enforce quota immediately."""

    @abstractmethod
    def checkout_to_dir(
        self,
        cache_key: str,
        dest_dir: Path,
        expected_signature: str = "",
    ) -> Optional[CachedOnDemandPackage]:
        """
        Restore a cached On-Demand package directly into `dest_dir` in `< 5 ms`,
        updating LRU statistics and rewriting relative asset paths.
        """

    @abstractmethod
    def store_package(
        self,
        cache_key: str,
        source_dir: Path,
        files_map: Dict[str, Any],
        candidate_dict: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> bool:
        """Atomically persist a converted On-Demand package directory into the cache."""

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Return cache configuration, entry count, total MiB used, and recent cached items."""

    @abstractmethod
    def evict_all(self) -> Dict[str, Any]:
        """Atomically purge all cached On-Demand conversions and return freed space stats."""
