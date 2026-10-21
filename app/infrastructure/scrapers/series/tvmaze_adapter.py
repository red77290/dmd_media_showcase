"""
Fallback TV Series Scraper Adapter: TVMaze Public API (`api.tvmaze.com/shows`).

Keyless public TV catalog adapter implementing `IScraperSource`:
- Uniform random pagination across 280+ pages of `/shows?page={random_page}` (~70,000 TV series).
- Extracts `original` HD posters, wide `banner` marquees, `background` stills, and localized synopses.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.catalog_metadata import SERIES_GENRES
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_wikipedia_localized_synopsis,
)

logger = logging.getLogger("dmd_showcase.scrapers.series.tvmaze")

_SERIES_DECADES: Dict[str, Tuple[int, int]] = {
    "1970s": (1970, 1979),
    "1980s": (1980, 1989),
    "1990s": (1990, 1999),
    "2000s": (2000, 2009),
    "2010s": (2010, 2019),
    "2020s": (2020, 2029),
}


class TvMazeAdapter(IScraperSource):
    """
    Keyless Fallback `IScraperSource` for `series` querying TVMaze's paginated `/shows` catalog.
    """

    source_id: str = "tvmaze"
    domain: DomainType = "series"
    role: str = "fallback"

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [
                {"id": gid, "label": meta["label"]}
                for gid, meta in SERIES_GENRES.items()
                if gid != "science-fiction"
            ],
            "decades": [{"id": d, "label": d} for d in _SERIES_DECADES],
        }

    async def _enrich_show(
        self,
        client: Any,
        show: Dict[str, Any],
        lang: str,
        genre_meta: Dict[str, Any],
    ) -> MediaCandidate:
        show_id = show.get("id")
        title = str(show.get("name") or "Unknown Series").strip()
        premiered = str(show.get("premiered") or "2010")
        year = premiered[:4] if len(premiered) >= 4 else "2010"
        rating_val = (show.get("rating") or {}).get("average") or 8.1
        genres = show.get("genres") or [genre_meta["label"]]
        primary_genre = str(genres[0]) if genres else genre_meta["label"]
        network = ((show.get("network") or show.get("webChannel") or {}).get("name") or "TV").strip()

        img = show.get("image") or {}
        poster_url = img.get("original") or img.get("medium")

        summary = clean_synopsis_text(show.get("summary"), max_chars=300)
        if lang != "en" or not summary:
            wiki_syn, wiki_thumb = await fetch_wikipedia_localized_synopsis(
                client, title, lang=lang, domain_hint="TV series"
            )
            if wiki_syn:
                summary = wiki_syn
            if wiki_thumb and not poster_url:
                poster_url = wiki_thumb

        cand = MediaCandidate(
            id=f"series_tvmaze_{show_id}",
            domain="series",
            title=title,
            year=year,
            badge=primary_genre[:8].upper(),
            badge_color=genre_meta["color"],
            subtitle=f"{primary_genre} • {network}"[:28],
            rating=f"{float(rating_val):.1f}",
            synopsis=summary or f"{title} ({year}) — {primary_genre} series on {network}.",
            lang=lang,
            poster_url=poster_url,
            jacket_url=poster_url,
            source_id=self.source_id,
            theme_palette=genre_meta["palette"],
            extra_meta={"tvmaze_id": str(show_id)},
        )

        if show_id:
            try:
                img_resp = await client.get(f"https://api.tvmaze.com/shows/{show_id}/images", timeout=3.5)
                if img_resp.status_code == 200:
                    for eimg in img_resp.json() or []:
                        itype = str(eimg.get("type") or "")
                        url = ((eimg.get("resolutions") or {}).get("original") or {}).get("url")
                        if not url:
                            continue
                        if itype == "banner" and not cand.marquee_url:
                            cand.marquee_url = str(url)
                        elif itype == "background":
                            cand.add_unique_snapshot(str(url))
                        elif itype == "poster" and not cand.jacket_url:
                            cand.jacket_url = str(url)
            except Exception:
                pass

        # Attach YouTube trailer/opening search query so `yt-dlp` can resolve clean 720p/1080p video
        cand.youtube_url = f"ytsearch1:{title} {year} series trailer opening"
        return cand

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        req_genres = [
            g.lower().strip() for g in criteria.genres if g.lower().strip() in SERIES_GENRES
        ]
        chosen_genre_key = random.choice(req_genres) if req_genres else "scifi"
        g_meta = SERIES_GENRES.get(chosen_genre_key, SERIES_GENRES["scifi"])
        target_tvmaze_genres = {SERIES_GENRES[g]["tvmaze"].lower() for g in req_genres}

        req_decades = [d for d in criteria.decades if d in _SERIES_DECADES]
        results: List[MediaCandidate] = []

        async with create_async_http_client(timeout=5.5) as client:
            pages = random.sample(range(0, 180), k=4)
            matching_shows: List[Dict[str, Any]] = []
            fallback_shows: List[Dict[str, Any]] = []

            for page in pages:
                try:
                    resp = await client.get("https://api.tvmaze.com/shows", params={"page": str(page)})
                    if resp.status_code != 200:
                        continue
                    for sh in resp.json() or []:
                        if not isinstance(sh, dict):
                            continue
                        sid = f"series_tvmaze_{sh.get('id')}"
                        if sid in seen_ids:
                            continue
                        if not (sh.get("image") or {}).get("original") and not (sh.get("image") or {}).get("medium"):
                            continue
                        avg_rate = float((sh.get("rating") or {}).get("average") or 7.5)
                        if avg_rate < max(5.5, criteria.min_rating - 1.0):
                            continue

                        sh_genres = {str(x).lower() for x in (sh.get("genres") or [])}
                        prem = str(sh.get("premiered") or "2010")
                        yr = int(prem[:4]) if len(prem) >= 4 and prem[:4].isdigit() else 2010

                        genre_ok = not target_tvmaze_genres or bool(sh_genres.intersection(target_tvmaze_genres))
                        decade_ok = not req_decades or any(
                            _SERIES_DECADES[d][0] <= yr <= _SERIES_DECADES[d][1] for d in req_decades
                        )
                        if genre_ok and decade_ok:
                            matching_shows.append(sh)
                        elif genre_ok:
                            fallback_shows.append(sh)
                    if len(matching_shows) >= count * 3:
                        break
                except Exception as exc:
                    logger.debug("[TVMaze] Page %d fetch error: %s", page, exc)

            pool = matching_shows if len(matching_shows) >= count else (matching_shows + fallback_shows)
            random.shuffle(pool)
            for sh in pool[:count]:
                cand = await self._enrich_show(client, sh, criteria.lang, g_meta)
                seen_ids.add(cand.id)
                results.append(cand)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        if not query.strip():
            return None
        try:
            async with create_async_http_client(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.tvmaze.com/singlesearch/shows",
                    params={"q": query.strip()},
                )
                if resp.status_code == 200 and resp.json():
                    return await self._enrich_show(client, resp.json(), lang, SERIES_GENRES["scifi"])
        except Exception:
            pass
        return None
