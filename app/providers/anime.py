"""
Multilingual Anime Provider (AnimeThemes.moe direct OP/ED streams + Jikan v4 / MyAnimeList + TMDB).
"""

from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Set

import httpx

from app.models import DomainType, SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate, clean_synopsis_text
from app.providers.curated_catalogs import CURATED_ANIME, pick_localized_text

logger = logging.getLogger("dmd_showcase.scraper.anime")


class AnimeProvider(BaseMediaProvider):
    domain: DomainType = "anime"

    async def _fetch_animethemes_video(self, slug: str, video_type: str = "opening") -> Optional[str]:
        if not slug:
            return None
        target_prefix = "OP" if video_type == "opening" else ("ED" if video_type == "ending" else "")
        try:
            async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://api.animethemes.moe/anime/{slug}",
                    params={"include": "animethemes.animethemeentries.videos,images"},
                )
                if resp.status_code != 200:
                    return None
                anime_obj = resp.json().get("anime") or {}
                themes = anime_obj.get("animethemes") or []
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
            logger.info("[SCRAPER:anime] AnimeThemes lookup skipped for '%s': %s", slug, exc)
        return None

    async def _enrich_with_jikan(self, candidate: RawMediaCandidate, mal_id: int) -> None:
        if not mal_id:
            return
        try:
            async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
                resp = await client.get(f"https://api.jikan.moe/v4/anime/{mal_id}")
                if resp.status_code == 200:
                    data = resp.json().get("data") or {}
                    images = (data.get("images") or {}).get("jpg") or {}
                    large_img = images.get("large_image_url") or images.get("image_url")
                    if large_img:
                        if not candidate.poster_url:
                            candidate.poster_url = large_img
                        candidate.add_unique_image(large_img)
                    trailer = data.get("trailer") or {}
                    yt_id = trailer.get("youtube_id")
                    if yt_id and not candidate.youtube_url:
                        candidate.youtube_url = f"https://www.youtube.com/watch?v={yt_id}"
        except Exception as exc:
            logger.info("[SCRAPER:anime] Jikan v4 lookup skipped for mal_id=%s: %s", mal_id, exc)

    async def _enrich_candidate(
        self, cand: RawMediaCandidate, entry: dict, req: SessionRegisterRequest
    ) -> RawMediaCandidate:
        slug = entry.get("animethemes_slug", "")
        mal_id = int(entry.get("mal_id", 0) or 0)
        primary = (req.primary_source or "auto").lower().strip()

        if primary == "jikan":
            await self._enrich_with_jikan(cand, mal_id)
            vid = await self._fetch_animethemes_video(slug, req.video_type)
            if vid and not cand.video_url:
                cand.video_url = vid
        else:
            vid = await self._fetch_animethemes_video(slug, req.video_type)
            if vid:
                cand.video_url = vid
                cand.source_used = "animethemes"
            await self._enrich_with_jikan(cand, mal_id)

        logger.info(
            "[SCRAPER:anime] Enriched '%s' -> poster=%s, images=%d, direct_video=%s, youtube=%s",
            cand.title,
            bool(cand.poster_url),
            len(cand.image_urls),
            bool(cand.video_url),
            bool(cand.youtube_url),
        )
        return cand

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
        exclude = set(exclude_ids or set())
        req_genres = {g.lower().strip() for g in req.genres if g.strip() and g.lower() != "all"}
        req_decades = {d.lower().strip() for d in req.decades if d.strip() and d.lower() != "all"}

        matching_all = [
            a
            for a in CURATED_ANIME
            if (not req_genres or bool(req_genres.intersection(a["genres"])))
            and (not req_decades or a["decade"].lower() in req_decades)
        ]
        if not matching_all:
            matching_all = list(CURATED_ANIME)

        unseen = [a for a in matching_all if a["id"] not in exclude]
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
            cand = await self._enrich_candidate(cand, entry, req)
            results.append(cand)
            idx += 1

        logger.info(
            "[SCRAPER:anime] Selected %d anime items (source=%s): %s",
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
        synth_req = SessionRegisterRequest(count=1, lang=lang)
        for entry in CURATED_ANIME:
            for t in entry["title"].values():
                if q_low in t.lower():
                    cand = self._entry_to_candidate(entry, lang)
                    return await self._enrich_candidate(cand, entry, synth_req)
        cand = self._entry_to_candidate(CURATED_ANIME[0], lang)
        return await self._enrich_candidate(cand, CURATED_ANIME[0], synth_req)

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "sources": [
                {"id": "auto", "label": "Auto (AnimeThemes.moe -> Jikan v4)"},
                {"id": "animethemes", "label": "AnimeThemes.moe (Direct OP/ED Videos)"},
                {"id": "jikan", "label": "Jikan v4 / MyAnimeList"},
            ],
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
