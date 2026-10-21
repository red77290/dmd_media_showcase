"""
Movies Domain Provider facade delegating to the V3.0 DDD `DomainSourceChainService`
(`TmdbMovieAdapter` [Primary] -> `ItunesMovieAdapter` [Fallback]).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from app.application.source_chain_service import source_chain_service
from app.domain.entities import DomainType
from app.models import SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate
from app.providers.games import candidate_to_raw, request_to_criteria


class MoviesProvider(BaseMediaProvider):
    domain: DomainType = "movies"

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        criteria = request_to_criteria("movies", req)
        cands = await source_chain_service.acquire_unseen_candidates(
            criteria=criteria,
            count=req.count,
            extra_exclude_ids=exclude_ids,
        )
        return [candidate_to_raw(c) for c in cands]

    async def search_on_demand(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[RawMediaCandidate]:
        cand = await source_chain_service.search_on_demand("movies", query, lang=lang)
        return candidate_to_raw(cand) if cand else None

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return source_chain_service.available_filters("movies", lang=lang)
