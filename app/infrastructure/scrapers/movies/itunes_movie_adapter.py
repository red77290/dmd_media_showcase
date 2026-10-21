"""
Fallback Movies Scraper Adapter: Apple iTunes Movie Trailers API (`itunes.apple.com/search`).

Keyless public movie & trailer catalog implementing `IScraperSource`:
- Uniform random pagination across genre keywords, release years, and `offset`.
- Upgrades `artworkUrl100` to `1000x1000bb.jpg` clean HD theatrical posters.
- Provides localized `longDescription` synopses and direct `.m4v`/`.mp4` HD trailer streams (`previewUrl`).
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.catalog_metadata import DECADE_BOUNDS, MOVIE_GENRES
from app.infrastructure.scrapers.http_utils import clean_synopsis_text, create_async_http_client

logger = logging.getLogger("dmd_showcase.scrapers.movies.itunes")

_ITUNES_COUNTRY_BY_LANG: Dict[str, str] = {
    "fr": "FR",
    "en": "US",
    "es": "ES",
    "de": "DE",
    "it": "IT",
    "pt": "BR",
    "ja": "JP",
}


class ItunesMovieAdapter(IScraperSource):
    """
    Keyless Fallback `IScraperSource` for `movies` querying Apple iTunes Movie Search API.
    """

    source_id: str = "itunes_movies"
    domain: DomainType = "movies"
    role: str = "fallback"

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "genres": [{"id": gid, "label": meta["label"]} for gid, meta in MOVIE_GENRES.items()],
            "decades": [{"id": d, "label": d} for d in DECADE_BOUNDS],
        }

    def _parse_itunes_track(
        self, track: Dict[str, Any], g_meta: Dict[str, Any], lang: str
    ) -> Optional[MediaCandidate]:
        track_id = track.get("trackId")
        title = str(track.get("trackName") or "").strip()
        if not track_id or not title:
            return None

        art_100 = str(track.get("artworkUrl100") or "")
        if not art_100.startswith("http"):
            return None
        # Upgrade Apple CDN artwork to clean 1000x1000 HD poster & wide banner
        hd_poster = re.sub(r"\d+x\d+bb\.jpg$", "1000x1000bb.jpg", art_100)
        wide_art = re.sub(r"\d+x\d+bb\.jpg$", "1200x630bb.jpg", art_100)

        release_date = str(track.get("releaseDate") or "2005")
        year = release_date[:4] if len(release_date) >= 4 else "2005"
        genre_name = str(track.get("primaryGenreName") or g_meta["label"]).strip()
        director = str(track.get("artistName") or "Cinema").strip()
        synopsis = clean_synopsis_text(
            track.get("longDescription") or track.get("shortDescription"), max_chars=300
        )
        preview_url = str(track.get("previewUrl") or "").strip()

        cand = MediaCandidate(
            id=f"movies_itunes_{track_id}",
            domain="movies",
            title=title,
            year=year,
            badge=genre_name[:8].upper(),
            badge_color=g_meta["color"],
            subtitle=f"{genre_name} • {director}"[:28],
            rating="8.2",
            synopsis=synopsis or f"{title} ({year}) — {genre_name} film directed by {director}.",
            lang=lang,
            poster_url=hd_poster,
            jacket_url=hd_poster,
            marquee_url=wide_art,
            backdrop_url=wide_art,
            video_url=preview_url if preview_url.startswith("http") else None,
            source_id=self.source_id,
            theme_palette=g_meta["palette"],
        )
        cand.add_unique_snapshot(wide_art)
        return cand

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        short_lang = (criteria.lang or "fr").split("-")[0].lower()
        country = _ITUNES_COUNTRY_BY_LANG.get(short_lang, "US")

        genre_keys = [g.lower().strip() for g in criteria.genres if g.lower().strip() in MOVIE_GENRES]
        chosen_genre_key = random.choice(genre_keys) if genre_keys else random.choice(list(MOVIE_GENRES.keys()))
        g_meta = MOVIE_GENRES[chosen_genre_key]

        valid_decades = [d for d in criteria.decades if d in DECADE_BOUNDS]
        decade_year = ""
        if valid_decades:
            dec = random.choice(valid_decades)
            base_yr = int(dec[:4])
            decade_year = str(random.randint(base_yr, base_yr + 9))

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=5.5) as client:
            for _ in range(max(4, count * 2)):
                if len(results) >= count:
                    break
                term = f"{g_meta['query']} {decade_year}".strip()
                offset = random.randint(0, 80)
                try:
                    resp = await client.get(
                        "https://itunes.apple.com/search",
                        params={
                            "term": term,
                            "media": "movie",
                            "entity": "movie",
                            "country": country,
                            "limit": "50",
                            "offset": str(offset),
                        },
                    )
                    rows = (resp.json().get("results") or []) if resp.status_code == 200 else []
                    if not rows:
                        resp = await client.get(
                            "https://itunes.apple.com/search",
                            params={
                                "term": g_meta["query"],
                                "media": "movie",
                                "entity": "movie",
                                "country": country,
                                "limit": "50",
                            },
                        )
                        rows = (resp.json().get("results") or []) if resp.status_code == 200 else []

                    random.shuffle(rows)
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        cand = self._parse_itunes_track(row, g_meta, short_lang)
                        if cand and cand.id not in seen_ids:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    logger.debug("[iTunes Movies] Discovery error: %s", exc)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        if not query.strip():
            return None
        short_lang = (lang or "fr").split("-")[0].lower()
        country = _ITUNES_COUNTRY_BY_LANG.get(short_lang, "US")
        try:
            async with create_async_http_client(timeout=5.0) as client:
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": query.strip(),
                        "media": "movie",
                        "entity": "movie",
                        "country": country,
                        "limit": "5",
                    },
                )
                if resp.status_code == 200:
                    for row in resp.json().get("results") or []:
                        if isinstance(row, dict):
                            cand = self._parse_itunes_track(row, MOVIE_GENRES["scifi"], short_lang)
                            if cand:
                                return cand
        except Exception:
            pass
        return None
