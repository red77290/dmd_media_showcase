"""
Generic Bounded TTL Cache Aggregate (`app/domain/aggregates/artifact_cache.py`).

Reusable across Domain & Application layers (e.g. staged `MediaCandidate` lookup in
`DomainSourceChainService` and rendered `StudioPreviewEntry` artifacts in `StudioPreviewService`).
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Dict, Generic, Iterator, Optional, Tuple, TypeVar

TKey = TypeVar("TKey")
TVal = TypeVar("TVal")


@dataclass
class _TimedEntry(Generic[TVal]):
    value: TVal
    created_at: float


class BoundedTtlCache(Generic[TKey, TVal]):
    """
    Deterministic bounded cache with automatic TTL expiration, max-capacity eviction,
    and an optional `on_evict(key, value)` lifecycle hook (e.g. temporary file cleanup).
    """

    def __init__(
        self,
        ttl_seconds: float = 1800.0,
        max_entries: int = 512,
        on_evict: Optional[Callable[[TKey, TVal], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._on_evict = on_evict
        self._clock = clock
        self._entries: Dict[TKey, _TimedEntry[TVal]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: TKey) -> bool:
        return self.get(key) is not None

    def _evict_key(self, key: TKey) -> Optional[TVal]:
        entry = self._entries.pop(key, None)
        if entry is None:
            return None
        if self._on_evict is not None:
            self._on_evict(key, entry.value)
        return entry.value

    def prune(self, now: Optional[float] = None) -> int:
        """Evict all TTL-expired entries and enforce `max_entries` oldest-first cap."""
        ts = self._clock() if now is None else now
        evicted = 0
        expired_keys = [
            k for k, entry in self._entries.items() if (ts - entry.created_at) > self.ttl_seconds
        ]
        for key in expired_keys:
            if self._evict_key(key) is not None:
                evicted += 1

        if len(self._entries) > self.max_entries:
            sorted_keys = sorted(
                self._entries.keys(), key=lambda k: self._entries[k].created_at
            )
            overflow = len(self._entries) - self.max_entries
            for key in sorted_keys[:overflow]:
                if self._evict_key(key) is not None:
                    evicted += 1
        return evicted

    def put(self, key: TKey, value: TVal, created_at: Optional[float] = None) -> None:
        """Insert or replace `key` and prune expired/overflowing entries."""
        ts = self._clock() if created_at is None else created_at
        if key in self._entries and self._entries[key].value is not value:
            self._evict_key(key)
        self._entries[key] = _TimedEntry(value=value, created_at=ts)
        self.prune(now=ts)

    def get(self, key: TKey) -> Optional[TVal]:
        """Lookup `key`, returning `None` (and evicting) when missing or expired."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if (self._clock() - entry.created_at) > self.ttl_seconds:
            self._evict_key(key)
            return None
        return entry.value

    def pop(self, key: TKey) -> Optional[TVal]:
        """Remove and return `key` without firing `on_evict` (caller takes ownership)."""
        entry = self._entries.pop(key, None)
        return entry.value if entry is not None else None

    def evict(self, key: TKey) -> Optional[TVal]:
        """Remove `key` and invoke `on_evict` if present."""
        return self._evict_key(key)

    def clear(self) -> None:
        """Evict all entries cleanly."""
        for key in list(self._entries.keys()):
            self._evict_key(key)

    def items_with_timestamps(self) -> Iterator[Tuple[TKey, TVal, float]]:
        """Iterate over `(key, value, created_at)` snapshots."""
        for key, entry in list(self._entries.items()):
            yield key, entry.value, entry.created_at
