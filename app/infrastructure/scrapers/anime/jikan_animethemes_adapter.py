"""
Primary Anime Scraper Adapter: Jikan v4 (MyAnimeList) + AnimeThemes.moe (`JikanAnimeThemesAdapter`).

Keyless public anime catalog adapter implementing `IScraperSource`:
- Uniform random pagination across `api.jikan.moe/v4/anime` (`last_visible_page`).
- Enriches candidates with direct creditless `.webm` OP/ED streams from `api.animethemes.moe`
  or clean HD YouTube trailers (`trailer.url`).
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.catalog_metadata import ANIME_GENRES, DECADE_BOUNDS
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_wikipedia_localized_synopsis,
)

logger = logging.getLogger("dmd_showcase.scrapers.anime.jikan")


async def enrich_with_animethemes_webm(
    client: Any,
    cand: MediaCandidate,
    video_type: str = "opening",
) -> None:
    """Attach direct creditless `.webm` OP/ED stream and high-res cover from `api.animethemes.moe`."""
    target_prefix = "OP" if video_type == "opening" else ("ED" if video_type == "ending" else "")
    try:
        resp = await client.get(
            "https://api.animethemes.moe/anime",
            params={
                "filter[name-like]": f"%{cand.title}%",
                "include": "animethemes.animethemeentries.videos,images",
                "page[size]": "3",
            },
            timeout=4.0,
        )
        if resp.status_code != 200:
            return
        anime_list = resp.json().get("anime") or []
        if not anime_list:
            return
        anime_obj = anime_list[0]
        for img in anime_obj.get("images") or []:
            link = img.get("link")
            if link:
                if not cand.poster_url:
                    cand.poster_url = str(link)
                    cand.jacket_url = str(link)
                cand.add_unique_snapshot(str(link))

        for theme in anime_obj.get("animethemes") or []:
            ttype = str(theme.get("type") or "")
            if target_prefix and not ttype.startswith(target_prefix):
                continue
            for entry in theme.get("animethemeentries") or []:
                for vid in entry.get("videos") or []:
                    link = vid.get("link")
                    if link and not cand.video_url:
                        cand.video_url = str(link)
                        return
        for theme in anime_obj.get("animethemes") or []:
            for entry in theme.get("animethemeentries") or []:
                for vid in entry.get("videos") or []:
                    link = vid.get("link")
                    if link and not cand.video_url:
                        cand.video_url = str(link)
                        return
    except Exception as exc:
        logger.debug("[AnimeThemes] Lookup skipped for '%s': %s", cand.title, exc)


class JikanAnimeThemesAdapter(IScraperSource):
    """
    Primary `IScraperSource` for `anime` querying Jikan v4 + AnimeThemes.moe.
    """

    source_id: str = "jikan_animethemes"
    domain: DomainType = "anime"
    role: str = "primary"

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [{"id": gid, "label": meta["label"]} for gid, meta in ANIME_GENRES.items()],
            "decades": [
                {"id": d, "label": d} for d in DECADE_BOUNDS if d != "1960s"
            ],
        }

    async def _build_jikan_candidate(
        self,
        client: Any,
        item: Dict[str, Any],
        g_meta: Dict[str, Any],
        lang: str,
        video_type: str,
    ) -> Optional[MediaCandidate]:
        mal_id = item.get("mal_id")
        if not mal_id:
            return None
        title = str(item.get("title_english") or item.get("title") or "").strip()
        if not title:
            return None

        images = (item.get("images") or {}).get("jpg") or {}
        poster_url = images.get("large_image_url") or images.get("image_url")
        if not poster_url:
            return None

        year = str(item.get("year") or ((item.get("aired") or {}).get("from") or "2012"))[:4]
        score = float(item.get("score") or 8.2)
        synopsis = clean_synopsis_text(item.get("synopsis"), max_chars=300)

        if lang != "en" or not synopsis:
            wiki_syn, _ = await fetch_wikipedia_localized_synopsis(client, title, lang=lang)
            if wiki_syn:
                synopsis = wiki_syn

        trailer = item.get("trailer") or {}
        yt_id = trailer.get("youtube_id")
        yt_url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else trailer.get("url")
        max_img = ((trailer.get("images") or {}).get("maximum_image_url")) or None

        cand = MediaCandidate(
            id=f"anime_jikan_{mal_id}",
            domain="anime",
            title=title,
            year=year,
            badge=g_meta["label"][:8].upper(),
            badge_color=g_meta["color"],
            subtitle=f"{g_meta['label']} • {year}"[:28],
            rating=f"{score:.1f}",
            synopsis=synopsis or f"{title} ({year}) — {g_meta['label']} anime.",
            lang=lang,
            poster_url=str(poster_url),
            jacket_url=str(poster_url),
            marquee_url=str(max_img or poster_url),
            backdrop_url=str(max_img) if max_img else None,
            youtube_url=str(yt_url) if yt_url else None,
            source_id=self.source_id,
            theme_palette=g_meta["palette"],
        )
        if max_img:
            cand.add_unique_snapshot(str(max_img))

        await enrich_with_animethemes_webm(client, cand, video_type=video_type)
        if not cand.video_url and not cand.youtube_url:
            cand.youtube_url = f"ytsearch1:{title} anime {video_type if video_type != 'any' else 'opening'} HD"
        return cand

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        genre_keys = [g.lower().strip() for g in criteria.genres if g.lower().strip() in ANIME_GENRES]
        chosen_genre_key = random.choice(genre_keys) if genre_keys else "shonen"
        g_meta = ANIME_GENRES[chosen_genre_key]

        base_params: Dict[str, str] = {
            "genres": str(g_meta["mal_id"]),
            "order_by": "members",
            "sort": "desc",
            "limit": "25",
            "sfw": "true",
        }
        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            start_d, end_d = DECADE_BOUNDS[random.choice(valid_decades)]
            base_params["start_date"] = start_d
            base_params["end_date"] = end_d

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=5.5) as client:
            for _ in range(max(2, count * 2)):
                if len(results) >= count:
                    break
                params = dict(base_params)
                params["page"] = str(random.randint(1, 12))
                try:
                    resp = await client.get("https://api.jikan.moe/v4/anime", params=params)
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    last_page = int(((payload.get("pagination") or {}).get("last_visible_page")) or 1)
                    if int(params["page"]) > last_page and last_page >= 1:
                        params["page"] = str(random.randint(1, last_page))
                        resp = await client.get("https://api.jikan.moe/v4/anime", params=params)
                        payload = resp.json() if resp.status_code == 200 else {}

                    items = payload.get("data") or []
                    random.shuffle(items)
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        cid = f"anime_jikan_{item.get('mal_id')}"
                        if cid in seen_ids:
                            continue
                        cand = await self._build_jikan_candidate(
                            client, item, g_meta, criteria.lang, criteria.video_type
                        )
                        if cand:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    logger.debug("[Jikan] Discover error: %s", exc)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        if not query.strip():
            return None
        try:
            async with create_async_http_client(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.jikan.moe/v4/anime",
                    params={"q": query.strip(), "limit": "5", "sfw": "true"},
                )
                if resp.status_code == 200:
                    for item in resp.json().get("data") or []:
                        if isinstance(item, dict):
                            cand = await self._build_jikan_candidate(
                                client, item, ANIME_GENRES["shonen"], lang, "opening"
                            )
                            if cand:
                                return cand
        except Exception:
            pass
        return None
