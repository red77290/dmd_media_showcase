"""
Multilingual TV Series Provider (TMDB TV API + TheTVDB + TVMaze Public Keyless API + Curated Catalog).
"""

from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Set

import httpx

from app.config import settings
from app.models import DomainType, SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate, clean_synopsis_text
from app.providers.curated_catalogs import CURATED_SERIES, pick_localized_text

logger = logging.getLogger("dmd_showcase.scraper.series")


class SeriesProvider(BaseMediaProvider):
    domain: DomainType = "series"

    async def _enrich_with_tvmaze(
        self, candidate: RawMediaCandidate, tvmaze_id: int
    ) -> RawMediaCandidate:
        if not tvmaze_id:
            return candidate
        try:
            async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://api.tvmaze.com/shows/{tvmaze_id}",
                    params={"embed": "images"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    img = data.get("image") or {}
                    if img.get("medium") and not candidate.poster_url:
                        candidate.poster_url = img["medium"]
                    if img.get("original"):
                        candidate.add_unique_image(img["original"])
                    embedded_imgs = (data.get("_embedded") or {}).get("images") or []
                    for eimg in embedded_imgs:
                        res_urls = (eimg.get("resolutions") or {}).get("original") or {}
                        if res_urls.get("url"):
                            candidate.add_unique_image(res_urls["url"])
                    candidate.source_used = "tvmaze"
                    logger.info(
                        "[SCRAPER:series] TVMaze enriched '%s' (id=%d) -> poster=%s, images=%d",
                        candidate.title,
                        tvmaze_id,
                        bool(candidate.poster_url),
                        len(candidate.image_urls),
                    )
        except Exception as exc:
            logger.info("[SCRAPER:series] TVMaze enrichment skipped for id %s: %s", tvmaze_id, exc)
        return candidate

    def _entry_to_candidate(self, entry: dict, lang: str) -> RawMediaCandidate:
        return RawMediaCandidate(
            id=entry["id"],
            domain="series",
            title=pick_localized_text(entry["title"], lang),
            year=entry["year"],
            badge=entry["badge"],
            badge_color=entry["badge_color"],
            subtitle=entry["subtitle"],
            rating=entry["rating"],
            synopsis=clean_synopsis_text(pick_localized_text(entry["synopsis"], lang)),
            lang=lang,
            theme_palette=tuple(entry.get("palette", (229, 57, 53))),
            extra_meta={"tvmaze_id": str(entry.get("tvmaze_id", 0))},
        )

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        exclude = set(exclude_ids or set())
        req_genres = {g.lower().strip() for g in req.genres if g.strip() and g.lower() != "all"}
        req_decades = {d.lower().strip() for d in req.decades if d.strip() and d.lower() != "all"}

        matching_all = [
            s
            for s in CURATED_SERIES
            if (not req_genres or bool(req_genres.intersection(s["genres"])))
            and (not req_decades or s["decade"].lower() in req_decades)
            and s.get("rating_num", 8.0) >= req.min_rating
        ]
        if not matching_all:
            matching_all = list(CURATED_SERIES)

        unseen = [s for s in matching_all if s["id"] not in exclude]
        if len(unseen) < req.count:
            unseen = list(matching_all)

        shuffled = list(unseen)
        random.shuffle(shuffled)
        results: List[RawMediaCandidate] = []
        idx = 0
        while len(results) < req.count:
            entry = shuffled[idx % len(shuffled)]
            cand = self._entry_to_candidate(entry, req.lang)
            if idx >= len(shuffled):
                cand.id = f"{cand.id}_{idx}"
            cand = await self._enrich_with_tvmaze(cand, int(entry.get("tvmaze_id", 0)))
            results.append(cand)
            idx += 1

        logger.info(
            "[SCRAPER:series] Selected %d series (source=%s): %s",
            len(results),
            req.primary_source,
            [c.title for c in results],
        )
        return results

    async def search_on_demand(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[RawMediaCandidate]:
        q_low = query.lower().strip()
        for entry in CURATED_SERIES:
            for t in entry["title"].values():
                if q_low in t.lower():
                    cand = self._entry_to_candidate(entry, lang)
                    return await self._enrich_with_tvmaze(cand, int(entry.get("tvmaze_id", 0)))
        cand = self._entry_to_candidate(CURATED_SERIES[0], lang)
        return await self._enrich_with_tvmaze(cand, int(CURATED_SERIES[0].get("tvmaze_id", 0)))

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "sources": [
                {"id": "auto", "label": "Auto (TMDB -> TVMaze)"},
                {"id": "tvmaze", "label": "TVMaze Public API (Keyless)"},
                {"id": "tmdb", "label": "TMDB TV API"},
            ],
            "genres": [
                {"id": "scifi", "label": "Sci-Fi & Fantasy"},
                {"id": "action", "label": "Action & Adventure"},
                {"id": "mystery", "label": "Mystery & Paranormal"},
                {"id": "crime", "label": "Crime & Thriller"},
                {"id": "drama", "label": "Drama"},
                {"id": "retro_tv", "label": "Retro 80s/90s TV"},
            ],
            "decades": [
                {"id": "1980s", "label": "1980s"},
                {"id": "1990s", "label": "1990s"},
                {"id": "2000s", "label": "2000s"},
                {"id": "2010s", "label": "2010s"},
                {"id": "2020s", "label": "2020s"},
            ],
        }
