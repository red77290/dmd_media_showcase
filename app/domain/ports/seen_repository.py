"""
Domain Port: `ISeenHistoryRepository` for Consume-Once persistence (`seen_history.json`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional, Set

from app.domain.entities import DomainType


class ISeenHistoryRepository(ABC):
    """
    Persists consumed media item IDs per domain so the server never serves the same
    title twice until the catalog is exhausted or reset by the user.
    """

    @abstractmethod
    def get_seen_ids(self, domain: DomainType) -> Set[str]:
        """Return the set of all consumed item IDs for `domain`."""

    @abstractmethod
    def is_seen(self, domain: DomainType, item_id: str) -> bool:
        """Return True if `item_id` has already been consumed in `domain`."""

    @abstractmethod
    def mark_consumed(self, domain: DomainType, item_id: str) -> None:
        """Record `item_id` as consumed and persist immediately."""

    @abstractmethod
    def reset(self, domain: Optional[DomainType] = None) -> int:
        """Clear seen history for a single `domain` or all domains. Returns count cleared."""

    @abstractmethod
    def stats(self) -> Dict[str, int]:
        """Return the number of consumed titles per domain."""
