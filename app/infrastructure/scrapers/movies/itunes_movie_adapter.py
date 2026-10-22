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
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_imdb_suggestion_items,
    fetch_wikipedia_localized_synopsis,
)

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

    async def _lookup_itunes_movie(
        self,
        client: Any,
        query_term: str,
        country: str,
        g_meta: Dict[str, Any],
        short_lang: str,
        seen_ids: Set[str],
        decade_bounds: Optional[ tuple[int, int] ] = None,
    ) -> Optional[MediaCandidate]:
        countries = [country] if country == "US" else [country, "US"]
        for ctry in countries:
            try:
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": query_term,
                        "media": "movie",
                        "entity": "movie",
                        "country": ctry,
                        "limit": "15",
                    },
                )
                if resp.status_code != 200:
                    continue
                rows = resp.json().get("results") or []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    cand = self._parse_itunes_track(row, g_meta, short_lang)
                    if not cand or cand.id in seen_ids:
                        continue
                    if decade_bounds and cand.year.isdigit():
                        yr = int(cand.year)
                        if not (decade_bounds[0] <= yr <= decade_bounds[1]):
                            continue
                    if short_lang != "en" and (ctry == "US" or len(cand.synopsis) < 50):
                        wiki_syn, _ = await fetch_wikipedia_localized_synopsis(
                            client, cand.title, lang=short_lang, domain_hint="film"
                        )
                        if wiki_syn:
                            cand.synopsis = wiki_syn
                    return cand
            except Exception as exc:
                logger.debug("[iTunes Movies] Lookup error for '%s' (%s): %s", query_term, ctry, exc)
        return None

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
        chosen_decade = random.choice(valid_decades) if valid_decades else random.choice(list(DECADE_BOUNDS.keys()))
        base_yr = int(chosen_decade[:4])
        decade_bounds: Optional[tuple[int, int]] = (base_yr, base_yr + 9) if valid_decades else None

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=6.0) as client:
            # 1. Discover real films for the requested decade + genre via Wikipedia MediaWiki API,
            #    then enrich via Apple iTunes Movie Trailers (`previewUrl` .m4v) + IMDb CDN + Wikipedia synopsis.
            search_q = f"{base_yr}s {g_meta['query']} film incategory:Films"
            wiki_items: List[Dict[str, Any]] = []
            try:
                sroffset = random.randint(0, 35)
                w_resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": search_q,
                        "srlimit": str(max(15, count * 6)),
                        "sroffset": str(sroffset),
                        "format": "json",
                    },
                )
                if w_resp.status_code == 200:
                    wiki_items = (w_resp.json().get("query") or {}).get("search") or []
                if not wiki_items:
                    w_resp2 = await client.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={
                            "action": "query",
                            "list": "search",
                            "srsearch": f"{g_meta['query']} film incategory:Films",
                            "srlimit": "20",
                            "format": "json",
                        },
                    )
                    if w_resp2.status_code == 200:
                        wiki_items = (w_resp2.json().get("query") or {}).get("search") or []
            except Exception as exc:
                logger.debug("[iTunes Movies] Wikipedia film discovery error: %s", exc)

            random.shuffle(wiki_items)
            for entry in wiki_items:
                if len(results) >= count:
                    break
                raw_title = str(entry.get("title") or "").strip()
                if not raw_title or raw_title.startswith(("List of", "Category:", "History of", "Outline of")):
                    continue
                clean_title = re.sub(r"\s*\([^)]*film[^)]*\)\s*$", "", raw_title, flags=re.I).strip()
                if not clean_title or len(clean_title) < 2:
                    continue

                # Try Apple iTunes Movie Search for this exact film title (gives HD poster + direct .m4v trailer)
                itunes_cand = await self._lookup_itunes_movie(
                    client, clean_title, country, g_meta, short_lang, seen_ids, decade_bounds=decade_bounds
                )
                if itunes_cand is not None:
                    seen_ids.add(itunes_cand.id)
                    results.append(itunes_cand)
                    continue

                # Fallback to IMDb Suggestion CDN + Wikipedia localized synopsis if not in iTunes Store
                imdb_hits = await fetch_imdb_suggestion_items(client, clean_title, qid_filter={"movie", "feature"})
                if not imdb_hits:
                    continue
                imdb_info = imdb_hits[0]
                imdb_id = str(imdb_info.get("id") or "")
                cand_id = f"movies_imdb_{imdb_id}"
                if not imdb_id or cand_id in seen_ids:
                    continue
                year_val = str(imdb_info.get("y") or base_yr)[:4]
                if decade_bounds and year_val.isdigit():
                    yr_i = int(year_val)
                    if not (decade_bounds[0] <= yr_i <= decade_bounds[1]):
                        continue
                poster_url = str((imdb_info.get("i") or {}).get("imageUrl") or "")
                if not poster_url:
                    continue
                imdb_title = str(imdb_info.get("l") or clean_title).strip()
                stars = str(imdb_info.get("s") or "").strip()
                wiki_syn, wiki_thumb = await fetch_wikipedia_localized_synopsis(
                    client, raw_title, lang=short_lang, domain_hint="film"
                )
                if not wiki_syn:
                    wiki_syn = clean_synopsis_text(entry.get("snippet"), max_chars=280)
                if not wiki_syn:
                    wiki_syn = f"{imdb_title} ({year_val}) — {g_meta['label']}. {stars}".strip()

                cand = MediaCandidate(
                    id=cand_id,
                    domain="movies",
                    title=imdb_title,
                    year=year_val,
                    badge=g_meta["label"][:8].upper(),
                    badge_color=g_meta["color"],
                    subtitle=f"{g_meta['label']} • {year_val}"[:28],
                    rating="8.2",
                    synopsis=wiki_syn,
                    lang=short_lang,
                    poster_url=poster_url,
                    jacket_url=poster_url,
                    marquee_url=wiki_thumb or poster_url,
                    backdrop_url=wiki_thumb or poster_url,
                    video_url=None,
                    source_id=self.source_id,
                    theme_palette=g_meta["palette"],
                )
                if wiki_thumb:
                    cand.add_unique_snapshot(wiki_thumb)
                seen_ids.add(cand.id)
                results.append(cand)

            # 2. Direct iTunes Store keyword search fallback (if Wikipedia returned < count)
            if len(results) < count:
                decade_year = str(random.randint(base_yr, base_yr + 9))
                search_terms = [
                    f"{g_meta['query']} {decade_year}".strip(),
                    g_meta["query"],
                    g_meta["label"],
                ]
                for term in search_terms:
                    if len(results) >= count:
                        break
                    cand = await self._lookup_itunes_movie(
                        client, term, country, g_meta, short_lang, seen_ids, decade_bounds=None
                    )
                    if cand is not None:
                        seen_ids.add(cand.id)
                        results.append(cand)

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
