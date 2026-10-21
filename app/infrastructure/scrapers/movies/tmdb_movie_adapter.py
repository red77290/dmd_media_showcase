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
from app.infrastructure.scrapers.http_utils import clean_synopsis_text, create_async_http_client

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
        self, client: Any, tmdb_id: int, api_key: str, short_lang: str, tmdb_lang: str, cand: MediaCandidate
    ) -> None:
        try:
            v_resp = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos",
                params={
                    "api_key": api_key,
                    "language": tmdb_lang,
                    "include_video_language": f"{short_lang},en,ja,ko,es,de,it,null",
                },
            )
            if v_resp.status_code == 200:
                vids = v_resp.json().get("results") or []
                for v in vids:
                    if v.get("site") == "YouTube" and v.get("key"):
                        cand.youtube_url = f"https://www.youtube.com/watch?v={v['key']}"
                        break
        except Exception:
            pass

        try:
            i_resp = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/images",
                params={"api_key": api_key},
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

        base_params: Dict[str, str] = {
            "api_key": api_key,
            "language": tmdb_lang,
            "sort_by": "popularity.desc",
            "vote_count.gte": "100",
            "vote_average.gte": str(max(5.0, criteria.min_rating)),
        }
        if genre_keys:
            base_params["with_genres"] = "|".join({str(MOVIE_GENRES[g]["tmdb_id"]) for g in genre_keys})

        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            start_d, end_d = DECADE_BOUNDS[random.choice(valid_decades)]
            base_params["primary_release_date.gte"] = start_d
            base_params["primary_release_date.lte"] = end_d

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=5.5) as client:
            for _ in range(max(2, count * 2)):
                if len(results) >= count:
                    break
                params = dict(base_params)
                params["page"] = str(random.randint(1, 25))
                try:
                    resp = await client.get("https://api.themoviedb.org/3/discover/movie", params=params)
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    total_pages = min(int(payload.get("total_pages") or 1), 500)
                    if int(params["page"]) > total_pages and total_pages >= 1:
                        params["page"] = str(random.randint(1, total_pages))
                        resp = await client.get("https://api.themoviedb.org/3/discover/movie", params=params)
                        payload = resp.json() if resp.status_code == 200 else {}

                    items = payload.get("results") or []
                    random.shuffle(items)
                    for item in items:
                        tmdb_id = item.get("id")
                        item_id = f"movies_tmdb_{tmdb_id}"
                        if not tmdb_id or item_id in seen_ids:
                            continue
                        poster_path = item.get("poster_path")
                        backdrop_path = item.get("backdrop_path")
                        if not poster_path:
                            continue
                        year = str(item.get("release_date") or "1999")[:4]
                        vote = float(item.get("vote_average") or 7.5)
                        title = str(item.get("title") or item.get("original_title") or "Movie").strip()
                        poster_url = f"https://image.tmdb.org/t/p/w780{poster_path}"
                        backdrop_url = (
                            f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None
                        )

                        cand = MediaCandidate(
                            id=item_id,
                            domain="movies",
                            title=title,
                            year=year,
                            badge=g_meta["label"][:8].upper(),
                            badge_color=g_meta["color"],
                            subtitle=f"{g_meta['label']} • {year}"[:28],
                            rating=f"{vote:.1f}",
                            synopsis=clean_synopsis_text(item.get("overview")) or f"{title} ({year}).",
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
                            client, int(tmdb_id), api_key, short_lang, tmdb_lang, cand
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
        try:
            async with create_async_http_client(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.themoviedb.org/3/search/movie",
                    params={"api_key": api_key, "language": tmdb_lang, "query": query.strip()},
                )
                if resp.status_code == 200:
                    items = resp.json().get("results") or []
                    if items:
                        first = items[0]
                        tmdb_id = int(first["id"])
                        poster_p = first.get("poster_path")
                        poster_url = f"https://image.tmdb.org/t/p/w780{poster_p}" if poster_p else None
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
                            client, tmdb_id, api_key, short_lang, tmdb_lang, cand
                        )
                        return cand
        except Exception:
            pass
        return None
