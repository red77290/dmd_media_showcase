"""
Fallback Anime Scraper Adapter: AniList GraphQL API (`graphql.anilist.co`) + AnimeThemes.moe.

Keyless public GraphQL anime adapter implementing `IScraperSource`:
- Uniform random pagination across AniList's `Page(page: $page, perPage: 25)` query.
- Provides `coverImage.extraLarge` HD jackets, `bannerImage` wide marquees, synopses,
  YouTube trailers (`trailer { id site }`), and direct `.webm` OP/ED streams via `api.animethemes.moe`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import resolve_canonical_genre
from app.infrastructure.scrapers.anime.jikan_animethemes_adapter import enrich_with_animethemes_webm
from app.infrastructure.scrapers.catalog_metadata import ANIME_GENRES, DECADE_BOUNDS
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    fetch_wikipedia_localized_synopsis,
    resolve_multilingual_media_details,
)
from app.infrastructure.scrapers.translation_service import ensure_candidate_localized

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
        native
      }
      genres
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
    display_name: str = "AniList"
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
        enrich_heavy: bool = True,
    ) -> Optional[MediaCandidate]:
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        al_id = node.get("id")
        if not al_id:
            return None
        mal_id = node.get("idMal")
        titles = node.get("title") or {}
        orig_title = str(titles.get("english") or titles.get("romaji") or "").strip()
        if not orig_title:
            return None
        native_title = str(titles.get("native") or "").strip()
        title = native_title if (short_lang == "ja" and native_title) else orig_title

        cover = node.get("coverImage") or {}
        poster_url = cover.get("extraLarge") or cover.get("large")
        if not poster_url:
            return None
        banner_url = node.get("bannerImage")
        year = str((node.get("startDate") or {}).get("year") or "2015")[:4]
        avg_score = float(node.get("averageScore") or 82.0) / 10.0
        synopsis = clean_synopsis_text(node.get("description"), max_chars=300)
        has_loc_syn = bool(short_lang == "en" and synopsis)

        resolved_trailer: Optional[str] = None
        if enrich_heavy and (short_lang != "en" or not synopsis):
            loc_t, loc_syn, loc_tr = await resolve_multilingual_media_details(
                client, orig_title, lang=short_lang, domain_hint="anime", year=year
            )
            if loc_syn:
                synopsis = loc_syn
                has_loc_syn = True
            if loc_t and short_lang != "ja":
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
            lang=short_lang,
            poster_url=str(poster_url),
            jacket_url=str(poster_url),
            marquee_url=str(banner_url or poster_url),
            backdrop_url=str(banner_url) if banner_url else None,
            youtube_url=yt_url,
            source_id=self.source_id,
            theme_palette=g_meta["palette"],
            extra_meta={
                "anilist_id": str(al_id),
                "original_title": orig_title,
                "synopsis_localized": "1" if has_loc_syn else "0",
            },
        )
        if banner_url:
            cand.add_unique_snapshot(str(banner_url))

        if not enrich_heavy:
            await ensure_candidate_localized(cand, short_lang, None)
            return cand

        await ensure_candidate_localized(
            cand,
            short_lang,
            client,
            raw_source_lang="" if has_loc_syn else "en",
        )

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
        genre_keys: List[str] = []
        for g in criteria.genres or []:
            if not g or g.strip().lower() == "all":
                continue
            cg = resolve_canonical_genre("anime", g)
            gk = cg if cg in ANIME_GENRES else g.strip().lower()
            if gk in ANIME_GENRES and gk not in genre_keys:
                genre_keys.append(gk)

        explicit_g_meta: Optional[Dict[str, Any]] = None
        if genre_keys:
            explicit_g_meta = ANIME_GENRES[random.choice(genre_keys)]

        base_variables: Dict[str, Any] = {
            "perPage": 25,
        }
        if explicit_g_meta is not None:
            if explicit_g_meta.get("anilist_tag"):
                base_variables["tag"] = explicit_g_meta["anilist_tag"]
            else:
                base_variables["genre"] = explicit_g_meta["anilist"]

        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            dec = random.choice(valid_decades)
            base_yr = int(dec[:4])
            base_variables["startGreater"] = base_yr * 10000
            base_variables["startLesser"] = (base_yr + 9) * 10000 + 1231

        results: List[MediaCandidate] = []
        tried_pages: Set[int] = set()
        page_cap = 15 if explicit_g_meta is not None else 40
        max_page = page_cap
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
                    max_page = max(1, min(page_cap, last_page))
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
                        node_genres = {str(x).lower() for x in (item.get("genres") or []) if x}
                        item_g_meta = (
                            explicit_g_meta
                            if explicit_g_meta is not None
                            else self._infer_anilist_genre_meta(node_genres, genre_keys)
                        )
                        cand = await self._build_anilist_candidate(
                            client, item, item_g_meta, criteria.lang, criteria.video_type
                        )
                        if cand:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    logger.debug("[AniList] GraphQL discover error: %s", exc)

        return results

    @staticmethod
    def _infer_anilist_genre_meta(
        node_genres: Set[str],
        preferred_keys: List[str],
    ) -> Dict[str, Any]:
        if preferred_keys:
            return ANIME_GENRES[preferred_keys[0]]
        for gk, meta in ANIME_GENRES.items():
            if not meta.get("anilist_tag") and str(meta.get("anilist") or "").lower() in node_genres:
                return meta
        return ANIME_GENRES["shonen"]

    async def search_candidates(
        self,
        query: str,
        criteria: FilterCriteria,
        limit: int = 8,
    ) -> List[MediaCandidate]:
        q_clean = (query or "").strip()
        if not q_clean or limit <= 0:
            return []

        target_limit = max(1, int(limit))
        short_lang = (criteria.lang or "fr").split("-")[0].split("_")[0].lower()
        video_type = (criteria.video_type or "opening").strip().lower()

        valid_genres: List[str] = []
        for g in criteria.genres or []:
            if not g or g.strip().lower() == "all":
                continue
            cg = resolve_canonical_genre("anime", g)
            gk = cg if cg in ANIME_GENRES else g.strip().lower()
            if gk in ANIME_GENRES and gk not in valid_genres:
                valid_genres.append(gk)
        allowed_anilist_genres = {str(ANIME_GENRES[gk]["anilist"]).lower() for gk in valid_genres}

        valid_decades = [
            d.strip().lower()
            for d in (criteria.decades or [])
            if d and d.strip().lower() in DECADE_BOUNDS
        ]

        variables: Dict[str, Any] = {
            "page": 1,
            "perPage": min(25, max(10, target_limit * 3)),
            "search": q_clean,
        }
        if len(valid_genres) == 1:
            only_meta = ANIME_GENRES[valid_genres[0]]
            if only_meta.get("anilist_tag"):
                variables["tag"] = only_meta["anilist_tag"]
            else:
                variables["genre"] = only_meta["anilist"]
        if len(valid_decades) == 1:
            base_yr = int(valid_decades[0][:4])
            variables["startGreater"] = base_yr * 10000
            variables["startLesser"] = (base_yr + 9) * 10000 + 1231

        try:
            async with create_async_http_client(timeout=5.5) as client:
                resp = await client.post(
                    "https://graphql.anilist.co",
                    json={"query": _ANILIST_QUERY, "variables": variables},
                )
                if resp.status_code != 200:
                    return []
                items = ((resp.json().get("data") or {}).get("Page") or {}).get("media") or []
                matched_specs: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
                seen_ids: Set[str] = set()

                for node in items:
                    if len(matched_specs) >= target_limit:
                        break
                    if not isinstance(node, dict) or not node.get("id"):
                        continue
                    cid = f"anime_anilist_{node.get('id')}"
                    if cid in seen_ids:
                        continue

                    node_genres = {str(x).lower() for x in (node.get("genres") or []) if x}
                    if len(valid_genres) > 1 and node_genres and not (node_genres & allowed_anilist_genres):
                        continue

                    if valid_decades:
                        yr_raw = str((node.get("startDate") or {}).get("year") or "")[:4]
                        if not yr_raw.isdigit():
                            continue
                        yr_val = int(yr_raw)
                        if not any(
                            int(DECADE_BOUNDS[d][0][:4]) <= yr_val <= int(DECADE_BOUNDS[d][1][:4])
                            for d in valid_decades
                        ):
                            continue

                    seen_ids.add(cid)
                    g_meta = self._infer_anilist_genre_meta(node_genres, valid_genres)
                    matched_specs.append((node, g_meta))

                if not matched_specs:
                    return []

                sem = asyncio.Semaphore(4)

                async def _build_idx(
                    idx: int,
                    spec: tuple[Dict[str, Any], Dict[str, Any]],
                ) -> Optional[MediaCandidate]:
                    nd, gm = spec
                    async with sem:
                        return await self._build_anilist_candidate(
                            client,
                            nd,
                            gm,
                            short_lang,
                            video_type,
                            enrich_heavy=(idx < min(target_limit, 4)),
                        )

                built = await asyncio.gather(
                    *(_build_idx(i, sp) for i, sp in enumerate(matched_specs)),
                    return_exceptions=True,
                )
                return [c for c in built if isinstance(c, MediaCandidate)][:target_limit]
        except Exception as exc:
            logger.debug("[AniList] Search error for '%s': %s", q_clean, exc)
        return []

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        cands = await self.search_candidates(
            query=query,
            criteria=FilterCriteria(
                domain="anime",
                genres=[],
                decades=[],
                video_type="opening",
                lang=lang,
            ),
            limit=1,
        )
        return cands[0] if cands else None
