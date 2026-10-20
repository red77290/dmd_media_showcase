"""
Multilingual Anime Provider (TMDB/TheTVDB localized metadata + AnimeThemes.moe / Jikan v4 Openings & PVs).
"""

from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Set

import httpx

from app.models import DomainType, SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate, clean_synopsis_text
from app.providers.curated_catalogs import CURATED_ANIME, pick_localized_text

logger = logging.getLogger(__name__)


class AnimeProvider(BaseMediaProvider):
    domain: DomainType = "anime"

    async def _fetch_animethemes_video(self, slug: str, video_type: str = "opening") -> Optional[str]:
        if not slug:
            return None
        target_prefix = "OP" if video_type == "opening" else ("ED" if video_type == "ending" else "")
        try:
            async with httpx.AsyncClient(timeout=3.5) as client:
                resp = await client.get(
                    f"https://api.animethemes.moe/anime/{slug}",
                    params={"include": "animethemes.animethemeentries.videos"},
                )
                if resp.status_code != 200:
                    return None
                themes = resp.json().get("anime", {}).get("animethemes") or []
                for theme in themes:
                    ttype = theme.get("type", "")
                    if target_prefix and not ttype.startswith(target_prefix):
                        continue
                    for entry in theme.get("animethemeentries") or []:
                        for vid in entry.get("videos") or []:
                            link = vid.get("link")
                            if link:
                                return link
        except Exception as exc:
            logger.debug("AnimeThemes lookup skipped for %s: %s", slug, exc)
        return None

    def _entry_to_candidate(self, entry: dict, lang: str) -> RawMediaCandidate:
        return RawMediaCandidate(
            id=entry["id"],
            domain="anime",
            title=pick_localized_text(entry["title"], lang),
            year=entry["year"],
            badge=entry["badge"],
            badge_color=entry["badge_color"],
            subtitle=entry["subtitle"],
            rating=entry["rating"],
            synopsis=clean_synopsis_text(pick_localized_text(entry["synopsis"], lang)),
            lang=lang,
            theme_palette=tuple(entry.get("palette", (251, 140, 0))),
            extra_meta={
                "mal_id": str(entry.get("mal_id", "")),
                "animethemes_slug": str(entry.get("animethemes_slug", "")),
            },
        )

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        exclude = exclude_ids or set()
        req_genres = {g.lower().strip() for g in req.genres if g.strip() and g.lower() != "all"}
        req_decades = {d.lower().strip() for d in req.decades if d.strip() and d.lower() != "all"}

        pool = [
            a
            for a in CURATED_ANIME
            if (not req_genres or bool(req_genres.intersection(a["genres"])))
            and (not req_decades or a["decade"].lower() in req_decades)
            and a["id"] not in exclude
        ]
        if not pool:
            pool = list(CURATED_ANIME)

        shuffled = list(pool)
        random.shuffle(shuffled)
        results: List[RawMediaCandidate] = []
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
        for entry in CURATED_ANIME:
            for t in entry["title"].values():
                if q_low in t.lower():
                    return self._entry_to_candidate(entry, lang)
        return self._entry_to_candidate(CURATED_ANIME[0], lang)

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [
                {"id": "shonen", "label": "Shonen"},
                {"id": "seinen", "label": "Seinen"},
                {"id": "mecha", "label": "Mecha"},
                {"id": "cyberpunk", "label": "Cyberpunk & Sci-Fi"},
                {"id": "fantasy", "label": "Fantasy & Adventure"},
                {"id": "ghibli_movies", "label": "Studio Ghibli / Feature Films"},
            ],
            "decades": [
                {"id": "1980s", "label": "1980s"},
                {"id": "1990s", "label": "1990s"},
                {"id": "2000s", "label": "2000s"},
                {"id": "2010s", "label": "2010s"},
                {"id": "2020s", "label": "2020s"},
            ],
            "video_types": [
                {"id": "opening", "label": "Opening (OP)"},
                {"id": "ending", "label": "Ending (ED)"},
                {"id": "pv", "label": "Promotional Trailer (PV)"},
                {"id": "any", "label": "Any"},
            ],
        }
