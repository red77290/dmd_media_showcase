"""
Multilingual Movies Provider (TMDB API v3 + Curated Multilingual Cinema Catalog).
"""

from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Set

import httpx

from app.config import settings
from app.models import DomainType, SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate, clean_synopsis_text
from app.providers.curated_catalogs import CURATED_MOVIES, pick_localized_text

logger = logging.getLogger(__name__)

TMDB_LANG_MAP = {
    "fr": "fr-FR",
    "en": "en-US",
    "es": "es-ES",
    "de": "de-DE",
    "it": "it-IT",
    "pt": "pt-BR",
    "ja": "ja-JP",
}

TMDB_GENRE_IDS = {
    "action": 28,
    "adventure": 12,
    "animation": 16,
    "comedy": 35,
    "crime": 80,
    "drama": 18,
    "fantasy": 14,
    "horror": 27,
    "scifi": 878,
    "thriller": 53,
    "western": 37,
}


class MoviesProvider(BaseMediaProvider):
    domain: DomainType = "movies"

    async def _fetch_from_tmdb(
        self, req: SessionRegisterRequest, exclude: Set[str]
    ) -> List[RawMediaCandidate]:
        if not settings.tmdb_api_key:
            return []

        short_lang = (req.lang or "fr").split("-")[0].lower()
        tmdb_lang = TMDB_LANG_MAP.get(short_lang, "fr-FR")

        genre_ids = [
            str(TMDB_GENRE_IDS[g.lower()])
            for g in req.genres
            if g.lower() in TMDB_GENRE_IDS
        ]
        params: Dict[str, str] = {
            "api_key": settings.tmdb_api_key,
            "language": tmdb_lang,
            "sort_by": "popularity.desc",
            "vote_count.gte": "200",
            "vote_average.gte": str(req.min_rating),
            "page": str(random.randint(1, 5)),
        }
        if genre_ids:
            params["with_genres"] = "|".join(genre_ids)

        candidates: List[RawMediaCandidate] = []
        try:
            async with httpx.AsyncClient(timeout=4.5) as client:
                resp = await client.get("https://api.themoviedb.org/3/discover/movie", params=params)
                if resp.status_code != 200:
                    return []
                results = resp.json().get("results") or []
                random.shuffle(results)
                for item in results:
                    item_id = f"movie_tmdb_{item.get('id')}"
                    if item_id in exclude:
                        continue
                    release_date = item.get("release_date") or "1990"
                    year = release_date[:4]
                    poster_path = item.get("poster_path")
                    backdrop_path = item.get("backdrop_path")
                    vote = float(item.get("vote_average") or 7.5)

                    cand = RawMediaCandidate(
                        id=item_id,
                        domain="movies",
                        title=item.get("title") or item.get("original_title") or "Unknown Movie",
                        year=year,
                        badge=f"FILM {year[:3]}0s" if len(year) == 4 else "FILM",
                        badge_color="#00ACC1",
                        subtitle=f"Cinema • {year}",
                        rating=f"★ {vote:.1f}",
                        synopsis=clean_synopsis_text(item.get("overview")),
                        lang=req.lang,
                        poster_url=f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else None,
                        backdrop_url=f"https://image.tmdb.org/t/p/w300{backdrop_path}" if backdrop_path else None,
                    )
                    candidates.append(cand)
                    if len(candidates) >= req.count:
                        break
        except Exception as exc:
            logger.warning("TMDB movie discover fallback to curated catalog: %s", exc)
        return candidates

    def _entry_to_candidate(self, entry: dict, lang: str) -> RawMediaCandidate:
        return RawMediaCandidate(
            id=entry["id"],
            domain="movies",
            title=pick_localized_text(entry["title"], lang),
            year=entry["year"],
            badge=entry["badge"],
            badge_color=entry["badge_color"],
            subtitle=entry["subtitle"],
            rating=entry["rating"],
            synopsis=clean_synopsis_text(pick_localized_text(entry["synopsis"], lang)),
            lang=lang,
            theme_palette=tuple(entry.get("palette", (0, 172, 193))),
        )

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        exclude = exclude_ids or set()
        tmdb_items = await self._fetch_from_tmdb(req, exclude)
        if len(tmdb_items) >= req.count:
            return tmdb_items[: req.count]

        req_genres = {g.lower().strip() for g in req.genres if g.strip() and g.lower() != "all"}
        req_decades = {d.lower().strip() for d in req.decades if d.strip() and d.lower() != "all"}

        pool = [
            m
            for m in CURATED_MOVIES
            if (not req_genres or bool(req_genres.intersection(m["genres"])))
            and (not req_decades or m["decade"].lower() in req_decades)
            and m.get("rating_num", 8.0) >= req.min_rating
            and m["id"] not in exclude
        ]
        if not pool:
            pool = list(CURATED_MOVIES)

        shuffled = list(pool)
        random.shuffle(shuffled)
        results = list(tmdb_items)
        idx = 0
        while len(results) < req.count:
            entry = shuffled[idx % len(shuffled)]
            cand = self._entry_to_candidate(entry, req.lang)
            if idx >= len(shuffled):
                cand.id = f"{cand.id}_{idx}"
            results.append(cand)
            idx += 1

        return results

    async def search_on_demand(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[RawMediaCandidate]:
        q_low = query.lower().strip()
        for entry in CURATED_MOVIES:
            for t in entry["title"].values():
                if q_low in t.lower():
                    return self._entry_to_candidate(entry, lang)
        return self._entry_to_candidate(CURATED_MOVIES[0], lang)

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [
                {"id": "scifi", "label": "Sci-Fi"},
                {"id": "action", "label": "Action"},
                {"id": "adventure", "label": "Adventure"},
                {"id": "animation", "label": "Animation"},
                {"id": "horror", "label": "Horror"},
                {"id": "thriller", "label": "Thriller"},
                {"id": "comedy", "label": "Comedy"},
                {"id": "cult", "label": "Cult Classics"},
            ],
            "decades": [
                {"id": "1970s", "label": "1970s"},
                {"id": "1980s", "label": "1980s"},
                {"id": "1990s", "label": "1990s"},
                {"id": "2000s", "label": "2000s"},
                {"id": "2010s", "label": "2010s"},
                {"id": "2020s", "label": "2020s"},
            ],
        }
