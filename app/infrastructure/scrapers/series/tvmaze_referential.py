"""
Live Referential Strategy for TVMaze (`TvMazeAdapter`).
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import FilterCriteria
from app.domain.ports.referential_strategy import IScraperReferentialStrategy

logger = logging.getLogger("dmd_showcase.scrapers.referential.tvmaze")


class TvMazeReferentialStrategy(IScraperReferentialStrategy):
    """Harvests active TV genres and show page bounds from `https://api.tvmaze.com/shows`."""

    source_id = "tvmaze"

    def __init__(self, store_cls: Optional[Any] = None) -> None:
        if store_cls is None:
            from app.infrastructure.scrapers.referential_store import LiveReferentialStore

            store_cls = LiveReferentialStore
        self.store = store_cls(self.source_id, ttl_seconds=86400)

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        genres: Set[str] = set()
        try:
            resp = await client.get(
                "https://api.tvmaze.com/shows", params={"page": "0"}, timeout=5.0
            )
            if resp.status_code == 200:
                for sh in resp.json() or []:
                    if isinstance(sh, dict):
                        for g in sh.get("genres") or []:
                            if g:
                                genres.add(str(g).strip())
        except Exception as exc:
            logger.debug("[REFERENTIAL:tvmaze] Sync skipped: %s", exc)
        return self.store.save(tokens=sorted(genres), metadata={"max_verified_page": 240})

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        max_page = int((ref.get("metadata") or {}).get("max_verified_page") or 200)
        return [str(p) for p in random.sample(range(0, max_page), k=min(count, max_page))]
