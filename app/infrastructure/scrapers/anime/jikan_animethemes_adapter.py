"""
Primary Anime Scraper Adapter: Jikan v4 (MyAnimeList) + AnimeThemes.moe (`JikanAnimeThemesAdapter`).

Keyless public anime catalog adapter implementing `IScraperSource`:
- Uniform random pagination across `api.jikan.moe/v4/anime` (`last_visible_page`).
- Enriches candidates with direct creditless `.webm` OP/ED streams from `api.animethemes.moe`
  or clean HD YouTube trailers (`trailer.url`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import resolve_canonical_genre
from app.infrastructure.scrapers.catalog_metadata import ANIME_GENRES, DECADE_BOUNDS
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    fetch_wikipedia_localized_synopsis,
    resolve_multilingual_media_details,
)
from app.infrastructure.scrapers.translation_service import ensure_candidate_localized

logger = logging.getLogger("dmd_showcase.scrapers.anime.jikan")
_YT_EMBED_RE = re.compile(r"/embed/([A-Za-z0-9_-]{6,})")


async def enrich_with_animethemes_webm(
    client: Any,
    cand: MediaCandidate,
    video_type: str = "opening",
    mal_id_override: Optional[str] = None,
    orig_title: Optional[str] = None,
) -> None:
    """Attach direct creditless `.webm` OP/ED stream and high-res cover from `api.animethemes.moe`."""
    target_prefix = "OP" if video_type == "opening" else ("ED" if video_type == "ending" else "")
    try:
        # 1. Search by MyAnimeList external ID if present for 100% exact match
        anime_list: List[Dict[str, Any]] = []
        mal_id = mal_id_override or (cand.id.split("_")[-1] if "anime_jikan_" in cand.id else "")
        if mal_id and str(mal_id).isdigit():
            try:
                id_resp = await client.get(
                    "https://api.animethemes.moe/anime",
                    params={
                        "filter[has]": "resources",
                        "filter[site]": "MyAnimeList",
                        "filter[external_id]": str(mal_id),
                        "include": "animethemes.animethemeentries.videos,images",
                        "page[size]": "2",
                    },
                    timeout=3.0,
                )
                if id_resp.status_code == 200:
                    anime_list = id_resp.json().get("anime") or []
            except Exception:
                pass

        if not anime_list:
            lookup_name = (orig_title or cand.title).strip()
            resp = await client.get(
                "https://api.animethemes.moe/anime",
                params={
                    "filter[name-like]": f"%{lookup_name}%",
                    "include": "animethemes.animethemeentries.videos,images",
                    "page[size]": "3",
                },
                timeout=3.0,
            )
            if resp.status_code == 200:
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

        # When the user explicitly requests `video_type="trailer"` and a YouTube trailer is already present,
        # do not overwrite it with an OP/ED .webm from AnimeThemes.
        if video_type == "trailer" and cand.youtube_url:
            return

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
    Includes a lightweight circuit-breaker backoff so upstream `api.jikan.moe` outages or
    timeouts fail over in `0ms` to `AniListAdapter` instead of burning `12-24s` per slot.
    """

    source_id: str = "jikan"
    display_name: str = "Jikan + AnimeThemes"
    domain: DomainType = "anime"
    role: str = "primary"

    def is_in_cooldown(self) -> bool:
        return self._is_circuit_open()

    _FAILURE_THRESHOLD: int = 2
    _BACKOFF_SECONDS: float = 180.0
    _consecutive_failures: int = 0
    _backoff_until_monotonic: float = 0.0

    @classmethod
    def reset_circuit_breaker(cls) -> None:
        cls._consecutive_failures = 0
        cls._backoff_until_monotonic = 0.0

    @classmethod
    def _is_circuit_open(cls) -> bool:
        return time.monotonic() < cls._backoff_until_monotonic

    @classmethod
    def _record_jikan_success(cls) -> None:
        cls._consecutive_failures = 0
        cls._backoff_until_monotonic = 0.0

    @classmethod
    def _record_jikan_failure(cls, reason: str) -> None:
        cls._consecutive_failures += 1
        if cls._consecutive_failures >= cls._FAILURE_THRESHOLD:
            cls._backoff_until_monotonic = time.monotonic() + cls._BACKOFF_SECONDS
            logger.warning(
                "[Jikan] Circuit breaker opened for %.0fs after %d consecutive failures (%s); failing over to AniList",
                cls._BACKOFF_SECONDS,
                cls._consecutive_failures,
                reason,
            )

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
        enrich_heavy: bool = True,
    ) -> Optional[MediaCandidate]:
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        mal_id = item.get("mal_id")
        if not mal_id:
            return None
        orig_title = str(item.get("title_english") or item.get("title") or "").strip()
        if not orig_title:
            return None
        ja_title = str(item.get("title_japanese") or "").strip()
        title = ja_title if (short_lang == "ja" and ja_title) else orig_title

        images = (item.get("images") or {}).get("jpg") or {}
        poster_url = images.get("large_image_url") or images.get("image_url")
        if not poster_url:
            return None

        year = str(item.get("year") or ((item.get("aired") or {}).get("from") or "2012"))[:4]
        score = float(item.get("score") or 8.2)
        synopsis = clean_synopsis_text(item.get("synopsis"), max_chars=300)
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

        trailer = item.get("trailer") or {}
        yt_id = trailer.get("youtube_id")
        if not yt_id and trailer.get("embed_url"):
            m = _YT_EMBED_RE.search(str(trailer.get("embed_url")))
            if m:
                yt_id = m.group(1)
        yt_url = (
            f"https://www.youtube.com/watch?v={yt_id}"
            if yt_id
            else (trailer.get("url") or resolved_trailer)
        )
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
            lang=short_lang,
            poster_url=str(poster_url),
            jacket_url=str(poster_url),
            marquee_url=str(max_img or poster_url),
            backdrop_url=str(max_img) if max_img else None,
            youtube_url=str(yt_url) if yt_url else None,
            source_id=self.source_id,
            theme_palette=g_meta["palette"],
            extra_meta={
                "mal_id": str(mal_id),
                "original_title": orig_title,
                "synopsis_localized": "1" if has_loc_syn else "0",
            },
        )
        if max_img:
            cand.add_unique_snapshot(str(max_img))

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
            mal_id_override=str(mal_id),
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
        if self._is_circuit_open():
            logger.debug("[Jikan] Circuit breaker open, skipping Jikan discovery")
            return []

        genre_keys: List[str] = []
        for g in criteria.genres or []:
            if not g or g.strip().lower() == "all":
                continue
            cg = resolve_canonical_genre("anime", g)
            gk = cg if cg in ANIME_GENRES else g.strip().lower()
            if gk in ANIME_GENRES and gk not in genre_keys:
                genre_keys.append(gk)

        explicit_g_meta: Optional[Dict[str, Any]] = None
        req_mal_id: Optional[int] = None
        if genre_keys:
            explicit_g_meta = ANIME_GENRES[random.choice(genre_keys)]
            req_mal_id = int(explicit_g_meta["mal_id"])

        base_params: Dict[str, str] = {
            "order_by": "members",
            "sort": "desc",
            "limit": "25",
            "sfw": "true",
            "min_score": str(max(6.0, criteria.min_rating)),
        }
        if req_mal_id is not None:
            base_params["genres"] = str(req_mal_id)
        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            start_d, end_d = DECADE_BOUNDS[random.choice(valid_decades)]
            base_params["start_date"] = start_d
            base_params["end_date"] = end_d

        results: List[MediaCandidate] = []
        tried_pages: Set[int] = set()
        page_cap = 15 if req_mal_id is not None else 30
        max_page = page_cap
        async with create_async_http_client(timeout=3.5) as client:
            for _ in range(max(2, count * 2)):
                if len(results) >= count or self._is_circuit_open():
                    break
                avail_pages = [p for p in range(1, max_page + 1) if p not in tried_pages]
                if not avail_pages:
                    break
                page = random.choice(avail_pages)
                tried_pages.add(page)
                params = dict(base_params)
                params["page"] = str(page)
                try:
                    resp = await client.get("https://api.jikan.moe/v4/anime", params=params)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        self._record_jikan_failure(f"HTTP {resp.status_code}")
                        continue
                    if resp.status_code != 200:
                        continue
                    self._record_jikan_success()
                    payload = resp.json()
                    last_page = int(((payload.get("pagination") or {}).get("last_visible_page")) or 1)
                    max_page = max(1, min(page_cap, last_page))
                    if page > last_page and last_page >= 1:
                        fallback_pages = [p for p in range(1, max_page + 1) if p not in tried_pages] or [1]
                        page = random.choice(fallback_pages)
                        tried_pages.add(page)
                        params["page"] = str(page)
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
                        item_genre_objs = (
                            (item.get("genres") or [])
                            + (item.get("themes") or [])
                            + (item.get("demographics") or [])
                        )
                        item_mal_ids: Set[int] = set()
                        if item_genre_objs:
                            item_mal_ids = {
                                int(g.get("mal_id"))
                                for g in item_genre_objs
                                if isinstance(g, dict) and str(g.get("mal_id") or "").isdigit()
                            }
                            if req_mal_id is not None and item_mal_ids and req_mal_id not in item_mal_ids:
                                continue
                        item_g_meta = (
                            explicit_g_meta
                            if explicit_g_meta is not None
                            else self._infer_jikan_genre_meta(item_mal_ids, genre_keys)
                        )
                        cand = await self._build_jikan_candidate(
                            client, item, item_g_meta, criteria.lang, criteria.video_type
                        )
                        if cand:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    self._record_jikan_failure(type(exc).__name__)
                    logger.debug("[Jikan] Discover error: %s", exc)

        return results

    @staticmethod
    def _infer_jikan_genre_meta(
        item_mal_ids: Set[int],
        preferred_keys: List[str],
    ) -> Dict[str, Any]:
        if preferred_keys:
            return ANIME_GENRES[preferred_keys[0]]
        for gk, meta in ANIME_GENRES.items():
            if int(meta["mal_id"]) in item_mal_ids and not meta.get("anilist_tag"):
                return meta
        return ANIME_GENRES["shonen"]

    async def search_candidates(
        self,
        query: str,
        criteria: FilterCriteria,
        limit: int = 8,
    ) -> List[MediaCandidate]:
        q_clean = (query or "").strip()
        if not q_clean or limit <= 0 or self._is_circuit_open():
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
        allowed_mal_ids = {int(ANIME_GENRES[gk]["mal_id"]) for gk in valid_genres}

        valid_decades = [
            d.strip().lower()
            for d in (criteria.decades or [])
            if d and d.strip().lower() in DECADE_BOUNDS
        ]

        params: Dict[str, str] = {
            "q": q_clean,
            "limit": str(min(25, max(10, target_limit * 3))),
            "sfw": "true",
        }
        if len(valid_genres) == 1:
            params["genres"] = str(ANIME_GENRES[valid_genres[0]]["mal_id"])
        if len(valid_decades) == 1:
            start_d, end_d = DECADE_BOUNDS[valid_decades[0]]
            params["start_date"] = start_d
            params["end_date"] = end_d

        try:
            async with create_async_http_client(timeout=3.5) as client:
                resp = await client.get("https://api.jikan.moe/v4/anime", params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    self._record_jikan_failure(f"HTTP {resp.status_code}")
                    return []
                if resp.status_code != 200:
                    return []
                self._record_jikan_success()
                raw_items = resp.json().get("data") or []
                matched_specs: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
                seen_ids: Set[str] = set()

                for item in raw_items:
                    if len(matched_specs) >= target_limit:
                        break
                    if not isinstance(item, dict) or not item.get("mal_id"):
                        continue
                    cid = f"anime_jikan_{item.get('mal_id')}"
                    if cid in seen_ids:
                        continue

                    item_genre_objs = (
                        (item.get("genres") or [])
                        + (item.get("themes") or [])
                        + (item.get("demographics") or [])
                    )
                    item_mal_ids = {
                        int(g.get("mal_id"))
                        for g in item_genre_objs
                        if isinstance(g, dict) and str(g.get("mal_id") or "").isdigit()
                    }
                    if allowed_mal_ids and item_mal_ids and not (item_mal_ids & allowed_mal_ids):
                        continue

                    if valid_decades:
                        yr_str = str(
                            item.get("year") or ((item.get("aired") or {}).get("from") or "")
                        )[:4]
                        if not yr_str.isdigit():
                            continue
                        yr_val = int(yr_str)
                        if not any(
                            int(DECADE_BOUNDS[d][0][:4]) <= yr_val <= int(DECADE_BOUNDS[d][1][:4])
                            for d in valid_decades
                        ):
                            continue

                    seen_ids.add(cid)
                    g_meta = self._infer_jikan_genre_meta(item_mal_ids, valid_genres)
                    matched_specs.append((item, g_meta))

                if not matched_specs:
                    return []

                sem = asyncio.Semaphore(4)

                async def _build_idx(
                    idx: int,
                    spec: tuple[Dict[str, Any], Dict[str, Any]],
                ) -> Optional[MediaCandidate]:
                    it, gm = spec
                    async with sem:
                        return await self._build_jikan_candidate(
                            client,
                            it,
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
            self._record_jikan_failure(type(exc).__name__)
            logger.debug("[Jikan] Search error for '%s': %s", q_clean, exc)
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
