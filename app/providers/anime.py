"""
Live Anime Provider (`anime`):
- Zero static/hardcoded mock catalogs.
- Exhaustive 31 Anime Genres, Themes & Demographics and 6 Decades aligned with
  Jikan v4 (MyAnimeList), Kitsu API, and AnimeThemes.moe.
- Multi-source live waterfall:
  1. Jikan v4 (`/v4/anime` with MAL genre IDs, decade date bounds, and `/pictures`)
  2. Kitsu Edge API (`kitsu.io/api/edge/anime` with `posterImage.large` & `coverImage.large` banner)
  3. AnimeThemes.moe (`api.animethemes.moe/anime` for direct creditless `.webm` OP/ED videos & covers)
  4. Wikipedia REST API (localized synopsis) + Dailymotion Video API (OP/ED/PV fallback & stills)
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models import DomainType, SessionRegisterRequest
from app.providers.base import (
    BaseMediaProvider,
    RawMediaCandidate,
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    fetch_wikipedia_summary,
)

logger = logging.getLogger("dmd_showcase.scraper.anime")

# Exhaustive 31 Anime Genres / Themes / Demographics with Jikan MAL ID & Kitsu category slug
ANIME_GENRES: Dict[str, Dict[str, Any]] = {
    "action": {"mal_id": 1, "kitsu": "action", "label": "Action", "color": "#E53935", "palette": (229, 57, 53)},
    "adventure": {"mal_id": 2, "kitsu": "adventure", "label": "Adventure", "color": "#00897B", "palette": (0, 137, 123)},
    "comedy": {"mal_id": 4, "kitsu": "comedy", "label": "Comedy", "color": "#FDD835", "palette": (253, 216, 53)},
    "drama": {"mal_id": 8, "kitsu": "drama", "label": "Drama", "color": "#5E35B1", "palette": (94, 53, 177)},
    "fantasy": {"mal_id": 10, "kitsu": "fantasy", "label": "Fantasy", "color": "#8E24AA", "palette": (142, 36, 170)},
    "scifi": {"mal_id": 24, "kitsu": "science-fiction", "label": "Sci-Fi", "color": "#00ACC1", "palette": (0, 172, 193)},
    "mecha": {"mal_id": 18, "kitsu": "mecha", "label": "Mecha", "color": "#1E88E5", "palette": (30, 136, 229)},
    "cyberpunk": {"mal_id": 24, "kitsu": "cyberpunk", "label": "Cyberpunk", "color": "#00E5FF", "palette": (0, 229, 255)},
    "shonen": {"mal_id": 27, "kitsu": "shounen", "label": "Shonen", "color": "#FB8C00", "palette": (251, 140, 0)},
    "seinen": {"mal_id": 42, "kitsu": "seinen", "label": "Seinen", "color": "#B71C1C", "palette": (183, 28, 28)},
    "shojo": {"mal_id": 25, "kitsu": "shoujo", "label": "Shojo", "color": "#D81B60", "palette": (216, 27, 96)},
    "mystery": {"mal_id": 7, "kitsu": "mystery", "label": "Mystery", "color": "#3949AB", "palette": (57, 73, 171)},
    "psychological": {"mal_id": 40, "kitsu": "psychological", "label": "Psychological", "color": "#6A1B9A", "palette": (106, 27, 154)},
    "horror": {"mal_id": 14, "kitsu": "horror", "label": "Horror", "color": "#C62828", "palette": (198, 40, 40)},
    "supernatural": {"mal_id": 37, "kitsu": "supernatural", "label": "Supernatural", "color": "#9C27B0", "palette": (156, 39, 176)},
    "romance": {"mal_id": 22, "kitsu": "romance", "label": "Romance", "color": "#EC407A", "palette": (236, 64, 122)},
    "slice_of_life": {"mal_id": 36, "kitsu": "slice-of-life", "label": "Slice of Life", "color": "#26A69A", "palette": (38, 166, 154)},
    "sports": {"mal_id": 30, "kitsu": "sports", "label": "Sports", "color": "#43A047", "palette": (67, 160, 71)},
    "music": {"mal_id": 19, "kitsu": "music", "label": "Music", "color": "#AB47BC", "palette": (171, 71, 188)},
    "historical": {"mal_id": 13, "kitsu": "historical", "label": "Historical", "color": "#8D6E63", "palette": (141, 110, 99)},
    "military": {"mal_id": 38, "kitsu": "military", "label": "Military", "color": "#546E7A", "palette": (84, 110, 122)},
    "martial_arts": {"mal_id": 17, "kitsu": "martial-arts", "label": "Martial Arts", "color": "#FF6F00", "palette": (255, 111, 0)},
    "samurai": {"mal_id": 21, "kitsu": "samurai", "label": "Samurai", "color": "#D84315", "palette": (216, 67, 21)},
    "space": {"mal_id": 29, "kitsu": "space", "label": "Space Opera", "color": "#283593", "palette": (40, 53, 147)},
    "vampire": {"mal_id": 32, "kitsu": "vampire", "label": "Vampire", "color": "#AD1457", "palette": (173, 20, 87)},
    "isekai": {"mal_id": 62, "kitsu": "isekai", "label": "Isekai", "color": "#00ACC1", "palette": (0, 172, 193)},
    "mahou_shoujo": {"mal_id": 66, "kitsu": "magical-girl", "label": "Magical Girl", "color": "#F06292", "palette": (240, 98, 146)},
    "post_apocalyptic": {"mal_id": 24, "kitsu": "post-apocalyptic", "label": "Post-Apocalyptic", "color": "#6D4C41", "palette": (109, 76, 65)},
    "racing": {"mal_id": 3, "kitsu": "motorsports", "label": "Racing", "color": "#F4511E", "palette": (244, 81, 30)},
    "parody": {"mal_id": 20, "kitsu": "parody", "label": "Parody", "color": "#7CB342", "palette": (124, 179, 66)},
    "thriller": {"mal_id": 41, "kitsu": "thriller", "label": "Suspense / Thriller", "color": "#FFB300", "palette": (255, 179, 0)},
}

ANIME_DECADES: Dict[str, Tuple[str, str]] = {
    "1970s": ("1970-01-01", "1979-12-31"),
    "1980s": ("1980-01-01", "1989-12-31"),
    "1990s": ("1990-01-01", "1999-12-31"),
    "2000s": ("2000-01-01", "2009-12-31"),
    "2010s": ("2010-01-01", "2019-12-31"),
    "2020s": ("2020-01-01", "2029-12-31"),
}


class AnimeProvider(BaseMediaProvider):
    domain: DomainType = "anime"

    async def _enrich_with_animethemes_by_title(
        self,
        client: Any,
        cand: RawMediaCandidate,
        video_type: str = "opening",
    ) -> None:
        """Search AnimeThemes.moe by title to attach direct `.webm` OP/ED stream and cover art."""
        target_prefix = "OP" if video_type == "opening" else ("ED" if video_type == "ending" else "")
        try:
            resp = await client.get(
                "https://api.animethemes.moe/anime",
                params={
                    "filter[name-like]": f"%{cand.title}%",
                    "include": "animethemes.animethemeentries.videos,images",
                    "page[size]": "3",
                },
                timeout=4.0,
            )
            if resp.status_code != 200:
                return
            anime_list = resp.json().get("anime") or []
            if not anime_list:
                return
            anime_obj = anime_list[0]
            for img in anime_obj.get("images") or []:
                link = img.get("link")
                if link:
                    if not cand.poster_url:
                        cand.poster_url = link
                        cand.jacket_url = link
                    cand.add_unique_image(link)

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
            # Second pass if specific OP/ED filter yielded nothing
            for theme in anime_obj.get("animethemes") or []:
                for entry in theme.get("animethemeentries") or []:
                    for vid in entry.get("videos") or []:
                        link = vid.get("link")
                        if link and not cand.video_url:
                            cand.video_url = str(link)
                            return
        except Exception as exc:
            logger.debug("[SCRAPER:anime] AnimeThemes lookup skipped for '%s': %s", cand.title, exc)

    async def _discover_kitsu_anime(
        self,
        client: Any,
        genre_key: str,
        decade: Optional[str],
        lang: str,
        video_type: str,
        count: int,
        exclude: Set[str],
    ) -> List[RawMediaCandidate]:
        """Discover random anime live from Kitsu Edge API by category and decade."""
        g_meta = ANIME_GENRES.get(genre_key, ANIME_GENRES["shonen"])
        params: Dict[str, str] = {
            "filter[categories]": g_meta["kitsu"],
            "page[limit]": str(max(12, count * 3)),
            "page[offset]": str(random.randint(0, 60)),
            "sort": "-userCount",
        }
        if decade and decade in ANIME_DECADES:
            yr_prefix = decade[:3]
            params["filter[seasonYear]"] = f"{yr_prefix}0..{yr_prefix}9"

        results: List[RawMediaCandidate] = []
        try:
            resp = await client.get("https://kitsu.io/api/edge/anime", params=params, timeout=4.5)
            if resp.status_code != 200 or not (resp.json().get("data")):
                params.pop("filter[seasonYear]", None)
                params["page[offset]"] = str(random.randint(0, 30))
                resp = await client.get("https://kitsu.io/api/edge/anime", params=params, timeout=4.5)
            if resp.status_code != 200:
                return []

            items = resp.json().get("data") or []
            random.shuffle(items)
            for item in items:
                if len(results) >= count:
                    break
                k_id = str(item.get("id") or "")
                cand_id = f"anime_kitsu_{k_id}"
                if cand_id in exclude:
                    continue
                attrs = item.get("attributes") or {}
                titles = attrs.get("titles") or {}
                title = (
                    titles.get("en")
                    or titles.get("en_jp")
                    or attrs.get("canonicalTitle")
                    or "Unknown Anime"
                ).strip()
                start_date = str(attrs.get("startDate") or "2010")
                year = start_date[:4] if len(start_date) >= 4 else "2010"
                avg_rating = float(attrs.get("averageRating") or 82.0) / 10.0

                poster_obj = attrs.get("posterImage") or {}
                cover_obj = attrs.get("coverImage") or {}
                poster_url = poster_obj.get("large") or poster_obj.get("medium") or poster_obj.get("original")
                banner_url = cover_obj.get("large") or cover_obj.get("original")

                if not poster_url:
                    continue

                synopsis = clean_synopsis_text(attrs.get("synopsis") or attrs.get("description"), max_chars=260)
                if lang != "en" or not synopsis:
                    wiki_syn, wiki_thumb = await fetch_wikipedia_summary(client, title, lang=lang)
                    if wiki_syn:
                        synopsis = wiki_syn

                cand = RawMediaCandidate(
                    id=cand_id,
                    domain="anime",
                    title=title,
                    year=year,
                    badge=g_meta["label"][:8].upper(),
                    badge_color=g_meta["color"],
                    subtitle=f"{g_meta['label']} • {year}"[:28],
                    rating=f"{avg_rating:.1f}",
                    synopsis=synopsis or f"{title} ({year}) — {g_meta['label']} anime.",
                    lang=lang,
                    poster_url=poster_url,
                    jacket_url=poster_url,
                    marquee_url=banner_url or poster_url,
                    backdrop_url=banner_url,
                    source_used="kitsu",
                    theme_palette=g_meta["palette"],
                )
                if banner_url:
                    cand.add_unique_image(banner_url)

                # Enrich with AnimeThemes.moe direct OP/ED stream and Dailymotion stills/clip
                await self._enrich_with_animethemes_by_title(client, cand, video_type=video_type)
                dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                    client, f"{title} anime {video_type if video_type != 'any' else 'opening'}", max_snapshots=4
                )
                if dm_url and not cand.video_url and not cand.youtube_url:
                    cand.youtube_url = dm_url
                for st in dm_stills:
                    cand.add_unique_image(st)

                results.append(cand)
        except Exception as exc:
            logger.warning("[SCRAPER:anime] Live Kitsu discovery error: %s", exc)

        return results

    async def _discover_jikan_anime(
        self,
        client: Any,
        genre_key: str,
        decade: Optional[str],
        lang: str,
        video_type: str,
        count: int,
        exclude: Set[str],
    ) -> List[RawMediaCandidate]:
        """Discover random anime live from Jikan v4 (MyAnimeList) by genre and decade."""
        g_meta = ANIME_GENRES.get(genre_key, ANIME_GENRES["shonen"])
        params: Dict[str, str] = {
            "genres": str(g_meta["mal_id"]),
            "order_by": "members",
            "sort": "desc",
            "limit": "20",
            "page": str(random.randint(1, 4)),
            "sfw": "true",
        }
        if decade and decade in ANIME_DECADES:
            s_date, e_date = ANIME_DECADES[decade]
            params["start_date"] = s_date
            params["end_date"] = e_date

        results: List[RawMediaCandidate] = []
        try:
            resp = await client.get("https://api.jikan.moe/v4/anime", params=params, timeout=4.5)
            if resp.status_code != 200:
                return []
            items = resp.json().get("data") or []
            random.shuffle(items)
            for item in items:
                if len(results) >= count:
                    break
                mal_id = item.get("mal_id")
                cand_id = f"anime_mal_{mal_id}"
                if cand_id in exclude:
                    continue
                title = (
                    item.get("title_english")
                    or item.get("title")
                    or "Unknown Anime"
                ).strip()
                year = str(item.get("year") or ((item.get("aired") or {}).get("prop", {}).get("from", {}).get("year")) or "2010")[:4]
                score = float(item.get("score") or 8.2)
                images = (item.get("images") or {}).get("jpg") or {}
                poster_url = images.get("large_image_url") or images.get("image_url")
                if not poster_url:
                    continue

                synopsis = clean_synopsis_text(item.get("synopsis"), max_chars=260)
                if lang != "en" or not synopsis:
                    wiki_syn, _ = await fetch_wikipedia_summary(client, title, lang=lang)
                    if wiki_syn:
                        synopsis = wiki_syn

                trailer = item.get("trailer") or {}
                yt_id = trailer.get("youtube_id")
                trailer_img = (trailer.get("images") or {}).get("large_image_url")

                cand = RawMediaCandidate(
                    id=cand_id,
                    domain="anime",
                    title=title,
                    year=year,
                    badge=g_meta["label"][:8].upper(),
                    badge_color=g_meta["color"],
                    subtitle=f"{g_meta['label']} • {year}"[:28],
                    rating=f"{score:.1f}",
                    synopsis=synopsis or f"{title} ({year}) — {g_meta['label']} anime.",
                    lang=lang,
                    poster_url=poster_url,
                    jacket_url=poster_url,
                    marquee_url=trailer_img or poster_url,
                    youtube_url=f"https://www.youtube.com/watch?v={yt_id}" if yt_id else None,
                    source_used="jikan",
                    theme_palette=g_meta["palette"],
                )
                if trailer_img:
                    cand.add_unique_image(trailer_img)

                await self._enrich_with_animethemes_by_title(client, cand, video_type=video_type)
                dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                    client, f"{title} anime opening trailer", max_snapshots=4
                )
                if dm_url and not cand.video_url and not cand.youtube_url:
                    cand.youtube_url = dm_url
                for st in dm_stills:
                    cand.add_unique_image(st)
                if not cand.marquee_url and cand.snapshot_urls:
                    cand.marquee_url = cand.snapshot_urls[0]

                results.append(cand)
        except Exception as exc:
            logger.debug("[SCRAPER:anime] Jikan v4 live discovery skipped: %s", exc)

        return results

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        exclude = set(exclude_ids or set())
        req_genres = [
            g.lower().strip()
            for g in req.genres
            if g.strip() and g.lower().strip() in ANIME_GENRES
        ]
        if not req_genres:
            req_genres = list(ANIME_GENRES.keys())

        req_decades = [
            d.lower().strip()
            for d in req.decades
            if d.strip() and d.lower().strip() in ANIME_DECADES
        ]
        if req.decade and req.decade.lower().strip() in ANIME_DECADES:
            req_decades.append(req.decade.lower().strip())

        results: List[RawMediaCandidate] = []
        primary = (req.primary_source or "auto").lower().strip()

        async with create_async_http_client(timeout=5.0) as client:
            attempts = 0
            while len(results) < req.count and attempts < 4:
                attempts += 1
                chosen_genre = random.choice(req_genres)
                chosen_decade = random.choice(req_decades) if req_decades else None
                needed = req.count - len(results)

                if primary == "jikan":
                    batch = await self._discover_jikan_anime(
                        client, chosen_genre, chosen_decade, req.lang, req.video_type, needed, exclude
                    )
                    if len(batch) < needed:
                        batch += await self._discover_kitsu_anime(
                            client, chosen_genre, chosen_decade, req.lang, req.video_type, needed - len(batch), exclude
                        )
                else:
                    batch = await self._discover_kitsu_anime(
                        client, chosen_genre, chosen_decade, req.lang, req.video_type, needed, exclude
                    )
                    if len(batch) < needed:
                        batch += await self._discover_jikan_anime(
                            client, chosen_genre, chosen_decade, req.lang, req.video_type, needed - len(batch), exclude
                        )

                for item in batch:
                    exclude.add(item.id)
                    results.append(item)
                    if len(results) >= req.count:
                        break

        logger.info(
            "[SCRAPER:anime] Live discovery returned %d/%d anime (genres=%s): %s",
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
                resp = await client.get(
                    "https://kitsu.io/api/edge/anime",
                    params={"filter[text]": q_clean, "page[limit]": "1"},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data") or []
                    if data:
                        attrs = data[0].get("attributes") or {}
                        title = attrs.get("canonicalTitle") or q_clean
                        year = str(attrs.get("startDate") or "2010")[:4]
                        poster = (attrs.get("posterImage") or {}).get("large")
                        cover = (attrs.get("coverImage") or {}).get("large")
                        cand = RawMediaCandidate(
                            id=f"anime_kitsu_{data[0].get('id')}",
                            domain="anime",
                            title=title,
                            year=year,
                            badge="ANIME",
                            badge_color="#FB8C00",
                            subtitle=f"Anime • {year}",
                            rating="8.5",
                            synopsis=clean_synopsis_text(attrs.get("synopsis")),
                            lang=lang,
                            poster_url=poster,
                            jacket_url=poster,
                            marquee_url=cover or poster,
                            source_used="kitsu",
                        )
                        await self._enrich_with_animethemes_by_title(client, cand)
                        dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                            client, f"{title} anime opening"
                        )
                        if dm_url and not cand.video_url:
                            cand.youtube_url = dm_url
                        for st in dm_stills:
                            cand.add_unique_image(st)
                        return cand
        except Exception:
            pass
        return None

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "sources": [
                {"id": "auto", "label": "Auto (Kitsu + AnimeThemes.moe + Jikan v4 + Dailymotion)"},
                {"id": "animethemes", "label": "AnimeThemes.moe (Direct Creditless OP/ED)"},
                {"id": "kitsu", "label": "Kitsu Edge API (Keyless Live)"},
                {"id": "jikan", "label": "Jikan v4 / MyAnimeList"},
            ],
            "genres": [
                {"id": gid, "label": meta["label"]}
                for gid, meta in ANIME_GENRES.items()
            ],
            "decades": [
                {"id": "all", "label": "All Decades"},
                *[{"id": dec, "label": dec} for dec in ANIME_DECADES.keys()],
            ],
            "video_types": [
                {"id": "opening", "label": "Opening (OP)"},
                {"id": "ending", "label": "Ending (ED)"},
                {"id": "pv", "label": "Promotional Trailer (PV)"},
                {"id": "any", "label": "Any"},
            ],
        }
