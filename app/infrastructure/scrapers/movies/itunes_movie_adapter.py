"""
Fallback Movies Scraper Adapter: Apple iTunes Movie Trailers API (`itunes.apple.com/search`).

Keyless public movie & trailer catalog implementing `IScraperSource`:
- Uniform random pagination across genre keywords, release years, and `offset`.
- Upgrades `artworkUrl100` to `1000x1000bb.jpg` clean HD theatrical posters.
- Provides localized `longDescription` synopses and direct `.m4v`/`.mp4` HD trailer streams (`previewUrl`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import resolve_canonical_genre
from app.infrastructure.scrapers.catalog_metadata import DECADE_BOUNDS, MOVIE_GENRES
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    fetch_imdb_suggestion_items,
    fetch_wikipedia_localized_synopsis,
    resolve_multilingual_media_details,
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

    @staticmethod
    def _is_itunes_genre_compatible(primary_genre: str, g_meta: Dict[str, Any]) -> bool:
        pg = (primary_genre or "").strip().lower()
        if not pg:
            return True
        label = str(g_meta.get("label") or "").lower()
        query = str(g_meta.get("query") or "").lower()
        if label in pg or pg in label or query in pg or pg in query:
            return True
        # Map Apple iTunes primaryGenreName categories to subgenres
        compat_map = {
            "sci-fi": ("sci-fi", "science fiction", "action", "fantasy", "thriller"),
            "cyberpunk": ("sci-fi", "science fiction", "action", "thriller", "animation"),
            "kaiju / monster": ("sci-fi", "science fiction", "action", "horror", "adventure"),
            "martial arts": ("action", "adventure", "drama", "foreign"),
            "superhero": ("action", "adventure", "sci-fi", "fantasy", "animation"),
            "film noir": ("crime", "thriller", "mystery", "drama", "classics"),
            "mystery": ("mystery", "thriller", "crime", "drama"),
            "family": ("kids", "family", "animation", "comedy", "adventure", "fantasy"),
            "fantasy": ("fantasy", "sci-fi", "adventure", "kids", "family", "animation"),
            "animation": ("animation", "kids", "family", "anime"),
            "war": ("war", "action", "drama", "history"),
            "history": ("history", "drama", "documentary", "biography", "war"),
        }
        allowed = compat_map.get(label, ())
        return any(tok in pg for tok in allowed) if allowed else True

    async def _lookup_itunes_movie(
        self,
        client: Any,
        query_term: str,
        country: str,
        g_meta: Dict[str, Any],
        short_lang: str,
        seen_ids: Set[str],
        decade_bounds: Optional[ tuple[int, int] ] = None,
        require_genre_match: bool = False,
    ) -> Optional[MediaCandidate]:
        countries = [country] if country == "US" else [country, "US"]
        q_low = query_term.strip().lower()
        for ctry in countries:
            try:
                resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": query_term,
                        "media": "movie",
                        "entity": "movie",
                        "country": ctry,
                        "limit": "25",
                    },
                )
                if resp.status_code != 200:
                    continue
                rows = [r for r in (resp.json().get("results") or []) if isinstance(r, dict)]
                random.shuffle(rows)
                rows.sort(
                    key=lambda r: 0
                    if str(r.get("trackName") or "").strip().lower() == q_low
                    else 1
                )
                for row in rows:
                    if require_genre_match and not self._is_itunes_genre_compatible(
                        str(row.get("primaryGenreName") or ""), g_meta
                    ):
                        continue
                    cand = self._parse_itunes_track(row, g_meta, short_lang)
                    if not cand or cand.id in seen_ids:
                        continue
                    if decade_bounds and cand.year.isdigit():
                        yr = int(cand.year)
                        if not (decade_bounds[0] <= yr <= decade_bounds[1]):
                            continue
                    if short_lang != "en" and (ctry == "US" or len(cand.synopsis) < 50):
                        loc_t, loc_syn, loc_tr = await resolve_multilingual_media_details(
                            client, cand.title, lang=short_lang, domain_hint="film", year=cand.year
                        )
                        if loc_syn:
                            cand.synopsis = loc_syn
                        if loc_t:
                            cand.title = loc_t
                        if loc_tr and not cand.youtube_url:
                            cand.youtube_url = loc_tr
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
            #    then enrich via Apple iTunes Movie Trailers (`previewUrl` .m4v) + IMDb CDN + Dailymotion trailer.
            rand_year = random.randint(base_yr, base_yr + 9)
            search_q = f'"{rand_year}" "{g_meta["query"]} film" "directed by"'
            wiki_items: List[Dict[str, Any]] = []
            try:
                sroffset = random.randint(0, 25)
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
                            "srsearch": f'"{g_meta["query"]} film" "directed by"',
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
                    client,
                    clean_title,
                    country,
                    g_meta,
                    short_lang,
                    seen_ids,
                    decade_bounds=decade_bounds,
                    require_genre_match=bool(genre_keys),
                )
                if itunes_cand is not None:
                    if not itunes_cand.video_url and not itunes_cand.youtube_url:
                        dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                            client, f"{itunes_cand.title} {itunes_cand.year} trailer", max_snapshots=4
                        )
                        if dm_url:
                            itunes_cand.youtube_url = dm_url
                        for st in dm_stills:
                            itunes_cand.add_unique_snapshot(st)
                    seen_ids.add(itunes_cand.id)
                    results.append(itunes_cand)
                    continue

                # Fallback to IMDb Suggestion CDN + Dailymotion HD Trailer + Wikipedia localized synopsis
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

                dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                    client, f"{imdb_title} {year_val} trailer", max_snapshots=4
                )
                backdrop_img = (dm_stills[0] if dm_stills else None) or wiki_thumb or poster_url

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
                    marquee_url=backdrop_img,
                    backdrop_url=backdrop_img,
                    video_url=None,
                    youtube_url=dm_url,
                    source_id=self.source_id,
                    theme_palette=g_meta["palette"],
                )
                for st in dm_stills:
                    cand.add_unique_snapshot(st)
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
                        client,
                        term,
                        country,
                        g_meta,
                        short_lang,
                        seen_ids,
                        decade_bounds=decade_bounds,
                        require_genre_match=bool(genre_keys),
                    )
                    if cand is None and decade_bounds is not None:
                        cand = await self._lookup_itunes_movie(
                            client,
                            term,
                            country,
                            g_meta,
                            short_lang,
                            seen_ids,
                            decade_bounds=None,
                            require_genre_match=bool(genre_keys),
                        )
                    if cand is not None:
                        seen_ids.add(cand.id)
                        results.append(cand)

        return results

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
        country = _ITUNES_COUNTRY_BY_LANG.get(short_lang, "US")

        valid_genres: List[str] = []
        for g in criteria.genres or []:
            if not g or g.strip().lower() == "all":
                continue
            cg = resolve_canonical_genre("movies", g)
            gk = cg if cg in MOVIE_GENRES else g.strip().lower()
            if gk in MOVIE_GENRES and gk not in valid_genres:
                valid_genres.append(gk)
        g_meta = MOVIE_GENRES[valid_genres[0]] if valid_genres else MOVIE_GENRES["scifi"]

        valid_decades = [
            d.strip().lower()
            for d in (criteria.decades or [])
            if d and d.strip().lower() in DECADE_BOUNDS
        ]

        candidates: List[MediaCandidate] = []
        cand_storefront: Dict[str, str] = {}
        seen_ids: Set[str] = set()
        seen_title_yr: Set[Tuple[str, str]] = set()
        countries = [country] if country == "US" else [country, "US"]

        try:
            async with create_async_http_client(timeout=5.5) as client:
                for ctry in countries:
                    if len(candidates) >= target_limit:
                        break
                    resp = await client.get(
                        "https://itunes.apple.com/search",
                        params={
                            "term": q_clean,
                            "media": "movie",
                            "entity": "movie",
                            "country": ctry,
                            "limit": str(min(50, max(15, target_limit * 4))),
                        },
                    )
                    if resp.status_code != 200:
                        continue
                    rows = [r for r in (resp.json().get("results") or []) if isinstance(r, dict)]
                    q_low = q_clean.lower()
                    rows.sort(
                        key=lambda r: 0
                        if str(r.get("trackName") or "").strip().lower() == q_low
                        else 1
                    )
                    for row in rows:
                        if len(candidates) >= target_limit:
                            break
                        if valid_genres and not any(
                            self._is_itunes_genre_compatible(
                                str(row.get("primaryGenreName") or ""), MOVIE_GENRES[gk]
                            )
                            for gk in valid_genres
                        ):
                            continue
                        cand = self._parse_itunes_track(row, g_meta, short_lang)
                        if not cand or cand.id in seen_ids:
                            continue
                        if valid_decades:
                            if not cand.year.isdigit():
                                continue
                            yr_val = int(cand.year)
                            if not any(
                                int(DECADE_BOUNDS[d][0][:4]) <= yr_val <= int(DECADE_BOUNDS[d][1][:4])
                                for d in valid_decades
                            ):
                                continue
                        ty_key = (re.sub(r"[^a-z0-9]+", "", cand.title.lower()), cand.year)
                        if ty_key[0] and ty_key in seen_title_yr:
                            continue
                        seen_ids.add(cand.id)
                        if ty_key[0]:
                            seen_title_yr.add(ty_key)
                        cand_storefront[cand.id] = ctry
                        candidates.append(cand)

                if candidates:
                    sem = asyncio.Semaphore(4)
                    top_to_enrich = candidates[: min(len(candidates), 4)]

                    async def _enrich_itunes(c: MediaCandidate) -> None:
                        async with sem:
                            ctry = cand_storefront.get(c.id, country)
                            orig_t = c.title
                            if short_lang != "en" and (ctry == "US" or len(c.synopsis) < 45):
                                loc_t, loc_syn, loc_tr = await resolve_multilingual_media_details(
                                    client, orig_t, lang=short_lang, domain_hint="film", year=c.year
                                )
                                if loc_syn:
                                    c.synopsis = loc_syn
                                if loc_t:
                                    c.title = loc_t
                                if loc_tr and not c.youtube_url:
                                    c.youtube_url = loc_tr
                            if not c.video_url and not c.youtube_url:
                                dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                                    client, f"{orig_t} {c.year} trailer", max_snapshots=3
                                )
                                if dm_url:
                                    c.youtube_url = dm_url
                                for st in dm_stills:
                                    c.add_unique_snapshot(st)

                    await asyncio.gather(*(_enrich_itunes(c) for c in top_to_enrich), return_exceptions=True)
        except Exception as exc:
            logger.debug("[iTunes Movies] Search error for '%s': %s", q_clean, exc)

        return candidates[:target_limit]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        cands = await self.search_candidates(
            query=query,
            criteria=FilterCriteria(domain="movies", genres=[], decades=[], lang=lang),
            limit=1,
        )
        return cands[0] if cands else None
