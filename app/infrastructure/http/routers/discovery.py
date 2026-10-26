"""
Catalog Discovery, Filter Taxonomy & Search Routes (`app/infrastructure/http/routers/discovery.py`).
"""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Query

from app.domain.entities import MediaCandidate
from app.infrastructure.http.dependencies import (
    get_orchestrator,
    get_scraper_registry,
    get_source_chain,
)
from app.models import (
    DomainType,
    SearchCandidateModel,
    SearchCandidatesResponse,
    SearchRequestPayload,
)

router = APIRouter()


@router.get("/api/v1/{domain}/filters", tags=["Discovery"])
@router.get("/api/v1/{domain}/referential", tags=["Discovery"])
async def get_domain_filters(
    domain: DomainType,
    lang: str = Query(
        default="fr",
        description="Preferred UI/content language (`fr`, `en`, `es`, `de`, `it`, `pt`, `ja`)",
    ),
) -> Dict[str, object]:
    """Return consolidated localized filter categories, relational system capabilities, genres, decades, and metadata sources."""
    filters = get_scraper_registry().available_filters(domain=domain, lang=lang)
    return {
        "domain": domain,
        "languages": ["fr", "en", "es", "de", "it", "pt", "ja"],
        "text_scroll_speeds": [
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "slow",
            "normal",
            "fast",
            "turbo",
        ],
        "video_modes": ["auto_action", "panorama", "center_crop"],
        "color_boost_modes": ["auto", "pixel_art", "anime", "cinema"],
        "video_types": ["any", "opening", "ending", "pv", "trailer"],
        "card_layouts": [
            "classic_split",
            "poster_right",
            "badge_header",
            "hero_banner",
            "minimal_arcade",
        ],
        "card_themes": ["tinted", "pure_black", "neon_border", "poster_wash", "amber_dmd"],
        "card_fonts": ["arcade_pixel", "clean_sans"],
        **filters,
    }


def _candidate_to_search_model(cand: MediaCandidate, fallback_lang: str) -> SearchCandidateModel:
    return SearchCandidateModel(
        candidate_id=cand.id,
        id=cand.id,
        domain=cand.domain,
        title=cand.title,
        year=cand.year,
        badge=cand.badge,
        badge_color=cand.badge_color,
        subtitle=cand.subtitle,
        rating=cand.rating,
        synopsis=cand.synopsis,
        lang=cand.lang or fallback_lang,
        source_id=cand.source_id,
        poster_url=cand.resolved_jacket_url(),
        marquee_url=cand.marquee_url or cand.backdrop_url,
        snapshot_count=len(cand.snapshot_urls),
        has_video=bool(cand.video_url or cand.youtube_url),
    )


async def _execute_domain_search(
    domain: DomainType,
    query: str,
    lang: str,
    systems: list[str],
    genres: list[str],
    decades: list[str],
    video_type: str,
    limit: int,
) -> SearchCandidatesResponse:
    criteria = get_orchestrator()._parse_domain_criteria(
        domain,
        {
            "lang": lang,
            "systems": systems,
            "genres": genres,
            "decades": decades,
            "video_type": video_type,
        },
        top_lang=lang,
    )
    found = await get_source_chain().search_candidates(
        criteria=criteria,
        query=query,
        limit=limit,
    )
    items = [_candidate_to_search_model(c, criteria.lang) for c in found]
    return SearchCandidatesResponse(
        domain=domain,
        query=query,
        lang=criteria.lang,
        count=len(items),
        items=items,
    )


@router.get(
    "/api/v1/{domain}/search",
    response_model=SearchCandidatesResponse,
    tags=["Discovery", "On-Demand & Voice Assistant"],
)
async def search_domain_candidates_get(
    domain: DomainType,
    q: str = Query(
        default="",
        description="Optional text query (title, ROM name, franchise). Empty returns filtered browse candidates.",
    ),
    query: Optional[str] = Query(default=None, description="Optional alias for `q`."),
    lang: str = Query(
        default="fr",
        description="Content broadcast language (`fr`, `en`, `es`, `de`, `it`, `pt`, `ja`).",
    ),
    systems: Optional[str] = Query(
        default=None,
        description="Comma-separated canonical systems (for `games`).",
    ),
    genres: Optional[str] = Query(
        default=None,
        description="Comma-separated canonical genres.",
    ),
    decades: Optional[str] = Query(
        default=None,
        description="Comma-separated decades (e.g. `1990s,2000s`).",
    ),
    video_type: str = Query(
        default="any",
        description="Preferred video type (`any`, `opening`, `ending`, `pv`, `trailer`).",
    ),
    limit: int = Query(
        default=8,
        ge=1,
        le=20,
        description="Maximum number of candidates to return (`1..20`).",
    ),
) -> SearchCandidatesResponse:
    """
    Search or browse multiple localized media candidates honoring `domain`, `lang`, `systems`, `genres`, `decades`,
    and optional text `q`. Returned candidates are staged in memory so selecting any `candidate_id` via
    `POST /api/v1/clients/{dmd_id}/queue` starts transcoding immediately without re-scraping.
    """
    eff_q = (query if query is not None else q).strip()
    sys_list = [s.strip() for s in systems.split(",") if s.strip()] if systems else []
    gen_list = [g.strip() for g in genres.split(",") if g.strip()] if genres else []
    dec_list = [d.strip() for d in decades.split(",") if d.strip()] if decades else []
    return await _execute_domain_search(
        domain=domain,
        query=eff_q,
        lang=lang,
        systems=sys_list,
        genres=gen_list,
        decades=dec_list,
        video_type=video_type,
        limit=limit,
    )


@router.post(
    "/api/v1/{domain}/search",
    response_model=SearchCandidatesResponse,
    tags=["Discovery", "On-Demand & Voice Assistant"],
)
async def search_domain_candidates_post(
    domain: DomainType,
    payload: SearchRequestPayload,
) -> SearchCandidatesResponse:
    """POST variant of `/api/v1/{domain}/search` accepting JSON filter arrays and content `lang`."""
    eff_q = (payload.query if payload.query is not None else payload.q).strip()
    return await _execute_domain_search(
        domain=domain,
        query=eff_q,
        lang=payload.lang,
        systems=list(payload.systems),
        genres=list(payload.genres),
        decades=list(payload.decades),
        video_type=payload.video_type,
        limit=payload.limit,
    )
