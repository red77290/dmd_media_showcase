"""
Live Movies Provider (`movies`):
- Zero static/hardcoded mock catalogs.
- Exhaustive 23 Movie Genres and 7 Decades aligned with TMDB, IMDb, and Wikipedia.
- Multi-source live waterfall:
  1. TMDB API v3 (`/discover/movie` + language-agnostic `/videos` + `/images`)
  2. Live Wikipedia Film Search + IMDb Suggestion CDN (`v3.sg.media-imdb.com`)
     + Dailymotion Public Trailer API (`api.dailymotion.com/videos`, language-agnostic)
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import settings
from app.models import DomainType, SessionRegisterRequest
from app.providers.base import (
    BaseMediaProvider,
    RawMediaCandidate,
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    fetch_imdb_suggestion_items,
    fetch_wikipedia_summary,
)

logger = logging.getLogger("dmd_showcase.scraper.movies")

TMDB_LANG_MAP = {
    "fr": "fr-FR",
    "en": "en-US",
    "es": "es-ES",
    "de": "de-DE",
    "it": "it-IT",
    "pt": "pt-BR",
    "ja": "ja-JP",
}

MOVIE_GENRES: Dict[str, Dict[str, Any]] = {
    "action": {"tmdb_id": 28, "label": "Action", "query": "action film", "color": "#E53935", "palette": (229, 57, 53)},
    "scifi": {"tmdb_id": 878, "label": "Sci-Fi", "query": "science fiction film", "color": "#00ACC1", "palette": (0, 172, 193)},
    "thriller": {"tmdb_id": 53, "label": "Thriller", "query": "thriller film", "color": "#FB8C00", "palette": (251, 140, 0)},
    "animation": {"tmdb_id": 16, "label": "Animation", "query": "animated feature film", "color": "#43A047", "palette": (67, 160, 71)},
    "horror": {"tmdb_id": 27, "label": "Horror", "query": "horror film", "color": "#B71C1C", "palette": (183, 28, 28)},
    "comedy": {"tmdb_id": 35, "label": "Comedy", "query": "comedy film", "color": "#FDD835", "palette": (253, 216, 53)},
    "drama": {"tmdb_id": 18, "label": "Drama", "query": "drama film", "color": "#5E35B1", "palette": (94, 53, 177)},
    "crime": {"tmdb_id": 80, "label": "Crime", "query": "crime gangster film", "color": "#6D4C41", "palette": (109, 76, 65)},
    "adventure": {"tmdb_id": 12, "label": "Adventure", "query": "adventure film", "color": "#00897B", "palette": (0, 137, 123)},
    "fantasy": {"tmdb_id": 14, "label": "Fantasy", "query": "fantasy film", "color": "#8E24AA", "palette": (142, 36, 170)},
    "mystery": {"tmdb_id": 9648, "label": "Mystery", "query": "mystery detective film", "color": "#3949AB", "palette": (57, 73, 171)},
    "romance": {"tmdb_id": 10749, "label": "Romance", "query": "romantic drama film", "color": "#D81B60", "palette": (216, 27, 96)},
    "western": {"tmdb_id": 37, "label": "Western", "query": "western film", "color": "#F4511E", "palette": (244, 81, 30)},
    "war": {"tmdb_id": 10752, "label": "War", "query": "war film", "color": "#546E7A", "palette": (84, 110, 122)},
    "history": {"tmdb_id": 36, "label": "History", "query": "historical epic film", "color": "#8D6E63", "palette": (141, 110, 99)},
    "documentary": {"tmdb_id": 99, "label": "Documentary", "query": "documentary film", "color": "#26A69A", "palette": (38, 166, 154)},
    "music": {"tmdb_id": 10402, "label": "Music", "query": "musical concert film", "color": "#AB47BC", "palette": (171, 71, 188)},
    "family": {"tmdb_id": 10751, "label": "Family", "query": "family adventure film", "color": "#29B6F6", "palette": (41, 182, 246)},
    "cyberpunk": {"tmdb_id": 878, "label": "Cyberpunk", "query": "cyberpunk dystopian film", "color": "#00E5FF", "palette": (0, 229, 255)},
    "martial_arts": {"tmdb_id": 28, "label": "Martial Arts", "query": "martial arts kung fu film", "color": "#FF6F00", "palette": (255, 111, 0)},
    "noir": {"tmdb_id": 80, "label": "Film Noir", "query": "neo-noir thriller film", "color": "#78909C", "palette": (120, 144, 156)},
    "superhero": {"tmdb_id": 28, "label": "Superhero", "query": "superhero comic film", "color": "#D32F2F", "palette": (211, 47, 47)},
    "kaiju": {"tmdb_id": 878, "label": "Kaiju / Monster", "query": "kaiju giant monster film", "color": "#7CB342", "palette": (124, 179, 66)},
}

DECADE_BOUNDS: Dict[str, Tuple[str, str]] = {
    "1960s": ("1960-01-01", "1969-12-31"),
    "1970s": ("1970-01-01", "1979-12-31"),
    "1980s": ("1980-01-01", "1989-12-31"),
    "1990s": ("1990-01-01", "1999-12-31"),
    "2000s": ("2000-01-01", "2009-12-31"),
    "2010s": ("2010-01-01", "2019-12-31"),
    "2020s": ("2020-01-01", "2029-12-31"),
}


class MoviesProvider(BaseMediaProvider):
    domain: DomainType = "movies"

    def _resolve_tmdb_key(self, req: Optional[SessionRegisterRequest]) -> str:
        if req and req.scraper_api_key and req.scraper_api_key.strip():
            return req.scraper_api_key.strip()
        return settings.tmdb_api_key

    async def _fetch_from_tmdb(
        self, req: SessionRegisterRequest, exclude: Set[str]
    ) -> List[RawMediaCandidate]:
        api_key = self._resolve_tmdb_key(req)
        if not api_key or (req.primary_source or "").lower() in ("wikipedia", "imdb"):
            return []

        short_lang = (req.lang or "fr").split("-")[0].lower()
        tmdb_lang = TMDB_LANG_MAP.get(short_lang, "fr-FR")

        genre_keys = [
            g.lower().strip()
            for g in req.genres
            if g.lower().strip() in MOVIE_GENRES
        ]
        genre_ids = list({str(MOVIE_GENRES[g]["tmdb_id"]) for g in genre_keys})
        chosen_genre_meta = MOVIE_GENRES.get(genre_keys[0], MOVIE_GENRES["scifi"]) if genre_keys else MOVIE_GENRES["scifi"]

        params: Dict[str, str] = {
            "api_key": api_key,
            "language": tmdb_lang,
            "sort_by": "popularity.desc",
            "vote_count.gte": "150",
            "vote_average.gte": str(max(5.0, req.min_rating)),
            "page": str(random.randint(1, 8)),
        }
        if genre_ids:
            params["with_genres"] = "|".join(genre_ids)

        req_decades = [d for d in req.decades if d in DECADE_BOUNDS]
        if req_decades:
            chosen_dec = random.choice(req_decades)
            start_d, end_d = DECADE_BOUNDS[chosen_dec]
            params["primary_release_date.gte"] = start_d
            params["primary_release_date.lte"] = end_d

        candidates: List[RawMediaCandidate] = []
        try:
            async with create_async_http_client(timeout=5.0) as client:
                resp = await client.get("https://api.themoviedb.org/3/discover/movie", params=params)
                if resp.status_code != 200:
                    return []
                results = resp.json().get("results") or []
                random.shuffle(results)
                for item in results:
                    tmdb_id = item.get("id")
                    item_id = f"movie_tmdb_{tmdb_id}"
                    if item_id in exclude:
                        continue
                    release_date = item.get("release_date") or "1995"
                    year = release_date[:4]
                    poster_path = item.get("poster_path")
                    backdrop_path = item.get("backdrop_path")
                    vote = float(item.get("vote_average") or 7.5)

                    poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
                    backdrop_url = f"https://image.tmdb.org/t/p/w780{backdrop_path}" if backdrop_path else None
                    title = item.get("title") or item.get("original_title") or "Unknown Movie"

                    cand = RawMediaCandidate(
                        id=item_id,
                        domain="movies",
                        title=title,
                        year=year,
                        badge=chosen_genre_meta["label"][:8].upper(),
                        badge_color=chosen_genre_meta["color"],
                        subtitle=f"{chosen_genre_meta['label']} • {year}"[:28],
                        rating=f"{vote:.1f}",
                        synopsis=clean_synopsis_text(item.get("overview")),
                        lang=req.lang,
                        poster_url=poster_url,
                        jacket_url=poster_url,
                        marquee_url=backdrop_url,
                        backdrop_url=backdrop_url,
                        source_used="tmdb",
                        theme_palette=chosen_genre_meta["palette"],
                    )
                    if backdrop_url:
                        cand.add_unique_image(backdrop_url)

                    # Query TMDB /videos in ALL languages (DMD is mute)
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

                    # Fallback to Dailymotion trailer & scene stills if YouTube key missing
                    if not cand.youtube_url:
                        dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                            client, f"{title} {year} trailer", max_snapshots=3
                        )
                        if dm_url:
                            cand.youtube_url = dm_url
                        for st in dm_stills:
                            cand.add_unique_image(st)

                    candidates.append(cand)
                    if len(candidates) >= req.count:
                        break
        except Exception as exc:
            logger.info("[SCRAPER:movies] TMDB live discover error: %s", exc)
        return candidates

    async def _discover_live_imdb_wikipedia_movies(
        self,
        genre_key: str,
        decade: Optional[str],
        lang: str,
        count: int,
        exclude: Set[str],
    ) -> List[RawMediaCandidate]:
        """
        Keyless live movie discovery combining Wikipedia MediaWiki Film Search,
        IMDb Suggestion CDN (`v3.sg.media-imdb.com`), and Dailymotion Trailer API.
        """
        g_meta = MOVIE_GENRES.get(genre_key, MOVIE_GENRES["scifi"])
        dec_str = decade[:4] if decade and decade[:4].isdigit() else random.choice(["1980", "1990", "2000", "2010", "2020"])
        search_q = f"{dec_str}s {g_meta['query']} incategory:Films"
        candidates: List[RawMediaCandidate] = []

        try:
            async with create_async_http_client(timeout=5.0) as client:
                offset = random.randint(0, 40)
                resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": search_q,
                        "srlimit": max(15, count * 5),
                        "sroffset": offset,
                        "format": "json",
                    },
                )
                items = (
                    resp.json().get("query", {}).get("search", [])
                    if resp.status_code == 200
                    else []
                )
                if not items:
                    resp = await client.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={
                            "action": "query",
                            "list": "search",
                            "srsearch": f"{g_meta['query']}",
                            "srlimit": 20,
                            "format": "json",
                        },
                    )
                    items = (
                        resp.json().get("query", {}).get("search", [])
                        if resp.status_code == 200
                        else []
                    )

                random.shuffle(items)
                for entry in items:
                    if len(candidates) >= count:
                        break
                    raw_title = str(entry.get("title") or "").strip()
                    if not raw_title or raw_title.startswith(("List of", "Category:", "History of")):
                        continue
                    clean_title = re.sub(r"\s*\([^)]*film[^)]*\)\s*$", "", raw_title, flags=re.I).strip()
                    if not clean_title or len(clean_title) < 2:
                        continue

                    # Query IMDb Suggestion CDN for official poster + year + IMDb ID
                    imdb_hits = await fetch_imdb_suggestion_items(
                        client, clean_title, qid_filter={"movie", "feature"}
                    )
                    if not imdb_hits:
                        continue
                    imdb_info = imdb_hits[0]
                    imdb_id = str(imdb_info.get("id") or "")
                    cand_id = f"movie_{imdb_id}"
                    if cand_id in exclude:
                        continue

                    imdb_title = str(imdb_info.get("l") or clean_title).strip()
                    year_val = str(imdb_info.get("y") or dec_str)[:4]
                    poster_url = str((imdb_info.get("i") or {}).get("imageUrl") or "")
                    stars = str(imdb_info.get("s") or "").strip()

                    wiki_syn, wiki_thumb = await fetch_wikipedia_summary(
                        client, raw_title, lang=lang, domain_hint="film"
                    )
                    if not wiki_syn:
                        wiki_syn = clean_synopsis_text(entry.get("snippet"), max_chars=250)
                    if not wiki_syn:
                        wiki_syn = f"{imdb_title} ({year_val}) — {g_meta['label']}. {stars}".strip()

                    dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                        client, f"{imdb_title} {year_val} trailer", max_snapshots=4
                    )

                    marquee_url = dm_stills[0] if dm_stills else (wiki_thumb or poster_url)
                    cand = RawMediaCandidate(
                        id=cand_id,
                        domain="movies",
                        title=imdb_title,
                        year=year_val,
                        badge=g_meta["label"][:8].upper(),
                        badge_color=g_meta["color"],
                        subtitle=f"{g_meta['label']} • {year_val}"[:28],
                        rating="8.4",
                        synopsis=wiki_syn,
                        lang=lang,
                        poster_url=poster_url or wiki_thumb,
                        jacket_url=poster_url or wiki_thumb,
                        marquee_url=marquee_url,
                        backdrop_url=marquee_url,
                        youtube_url=dm_url,
                        source_used="imdb+wikipedia+dailymotion",
                        theme_palette=g_meta["palette"],
                    )
                    for st in dm_stills:
                        cand.add_unique_image(st)
                    if wiki_thumb:
                        cand.add_unique_image(wiki_thumb)
                    candidates.append(cand)
        except Exception as exc:
            logger.warning("[SCRAPER:movies] Live IMDb/Wikipedia movie discovery failed: %s", exc)

        return candidates

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        exclude = set(exclude_ids or set())
        results = await self._fetch_from_tmdb(req, exclude)
        for r in results:
            exclude.add(r.id)
        if len(results) >= req.count:
            return results[: req.count]

        req_genres = [
            g.lower().strip()
            for g in req.genres
            if g.strip() and g.lower().strip() in MOVIE_GENRES
        ]
        if not req_genres:
            req_genres = list(MOVIE_GENRES.keys())

        req_decades = [
            d.lower().strip()
            for d in req.decades
            if d.strip() and d.lower().strip() in DECADE_BOUNDS
        ]

        attempts = 0
        while len(results) < req.count and attempts < 5:
            attempts += 1
            chosen_genre = random.choice(req_genres)
            chosen_decade = random.choice(req_decades) if req_decades else req.decade
            needed = req.count - len(results)
            batch = await self._discover_live_imdb_wikipedia_movies(
                chosen_genre, chosen_decade, req.lang, needed, exclude
            )
            for item in batch:
                exclude.add(item.id)
                results.append(item)
                if len(results) >= req.count:
                    break

        logger.info(
            "[SCRAPER:movies] Live discovery returned %d/%d movies (genres=%s): %s",
            len(results),
            req.count,
            req_genres[:5],
            [c.title for c in results],
        )
        return results[: req.count]

    async def search_on_demand(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[RawMediaCandidate]:
        q_clean = query.strip()
        if not q_clean:
            return None
        try:
            async with create_async_http_client(timeout=5.0) as client:
                hits = await fetch_imdb_suggestion_items(client, q_clean, qid_filter={"movie", "feature"})
                if not hits:
                    hits = await fetch_imdb_suggestion_items(client, q_clean)
                if not hits:
                    return None
                info = hits[0]
                title = str(info.get("l") or q_clean)
                year = str(info.get("y") or "2000")[:4]
                poster = str((info.get("i") or {}).get("imageUrl") or "")
                wiki_syn, wiki_thumb = await fetch_wikipedia_summary(client, title, lang=lang, domain_hint="film")
                dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(client, f"{title} {year} trailer")
                cand = RawMediaCandidate(
                    id=f"movie_{info.get('id', 'ondemand')}",
                    domain="movies",
                    title=title,
                    year=year,
                    badge="FILM",
                    badge_color="#00ACC1",
                    subtitle=f"Cinema • {year}",
                    rating="8.5",
                    synopsis=wiki_syn or f"{title} ({year})",
                    lang=lang,
                    poster_url=poster or wiki_thumb,
                    jacket_url=poster or wiki_thumb,
                    marquee_url=dm_stills[0] if dm_stills else poster,
                    youtube_url=dm_url,
                    source_used="imdb+dailymotion",
                )
                for st in dm_stills:
                    cand.add_unique_image(st)
                return cand
        except Exception:
            return None

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "sources": [
                {"id": "auto", "label": "Auto (TMDB -> IMDb + Wikipedia + Dailymotion Live)"},
                {"id": "tmdb", "label": "TMDB API v3 (Optional Key)"},
                {"id": "imdb", "label": "IMDb + Wikipedia + Dailymotion (Keyless Live)"},
            ],
            "genres": [
                {"id": gid, "label": meta["label"]}
                for gid, meta in MOVIE_GENRES.items()
            ],
            "decades": [
                {"id": "all", "label": "All Decades"},
                *[{"id": dec, "label": dec} for dec in DECADE_BOUNDS.keys()],
            ],
        }
