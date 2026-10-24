"""
Primary Movies Scraper Adapter: TMDB API v3 (`api.themoviedb.org/3/discover/movie`).
Implements `IScraperSource` with uniform random page sampling and HD YouTube trailer resolution.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.persistence.server_config_store import server_config_store
from app.infrastructure.scrapers.catalog_metadata import DECADE_BOUNDS, MOVIE_GENRES, TMDB_LANG_MAP
from app.infrastructure.scrapers.http_utils import (
    build_tmdb_auth,
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    resolve_multilingual_media_details,
)

logger = logging.getLogger("dmd_showcase.scrapers.movies.tmdb")


class TmdbMovieAdapter(IScraperSource):
    """
    Primary `IScraperSource` for `movies` querying TMDB `/3/discover/movie` with uniform
    random pagination and language-agnostic YouTube HD trailer extraction.
    """

    source_id: str = "tmdb_movies"
    domain: DomainType = "movies"
    role: str = "primary"

    def _api_key(self) -> str:
        return str(server_config_store.get_all().get("tmdb_api_key") or "").strip()

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [{"id": gid, "label": meta["label"]} for gid, meta in MOVIE_GENRES.items()],
            "decades": [{"id": d, "label": d} for d in DECADE_BOUNDS],
        }

    async def _enrich_videos_and_images(
        self,
        client: Any,
        tmdb_id: int,
        api_key: str,
        short_lang: str,
        tmdb_lang: str,
        cand: MediaCandidate,
        orig_title: Optional[str] = None,
    ) -> None:
        auth_params, auth_headers = build_tmdb_auth(api_key)
        try:
            v_resp = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos",
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
                        if str(v.get("type") or "").lower() == "trailer"
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

        # Direct CDN trailer via Apple iTunes Search (try original title then localized title)
        if not cand.video_url:
            search_terms = [t for t in [orig_title, cand.title] if t]
            for term in dict.fromkeys(search_terms):
                try:
                    it_resp = await client.get(
                        "https://itunes.apple.com/search",
                        params={
                            "term": term,
                            "media": "movie",
                            "entity": "movie",
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
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/images",
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
                client, f"{q_title} {cand.year} trailer", max_snapshots=3
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

        genre_keys = [g.lower().strip() for g in criteria.genres if g.lower().strip() in MOVIE_GENRES]
        chosen_genre_key = random.choice(genre_keys) if genre_keys else "scifi"
        g_meta = MOVIE_GENRES[chosen_genre_key]

        auth_params, auth_headers = build_tmdb_auth(api_key)
        base_params: Dict[str, str] = {
            **auth_params,
            "language": tmdb_lang,
            "sort_by": "popularity.desc",
            "vote_count.gte": "100",
            "vote_average.gte": str(max(5.0, criteria.min_rating)),
        }
        if genre_keys:
            base_params["with_genres"] = str(g_meta["tmdb_id"])
            if g_meta.get("tmdb_keyword_id"):
                base_params["with_keywords"] = str(g_meta["tmdb_keyword_id"])

        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            start_d, end_d = DECADE_BOUNDS[random.choice(valid_decades)]
            base_params["primary_release_date.gte"] = start_d
            base_params["primary_release_date.lte"] = end_d

        results: List[MediaCandidate] = []
        max_page = 25
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
                        "https://api.themoviedb.org/3/discover/movie",
                        params=params,
                        headers=auth_headers,
                    )
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    total_pages = min(int(payload.get("total_pages") or 1), 500)
                    max_page = max(1, min(25, total_pages))
                    if int(params["page"]) > total_pages and total_pages >= 1:
                        fallback_page = random.randint(1, total_pages)
                        tried_pages.add(fallback_page)
                        params["page"] = str(fallback_page)
                        resp = await client.get(
                            "https://api.themoviedb.org/3/discover/movie",
                            params=params,
                            headers=auth_headers,
                        )
                        payload = resp.json() if resp.status_code == 200 else {}

                    items = payload.get("results") or []
                    random.shuffle(items)
                    for item in items:
                        tmdb_id = item.get("id")
                        item_id = f"movies_tmdb_{tmdb_id}"
                        if not tmdb_id or item_id in seen_ids:
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
                        year = str(item.get("release_date") or "1999")[:4]
                        vote = float(item.get("vote_average") or 7.5)
                        orig_title = str(item.get("original_title") or item.get("title") or "Movie").strip()
                        title = str(item.get("title") or orig_title).strip()
                        poster_url = f"https://image.tmdb.org/t/p/w780{poster_path}"
                        backdrop_url = (
                            f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None
                        )
                        synopsis = clean_synopsis_text(item.get("overview"))
                        if len(synopsis) < 30 and short_lang != "en":
                            loc_t, loc_syn, _ = await resolve_multilingual_media_details(
                                client, orig_title, lang=short_lang, domain_hint="film", year=year
                            )
                            if loc_syn:
                                synopsis = loc_syn
                            if loc_t:
                                title = loc_t

                        cand = MediaCandidate(
                            id=item_id,
                            domain="movies",
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
                        await self._enrich_videos_and_images(
                            client, int(tmdb_id), api_key, short_lang, tmdb_lang, cand, orig_title=orig_title
                        )
                        seen_ids.add(cand.id)
                        results.append(cand)
                        if len(results) >= count:
                            break
                except Exception as exc:
                    logger.debug("[TMDB Movies] Discover error: %s", exc)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        api_key = self._api_key()
        if not api_key or not query.strip():
            return None
        short_lang = (lang or "fr").split("-")[0].lower()
        tmdb_lang = TMDB_LANG_MAP.get(short_lang, "fr-FR")
        auth_params, auth_headers = build_tmdb_auth(api_key)
        try:
            async with create_async_http_client(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.themoviedb.org/3/search/movie",
                    params={**auth_params, "language": tmdb_lang, "query": query.strip()},
                    headers=auth_headers,
                )
                if resp.status_code == 200:
                    items = resp.json().get("results") or []
                    if items:
                        first = items[0]
                        tmdb_id = int(first["id"])
                        poster_p = first.get("poster_path")
                        poster_url = f"https://image.tmdb.org/t/p/w780{poster_p}" if poster_p else None
                        orig_title = str(first.get("original_title") or first.get("title") or query)
                        cand = MediaCandidate(
                            id=f"movies_tmdb_{tmdb_id}",
                            domain="movies",
                            title=str(first.get("title") or query),
                            year=str(first.get("release_date") or "2000")[:4],
                            badge="MOVIE",
                            badge_color="#E53935",
                            subtitle="Cinema",
                            rating=f"{float(first.get('vote_average') or 8.0):.1f}",
                            synopsis=clean_synopsis_text(first.get("overview")),
                            lang=short_lang,
                            poster_url=poster_url,
                            jacket_url=poster_url,
                            source_id=self.source_id,
                        )
                        await self._enrich_videos_and_images(
                            client, tmdb_id, api_key, short_lang, tmdb_lang, cand, orig_title=orig_title
                        )
                        return cand
        except Exception:
            pass
        return None
