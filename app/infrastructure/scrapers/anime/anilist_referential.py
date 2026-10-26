"""
Live Referential Strategy for AniList GraphQL (`AniListAdapter`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import FilterCriteria
from app.domain.ports.referential_strategy import IScraperReferentialStrategy

logger = logging.getLogger("dmd_showcase.scrapers.referential.anilist")


class AniListReferentialStrategy(IScraperReferentialStrategy):
    """Synchronizes live `GenreCollection` from `https://graphql.anilist.co`."""

    source_id = "anilist"

    def __init__(self, store_cls: Optional[Any] = None) -> None:
        if store_cls is None:
            from app.infrastructure.scrapers.referential_store import LiveReferentialStore

            store_cls = LiveReferentialStore
        self.store = store_cls(self.source_id, ttl_seconds=86400)

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        genres: List[str] = []
        try:
            resp = await client.post(
                "https://graphql.anilist.co",
                json={"query": "query { GenreCollection }"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                raw_list = (resp.json().get("data") or {}).get("GenreCollection") or []
                genres = [str(g).strip() for g in raw_list if g]
        except Exception as exc:
            logger.debug("[REFERENTIAL:anilist] GenreCollection sync skipped: %s", exc)
        return self.store.save(tokens=genres, metadata={"genres": genres})

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        return list(ref.get("tokens") or [])[:count]
