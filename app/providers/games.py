"""
Games Domain Provider facade delegating to the V3.0 DDD `DomainSourceChainService`
(`ScreenScraperAdapter` [Primary] -> `ArcadeItaliaAdapter` [Fallback]).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from app.application.source_chain_service import source_chain_service
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.models import SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate


def candidate_to_raw(cand: MediaCandidate) -> RawMediaCandidate:
    return RawMediaCandidate(
        id=cand.id,
        domain=cand.domain,
        title=cand.title,
        year=cand.year,
        badge=cand.badge,
        badge_color=cand.badge_color,
        subtitle=cand.subtitle,
        rating=cand.rating,
        synopsis=cand.synopsis,
        lang=cand.lang,
        poster_url=cand.poster_url,
        jacket_url=cand.jacket_url,
        marquee_url=cand.marquee_url,
        backdrop_url=cand.backdrop_url,
        snapshot_urls=list(cand.snapshot_urls),
        image_urls=list(cand.snapshot_urls),
        video_url=cand.video_url,
        youtube_url=cand.youtube_url,
        source_used=cand.source_id,
        theme_palette=cand.theme_palette,
        extra_meta=dict(cand.extra_meta),
    )


def request_to_criteria(domain: DomainType, req: SessionRegisterRequest) -> FilterCriteria:
    decades = list(req.decades)
    if req.decade and req.decade not in decades:
        decades.append(req.decade)
    return FilterCriteria(
        domain=domain,
        lang=req.lang,
        systems=list(req.systems),
        genres=list(req.genres),
        decades=decades,
        min_rating=req.min_rating,
        video_type=req.video_type,
        include_poster=req.include_poster,
        include_synopsis=req.include_synopsis,
        text_scroll_speed=req.text_scroll_speed,
        jacket_panorama=req.jacket_panorama,
        marquee_panorama=req.marquee_panorama,
        snapshot_panorama_count=req.effective_snapshot_count(),
        include_video=req.include_video,
        video_max_duration_s=req.video_max_duration_s,
        video_fps=req.video_fps,
        video_mode=req.video_mode,
        color_boost=req.color_boost,
    )


class GamesProvider(BaseMediaProvider):
    domain: DomainType = "games"

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        criteria = request_to_criteria("games", req)
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
        cand = await source_chain_service.search_on_demand("games", query, lang=lang)
        return candidate_to_raw(cand) if cand else None

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return source_chain_service.available_filters("games", lang=lang)
