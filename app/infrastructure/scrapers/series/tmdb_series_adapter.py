"""
Primary TV Series Scraper Adapter: TMDB API v3 (`api.themoviedb.org/3/discover/tv`).
Implements `IScraperSource` with uniform random page sampling and HD YouTube trailer/opening resolution.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import SERIES_GENRE_CATALOG, localize_label, resolve_canonical_genre
from app.infrastructure.persistence.server_config_store import server_config_store
from app.infrastructure.scrapers.catalog_metadata import DECADE_BOUNDS, SERIES_GENRES, TMDB_LANG_MAP
from app.infrastructure.scrapers.http_utils import (
    build_tmdb_auth,
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    resolve_multilingual_media_details,
)

logger = logging.getLogger("dmd_showcase.scrapers.series.tmdb")


class TmdbSeriesAdapter(IScraperSource):
    """
    Primary `IScraperSource` for `series` querying TMDB `/3/discover/tv` with uniform
    random pagination and language-agnostic YouTube HD trailer/opening extraction.
    """

    source_id: str = "tmdb_series"
    display_name: str = "TMDB (Series)"
    domain: DomainType = "series"
    role: str = "primary"

    def _api_key(self) -> str:
        return str(server_config_store.get_all().get("tmdb_api_key") or "").strip()

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [
                {"id": gid, "label": meta["label"]}
                for gid, meta in SERIES_GENRES.items()
                if gid != "science-fiction"
            ],
            "decades": [
                {"id": d, "label": d} for d in DECADE_BOUNDS if d != "1960s"
            ],
        }

    async def _enrich_tv_videos_and_images(
        self,
        client: Any,
        tv_id: int,
        api_key: str,
        short_lang: str,
        tmdb_lang: str,
        cand: MediaCandidate,
        orig_title: Optional[str] = None,
    ) -> None:
        auth_params, auth_headers = build_tmdb_auth(api_key)
        try:
            v_resp = await client.get(
                f"https://api.themoviedb.org/3/tv/{tv_id}/videos",
                params={
                    **auth_params,
                    "language": tmdb_lang,
                    "include_video_language": f"{short_lang},en,ja,ko,es,de,it,fr,null",
                },
                headers=auth_headers,
            )
            if v_resp.status_code == 200:
                vids = list(v_resp.json().get("results") or [])
                vids.sort(
                    key=lambda v: (
                        0
                        if str(v.get("type") or "").lower() in ("trailer", "opening credits")
                        else (1 if str(v.get("type") or "").lower() == "teaser" else 2),
                        0 if v.get("official") else 1,
                    )
                )
                for v in vids:
                    site = str(v.get("site") or "")
                    key = str(v.get("key") or "").strip()
                    if site == "YouTube" and key:
                        cand.youtube_url = f"https://www.youtube.com/watch?v={key}"
                        break
                    elif site == "Vimeo" and key and not cand.youtube_url:
                        cand.youtube_url = f"https://vimeo.com/{key}"
        except Exception:
            pass

        # Resilient TV Episode previewUrl (.m4v direct CDN stream) via iTunes
        if not cand.video_url:
            search_terms = [t for t in [orig_title, cand.title] if t]
            for term in dict.fromkeys(search_terms):
                try:
                    it_resp = await client.get(
                        "https://itunes.apple.com/search",
                        params={
                            "term": term,
                            "media": "tvShow",
                            "entity": "tvEpisode",
                            "limit": "3",
                        },
                        timeout=3.0,
                    )
                    if it_resp.status_code == 200:
                        for row in it_resp.json().get("results") or []:
                            prev = str(row.get("previewUrl") or "").strip()
                            if prev.startswith("http"):
                                cand.video_url = prev
                                break
                    if cand.video_url:
                        break
                except Exception:
                    pass

        try:
            i_resp = await client.get(
                f"https://api.themoviedb.org/3/tv/{tv_id}/images",
                params={**auth_params, "include_image_language": f"{short_lang},en,null"},
                headers=auth_headers,
            )
            if i_resp.status_code == 200:
                img_data = i_resp.json()
                for logo in (img_data.get("logos") or [])[:1]:
                    if logo.get("file_path"):
                        cand.marquee_url = f"https://image.tmdb.org/t/p/w780{logo['file_path']}"
                for bd in (img_data.get("backdrops") or [])[:5]:
                    if bd.get("file_path"):
                        cand.add_unique_snapshot(f"https://image.tmdb.org/t/p/w1280{bd['file_path']}")
        except Exception:
            pass

        if not cand.video_url and not cand.youtube_url:
            q_title = orig_title or cand.title
            dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                client, f"{q_title} series trailer", max_snapshots=3
            )
            if dm_url:
                cand.youtube_url = dm_url
            for st in dm_stills:
                cand.add_unique_snapshot(st)

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        api_key = self._api_key()
        if not api_key:
            return []

        short_lang = (criteria.lang or "fr").split("-")[0].lower()
        tmdb_lang = TMDB_LANG_MAP.get(short_lang, "fr-FR")

        genre_keys = [g.lower().strip() for g in criteria.genres if g.lower().strip() in SERIES_GENRES]
        chosen_genre_key = random.choice(genre_keys) if genre_keys else "scifi"
        g_meta = SERIES_GENRES[chosen_genre_key]

        auth_params, auth_headers = build_tmdb_auth(api_key)
        base_params: Dict[str, str] = {
            **auth_params,
            "language": tmdb_lang,
            "sort_by": "popularity.desc",
            "vote_count.gte": "60",
            "vote_average.gte": str(max(5.0, criteria.min_rating)),
            "without_genres": "10763,10767",
        }
        if genre_keys:
            base_params["with_genres"] = str(g_meta["tmdb_id"])
            if g_meta.get("tmdb_keyword_id"):
                base_params["with_keywords"] = str(g_meta["tmdb_keyword_id"])

        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            start_d, end_d = DECADE_BOUNDS[random.choice(valid_decades)]
            base_params["first_air_date.gte"] = start_d
            base_params["first_air_date.lte"] = end_d

        results: List[MediaCandidate] = []
        max_page = 20
        tried_pages: Set[int] = set()
        async with create_async_http_client(timeout=5.5) as client:
            for _ in range(max(2, count * 2)):
                if len(results) >= count:
                    break
                params = dict(base_params)
                avail_pages = [p for p in range(1, max(2, max_page + 1)) if p not in tried_pages] or [1]
                chosen_page = random.choice(avail_pages)
                tried_pages.add(chosen_page)
                params["page"] = str(chosen_page)
                try:
                    resp = await client.get(
                        "https://api.themoviedb.org/3/discover/tv",
                        params=params,
                        headers=auth_headers,
                    )
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    total_pages = min(int(payload.get("total_pages") or 1), 500)
                    max_page = max(1, min(20, total_pages))
                    if int(params["page"]) > total_pages and total_pages >= 1:
                        fallback_page = random.randint(1, total_pages)
                        tried_pages.add(fallback_page)
                        params["page"] = str(fallback_page)
                        resp = await client.get(
                            "https://api.themoviedb.org/3/discover/tv",
                            params=params,
                            headers=auth_headers,
                        )
                        payload = resp.json() if resp.status_code == 200 else {}

                    items = payload.get("results") or []
                    random.shuffle(items)
                    for item in items:
                        tv_id = item.get("id")
                        item_id = f"series_tmdb_{tv_id}"
                        if not tv_id or item_id in seen_ids:
                            continue
                        item_gids = item.get("genre_ids")
                        if genre_keys and isinstance(item_gids, list) and item_gids:
                            if int(g_meta["tmdb_id"]) not in item_gids:
                                seen_ids.add(item_id)
                                continue
                        poster_path = item.get("poster_path")
                        backdrop_path = item.get("backdrop_path")
                        if not poster_path:
                            continue
                        year = str(item.get("first_air_date") or "2010")[:4]
                        vote = float(item.get("vote_average") or 7.8)
                        orig_title = str(item.get("original_name") or item.get("name") or "Series").strip()
                        title = str(item.get("name") or orig_title).strip()
                        poster_url = f"https://image.tmdb.org/t/p/w780{poster_path}"
                        backdrop_url = (
                            f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None
                        )
                        synopsis = clean_synopsis_text(item.get("overview"))
                        if len(synopsis) < 30 and short_lang != "en":
                            loc_t, loc_syn, _ = await resolve_multilingual_media_details(
                                client, orig_title, lang=short_lang, domain_hint="series", year=year
                            )
                            if loc_syn:
                                synopsis = loc_syn
                            if loc_t:
                                title = loc_t

                        cand = MediaCandidate(
                            id=item_id,
                            domain="series",
                            title=title,
                            year=year,
                            badge=g_meta["label"][:8].upper(),
                            badge_color=g_meta["color"],
                            subtitle=f"{g_meta['label']} • {year}"[:28],
                            rating=f"{vote:.1f}",
                            synopsis=synopsis or f"{title} ({year}).",
                            lang=short_lang,
                            poster_url=poster_url,
                            jacket_url=poster_url,
                            marquee_url=backdrop_url,
                            backdrop_url=backdrop_url,
                            source_id=self.source_id,
                            theme_palette=g_meta["palette"],
                        )
                        if backdrop_url:
                            cand.add_unique_snapshot(backdrop_url)
                        await self._enrich_tv_videos_and_images(
                            client, int(tv_id), api_key, short_lang, tmdb_lang, cand, orig_title=orig_title
                        )
                        seen_ids.add(cand.id)
                        results.append(cand)
                        if len(results) >= count:
                            break
                except Exception as exc:
                    logger.debug("[TMDB Series] Discover error: %s", exc)

        return results

    @staticmethod
    def _resolve_series_genre_meta(
        item_gids: List[int],
        preferred_keys: List[str],
    ) -> Tuple[str, Dict[str, Any]]:
        if preferred_keys:
            gk = preferred_keys[0]
            return gk, SERIES_GENRES[gk]
        for gid in item_gids:
            for gk, meta in SERIES_GENRES.items():
                if gk == "science-fiction":
                    continue
                if int(meta["tmdb_id"]) == int(gid) and not meta.get("tmdb_keyword_id"):
                    return gk, meta
        return "scifi", {"label": "TV Series", "color": "#00ACC1", "palette": (0, 172, 193)}

    async def search_candidates(
        self,
        query: str,
        criteria: FilterCriteria,
        limit: int = 8,
    ) -> List[MediaCandidate]:
        api_key = self._api_key()
        q_clean = (query or "").strip()
        if not api_key or not q_clean or limit <= 0:
            return []

        target_limit = max(1, int(limit))
        short_lang = (criteria.lang or "fr").split("-")[0].split("_")[0].lower()
        tmdb_lang = TMDB_LANG_MAP.get(short_lang, "fr-FR")
        auth_params, auth_headers = build_tmdb_auth(api_key)

        valid_genres: List[str] = []
        for g in criteria.genres or []:
            if not g or g.strip().lower() == "all":
                continue
            cg = resolve_canonical_genre("series", g)
            gk = cg if cg in SERIES_GENRES else g.strip().lower()
            if gk in SERIES_GENRES and gk not in valid_genres:
                valid_genres.append(gk)
        allowed_gids = {int(SERIES_GENRES[gk]["tmdb_id"]) for gk in valid_genres}

        valid_decades = [
            d.strip().lower()
            for d in (criteria.decades or [])
            if d and d.strip().lower() in DECADE_BOUNDS
        ]

        candidates: List[MediaCandidate] = []
        orig_titles: Dict[str, str] = {}
        tv_ids: Dict[str, int] = {}
        seen_ids: Set[str] = set()

        try:
            async with create_async_http_client(timeout=5.5) as client:
                max_pages = 2 if (allowed_gids or valid_decades) else 1
                for page in range(1, max_pages + 1):
                    if len(candidates) >= target_limit:
                        break
                    resp = await client.get(
                        "https://api.themoviedb.org/3/search/tv",
                        params={
                            **auth_params,
                            "language": tmdb_lang,
                            "query": q_clean,
                            "include_adult": "false",
                            "page": str(page),
                        },
                        headers=auth_headers,
                    )
                    if resp.status_code != 200:
                        break
                    payload = resp.json()
                    items = payload.get("results") or []
                    if not items:
                        break

                    for idx, item in enumerate(items):
                        if len(candidates) >= target_limit:
                            break
                        if not isinstance(item, dict) or not item.get("id"):
                            continue
                        tv_id = int(item["id"])
                        cid = f"series_tmdb_{tv_id}"
                        if cid in seen_ids:
                            continue

                        item_gids = [
                            int(x) for x in (item.get("genre_ids") or []) if str(x).isdigit()
                        ]
                        if allowed_gids and not (set(item_gids) & allowed_gids):
                            continue

                        year = str(item.get("first_air_date") or "")[:4]
                        if valid_decades:
                            if not year.isdigit():
                                continue
                            yr_val = int(year)
                            if not any(
                                int(DECADE_BOUNDS[d][0][:4]) <= yr_val <= int(DECADE_BOUNDS[d][1][:4])
                                for d in valid_decades
                            ):
                                continue
                        year = year if (len(year) == 4 and year.isdigit()) else "2010"

                        poster_p = item.get("poster_path")
                        backdrop_p = item.get("backdrop_path")
                        if not poster_p and not backdrop_p and not (target_limit == 1 and idx == 0 and not candidates):
                            continue

                        poster_url = (
                            f"https://image.tmdb.org/t/p/w780{poster_p}"
                            if poster_p
                            else (f"https://image.tmdb.org/t/p/w780{backdrop_p}" if backdrop_p else None)
                        )
                        backdrop_url = (
                            f"https://image.tmdb.org/t/p/w1280{backdrop_p}" if backdrop_p else None
                        )
                        gk, g_meta = self._resolve_series_genre_meta(item_gids, valid_genres)
                        g_cat = SERIES_GENRE_CATALOG.get(gk)
                        genre_label = (
                            localize_label(g_cat["labels"], short_lang, g_meta["label"])
                            if g_cat
                            else g_meta["label"]
                        )

                        orig_title = str(item.get("original_name") or item.get("name") or q_clean).strip()
                        title = str(item.get("name") or orig_title).strip()
                        vote = float(item.get("vote_average") or 8.0)
                        synopsis = clean_synopsis_text(item.get("overview"))

                        cand = MediaCandidate(
                            id=cid,
                            domain="series",
                            title=title,
                            year=year,
                            badge=str(g_meta["label"])[:8].upper() if item_gids or valid_genres else "SERIES",
                            badge_color=str(g_meta["color"]),
                            subtitle=f"{genre_label} • {year}"[:28],
                            rating=f"{vote:.1f}",
                            synopsis=synopsis or f"{title} ({year}).",
                            lang=short_lang,
                            poster_url=poster_url,
                            jacket_url=poster_url,
                            marquee_url=backdrop_url,
                            backdrop_url=backdrop_url,
                            source_id=self.source_id,
                            theme_palette=tuple(g_meta["palette"]),
                        )
                        if backdrop_url:
                            cand.add_unique_snapshot(backdrop_url)

                        seen_ids.add(cid)
                        orig_titles[cid] = orig_title
                        tv_ids[cid] = tv_id
                        candidates.append(cand)

                    if page >= int(payload.get("total_pages") or 1):
                        break

                if candidates:
                    sem = asyncio.Semaphore(4)
                    top_to_enrich = candidates[: min(len(candidates), 4)]

                    async def _enrich_one(c: MediaCandidate) -> None:
                        async with sem:
                            ot = orig_titles.get(c.id, c.title)
                            if len(c.synopsis) < 25 and short_lang != "en":
                                loc_t, loc_syn, _ = await resolve_multilingual_media_details(
                                    client, ot, lang=short_lang, domain_hint="series", year=c.year
                                )
                                if loc_syn:
                                    c.synopsis = loc_syn
                                if loc_t:
                                    c.title = loc_t
                            await self._enrich_tv_videos_and_images(
                                client,
                                tv_ids[c.id],
                                api_key,
                                short_lang,
                                tmdb_lang,
                                c,
                                orig_title=ot,
                            )

                    await asyncio.gather(*(_enrich_one(c) for c in top_to_enrich), return_exceptions=True)
        except Exception as exc:
            logger.debug("[TMDB Series] Search error for '%s': %s", q_clean, exc)

        return candidates[:target_limit]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        cands = await self.search_candidates(
            query=query,
            criteria=FilterCriteria(domain="series", genres=[], decades=[], lang=lang),
            limit=1,
        )
        return cands[0] if cands else None
