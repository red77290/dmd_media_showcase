"""
Fallback Anime Scraper Adapter: AniList GraphQL API (`graphql.anilist.co`) + AnimeThemes.moe.

Keyless public GraphQL anime adapter implementing `IScraperSource`:
- Uniform random pagination across AniList's `Page(page: $page, perPage: 25)` query.
- Provides `coverImage.extraLarge` HD jackets, `bannerImage` wide marquees, synopses,
  YouTube trailers (`trailer { id site }`), and direct `.webm` OP/ED streams via `api.animethemes.moe`.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.anime.jikan_animethemes_adapter import enrich_with_animethemes_webm
from app.infrastructure.scrapers.catalog_metadata import ANIME_GENRES, DECADE_BOUNDS
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    fetch_wikipedia_localized_synopsis,
    resolve_multilingual_media_details,
)

logger = logging.getLogger("dmd_showcase.scrapers.anime.anilist")

_ANILIST_QUERY = """
query ($page: Int, $perPage: Int, $genre: String, $tag: String, $startGreater: FuzzyDateInt, $startLesser: FuzzyDateInt, $search: String) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      lastPage
    }
    media(type: ANIME, isAdult: false, sort: POPULARITY_DESC, genre: $genre, tag: $tag, startDate_greater: $startGreater, startDate_lesser: $startLesser, search: $search) {
      id
      idMal
      title {
        english
        romaji
      }
      startDate {
        year
      }
      averageScore
      description(asHtml: false)
      coverImage {
        extraLarge
        large
      }
      bannerImage
      trailer {
        id
        site
      }
    }
  }
}
"""


class AniListAdapter(IScraperSource):
    """
    Keyless Fallback `IScraperSource` for `anime` querying AniList GraphQL + AnimeThemes.moe.
    """

    source_id: str = "anilist"
    domain: DomainType = "anime"
    role: str = "fallback"

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [{"id": gid, "label": meta["label"]} for gid, meta in ANIME_GENRES.items()],
            "decades": [{"id": d, "label": d} for d in DECADE_BOUNDS if d != "1960s"],
        }

    async def _build_anilist_candidate(
        self,
        client: Any,
        node: Dict[str, Any],
        g_meta: Dict[str, Any],
        lang: str,
        video_type: str,
    ) -> Optional[MediaCandidate]:
        al_id = node.get("id")
        if not al_id:
            return None
        mal_id = node.get("idMal")
        titles = node.get("title") or {}
        orig_title = str(titles.get("english") or titles.get("romaji") or "").strip()
        if not orig_title:
            return None
        title = orig_title

        cover = node.get("coverImage") or {}
        poster_url = cover.get("extraLarge") or cover.get("large")
        if not poster_url:
            return None
        banner_url = node.get("bannerImage")
        year = str((node.get("startDate") or {}).get("year") or "2015")[:4]
        avg_score = float(node.get("averageScore") or 82.0) / 10.0
        synopsis = clean_synopsis_text(node.get("description"), max_chars=300)

        resolved_trailer: Optional[str] = None
        if lang != "en" or not synopsis:
            loc_t, loc_syn, loc_tr = await resolve_multilingual_media_details(
                client, orig_title, lang=lang, domain_hint="anime", year=year
            )
            if loc_syn:
                synopsis = loc_syn
            if loc_t:
                title = loc_t
            if loc_tr:
                resolved_trailer = loc_tr

        trailer = node.get("trailer") or {}
        yt_url: Optional[str] = resolved_trailer
        if str(trailer.get("site") or "").lower() == "youtube" and trailer.get("id"):
            yt_url = f"https://www.youtube.com/watch?v={trailer['id']}"

        cand = MediaCandidate(
            id=f"anime_anilist_{al_id}",
            domain="anime",
            title=title,
            year=year,
            badge=g_meta["label"][:8].upper(),
            badge_color=g_meta["color"],
            subtitle=f"{g_meta['label']} • {year}"[:28],
            rating=f"{avg_score:.1f}",
            synopsis=synopsis or f"{title} ({year}) — {g_meta['label']} anime.",
            lang=lang,
            poster_url=str(poster_url),
            jacket_url=str(poster_url),
            marquee_url=str(banner_url or poster_url),
            backdrop_url=str(banner_url) if banner_url else None,
            youtube_url=yt_url,
            source_id=self.source_id,
            theme_palette=g_meta["palette"],
        )
        if banner_url:
            cand.add_unique_snapshot(str(banner_url))

        await enrich_with_animethemes_webm(
            client,
            cand,
            video_type=video_type,
            mal_id_override=str(mal_id) if mal_id else None,
            orig_title=orig_title,
        )
        if not cand.video_url and not cand.youtube_url:
            dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                client, f"{orig_title} anime opening trailer", max_snapshots=3
            )
            if dm_url:
                cand.youtube_url = dm_url
            for st in dm_stills:
                cand.add_unique_snapshot(st)
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

        base_variables: Dict[str, Any] = {
            "perPage": 25,
        }
        if g_meta.get("anilist_tag"):
            base_variables["tag"] = g_meta["anilist_tag"]
        else:
            base_variables["genre"] = g_meta["anilist"]

        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            dec = random.choice(valid_decades)
            base_yr = int(dec[:4])
            base_variables["startGreater"] = base_yr * 10000
            base_variables["startLesser"] = (base_yr + 9) * 10000 + 1231

        results: List[MediaCandidate] = []
        tried_pages: Set[int] = set()
        max_page = 15
        async with create_async_http_client(timeout=5.5) as client:
            for _ in range(max(2, count * 2)):
                if len(results) >= count:
                    break
                avail_pages = [p for p in range(1, max_page + 1) if p not in tried_pages]
                if not avail_pages:
                    break
                page = random.choice(avail_pages)
                tried_pages.add(page)
                variables = dict(base_variables)
                variables["page"] = page
                try:
                    resp = await client.post(
                        "https://graphql.anilist.co",
                        json={"query": _ANILIST_QUERY, "variables": variables},
                    )
                    if resp.status_code != 200:
                        continue
                    page_data = (resp.json().get("data") or {}).get("Page") or {}
                    last_page = int(((page_data.get("pageInfo") or {}).get("lastPage")) or 1)
                    max_page = max(1, min(15, last_page))
                    items = page_data.get("media") or []
                    if not items and page > 1:
                        fallback_pages = [p for p in range(1, max_page + 1) if p not in tried_pages] or [1]
                        page = random.choice(fallback_pages)
                        tried_pages.add(page)
                        variables["page"] = page
                        resp = await client.post(
                            "https://graphql.anilist.co",
                            json={"query": _ANILIST_QUERY, "variables": variables},
                        )
                        page_data = (resp.json().get("data") or {}).get("Page") or {}
                        items = page_data.get("media") or []

                    random.shuffle(items)
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        cid = f"anime_anilist_{item.get('id')}"
                        if cid in seen_ids:
                            continue
                        cand = await self._build_anilist_candidate(
                            client, item, g_meta, criteria.lang, criteria.video_type
                        )
                        if cand:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    logger.debug("[AniList] GraphQL discover error: %s", exc)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        if not query.strip():
            return None
        try:
            async with create_async_http_client(timeout=5.0) as client:
                resp = await client.post(
                    "https://graphql.anilist.co",
                    json={
                        "query": _ANILIST_QUERY,
                        "variables": {"page": 1, "perPage": 5, "search": query.strip()},
                    },
                )
                if resp.status_code == 200:
                    items = ((resp.json().get("data") or {}).get("Page") or {}).get("media") or []
                    for item in items:
                        if isinstance(item, dict):
                            cand = await self._build_anilist_candidate(
                                client, item, ANIME_GENRES["shonen"], lang, "opening"
                            )
                            if cand:
                                return cand
        except Exception:
            pass
        return None
