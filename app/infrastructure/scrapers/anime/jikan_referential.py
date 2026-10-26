"""
Live Referential Strategy for Jikan v4 + AnimeThemes (`JikanAnimeThemesAdapter`).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import FilterCriteria
from app.domain.ports.referential_strategy import IScraperReferentialStrategy

logger = logging.getLogger("dmd_showcase.scrapers.referential.jikan")


class JikanReferentialStrategy(IScraperReferentialStrategy):
    """Synchronizes live anime genre IDs from `https://api.jikan.moe/v4/genres/anime`."""

    source_id = "jikan_animethemes"

    def __init__(self, store_cls: Optional[Any] = None) -> None:
        if store_cls is None:
            from app.infrastructure.scrapers.referential_store import LiveReferentialStore

            store_cls = LiveReferentialStore
        self.store = store_cls(self.source_id, ttl_seconds=86400)

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        genres_map: Dict[str, Dict[str, Any]] = {}
        try:
            resp = await client.get("https://api.jikan.moe/v4/genres/anime", timeout=5.0)
            if resp.status_code == 200:
                for item in resp.json().get("data") or []:
                    mal_id = item.get("mal_id")
                    name = str(item.get("name") or "").strip()
                    if mal_id and name:
                        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                        genres_map[slug] = {"mal_id": int(mal_id), "label": name}
        except Exception as exc:
            logger.debug("[REFERENTIAL:jikan] Genre sync skipped: %s", exc)
        return self.store.save(
            tokens=list(genres_map.keys()), metadata={"genres": genres_map}
        )

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        return list(ref.get("tokens") or [])[:count]
