"""
Primary TV Series Scraper Adapter: TMDB API v3 (`api.themoviedb.org/3/discover/tv`).
Implements `IScraperSource` with uniform random page sampling and HD YouTube trailer/opening resolution.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
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
                for v in v_resp.json().get("results") or []:
                    site = str(v.get("site") or "")
                    key = str(v.get("key") or "").strip()
                    if site == "YouTube" and key:
                        cand.youtube_url = f"https://www.youtube.com/watch?v={key}"
                        if str(v.get("type", "")).lower() == "trailer":
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
                params=auth_params,
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
        }
        if genre_keys:
            base_params["with_genres"] = "|".join({str(SERIES_GENRES[g]["tmdb_id"]) for g in genre_keys})

        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        if valid_decades:
            start_d, end_d = DECADE_BOUNDS[random.choice(valid_decades)]
            base_params["first_air_date.gte"] = start_d
            base_params["first_air_date.lte"] = end_d

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=5.5) as client:
            for _ in range(max(2, count * 2)):
                if len(results) >= count:
                    break
                params = dict(base_params)
                params["page"] = str(random.randint(1, 20))
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
                    if int(params["page"]) > total_pages and total_pages >= 1:
                        params["page"] = str(random.randint(1, total_pages))
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
                    "https://api.themoviedb.org/3/search/tv",
                    params={**auth_params, "language": tmdb_lang, "query": query.strip()},
                    headers=auth_headers,
                )
                if resp.status_code == 200:
                    items = resp.json().get("results") or []
                    if items:
                        first = items[0]
                        tv_id = int(first["id"])
                        poster_p = first.get("poster_path")
                        poster_url = f"https://image.tmdb.org/t/p/w780{poster_p}" if poster_p else None
                        orig_title = str(first.get("original_name") or first.get("name") or query)
                        cand = MediaCandidate(
                            id=f"series_tmdb_{tv_id}",
                            domain="series",
                            title=str(first.get("name") or query),
                            year=str(first.get("first_air_date") or "2010")[:4],
                            badge="SERIES",
                            badge_color="#00ACC1",
                            subtitle="TV Series",
                            rating=f"{float(first.get('vote_average') or 8.2):.1f}",
                            synopsis=clean_synopsis_text(first.get("overview")),
                            lang=short_lang,
                            poster_url=poster_url,
                            jacket_url=poster_url,
                            source_id=self.source_id,
                        )
                        await self._enrich_tv_videos_and_images(
                            client, tv_id, api_key, short_lang, tmdb_lang, cand, orig_title=orig_title
                        )
                        return cand
        except Exception:
            pass
        return None
