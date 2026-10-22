"""
DDD Port: `IScraperReferentialStrategy`.

Defines the per-source strategy interface for synchronizing live upstream referentials
(systems, genres, pagination bounds, dynamic catalog identifiers) in real time without
hardcoded static item lists.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Set

from app.domain.entities import FilterCriteria


class IScraperReferentialStrategy(ABC):
    """
    Strategy port decoupling live referential synchronization and random discovery
    from individual `IScraperSource` adapters.
    """

    source_id: str

    @abstractmethod
    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        """
        Fetch or refresh the live referential for this scraper source from its upstream
        endpoints and persist it in `./data/referentials/{source_id}.json`.
        """

    @abstractmethod
    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        """
        Return `count` unseen dynamic query tokens or upstream identifiers sampled
        from the live-synchronized referential, triggering a real-time incremental
        refresh whenever the unseen pool for `criteria` runs low.
        """

