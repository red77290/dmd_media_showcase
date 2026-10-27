"""
Fallback TV Series Scraper Adapter: TVMaze Public API (`api.tvmaze.com/shows`).

Keyless public TV catalog adapter implementing `IScraperSource`:
- Uniform random pagination across 280+ pages of `/shows?page={random_page}` (~70,000 TV series).
- Extracts `original` HD posters, wide `banner` marquees, `background` stills, and localized synopses.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import resolve_canonical_genre
from app.infrastructure.scrapers.catalog_metadata import DECADE_BOUNDS, SERIES_GENRES
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
    fetch_wikipedia_localized_synopsis,
    resolve_multilingual_media_details,
)

logger = logging.getLogger("dmd_showcase.scrapers.series.tvmaze")

_EXCLUDED_TVMAZE_SHOW_TYPES = {
    "talk show",
    "game show",
    "news",
    "reality",
    "variety",
    "award show",
    "panel show",
}

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
    display_name: str = "TVMaze"
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
        enrich_heavy: bool = True,
    ) -> MediaCandidate:
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        show_id = show.get("id")
        orig_title = str(show.get("name") or "Unknown Series").strip()
        title = orig_title
        premiered = str(show.get("premiered") or "2010")
        year = premiered[:4] if len(premiered) >= 4 else "2010"
        rating_val = (show.get("rating") or {}).get("average") or 8.1
        genres = show.get("genres") or [genre_meta["label"]]
        primary_genre = str(genres[0]) if genres else genre_meta["label"]
        network = ((show.get("network") or show.get("webChannel") or {}).get("name") or "TV").strip()

        img = show.get("image") or {}
        poster_url = img.get("original") or img.get("medium")

        summary = clean_synopsis_text(show.get("summary"), max_chars=300)
        resolved_trailer: Optional[str] = None
        if enrich_heavy:
            loc_t, loc_syn, loc_tr = await resolve_multilingual_media_details(
                client, orig_title, lang=short_lang, domain_hint="TV series", year=year
            )
            if loc_syn and (short_lang != "en" or not summary):
                summary = loc_syn
            if loc_t and short_lang != "en":
                title = loc_t
            if loc_tr:
                resolved_trailer = loc_tr

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
            lang=short_lang,
            poster_url=poster_url,
            jacket_url=poster_url,
            youtube_url=resolved_trailer,
            source_id=self.source_id,
            theme_palette=genre_meta["palette"],
            extra_meta={"tvmaze_id": str(show_id)},
        )

        if not enrich_heavy:
            return cand

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

        # Try Apple iTunes TV Episode previewUrl (.m4v) using original title first
        for term in dict.fromkeys([orig_title, title]):
            try:
                it_resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": term,
                        "media": "tvShow",
                        "entity": "tvEpisode",
                        "country": "US",
                        "limit": "5",
                    },
                    timeout=3.5,
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

        if not cand.video_url and not cand.youtube_url:
            dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                client, f"{orig_title} series trailer", max_snapshots=4
            )
            if dm_url:
                cand.youtube_url = dm_url
            for st in dm_stills:
                cand.add_unique_snapshot(st)

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
        if "anime" in req_genres or "animation" in req_genres:
            target_tvmaze_genres.update({"anime", "animation"})
        if "nature" in req_genres:
            target_tvmaze_genres.update({"nature", "documentary"})

        req_decades = [d for d in criteria.decades if d in _SERIES_DECADES]
        results: List[MediaCandidate] = []

        async with create_async_http_client(timeout=5.5) as client:
            pages = random.sample(range(0, 180), k=6)
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
                        sh_type = str(sh.get("type") or "").strip().lower()
                        if sh_type in _EXCLUDED_TVMAZE_SHOW_TYPES:
                            seen_ids.add(sid)
                            continue
                        if sh_type == "sports" and "sports" not in req_genres:
                            seen_ids.add(sid)
                            continue
                        if not (sh.get("image") or {}).get("original") and not (sh.get("image") or {}).get("medium"):
                            continue
                        avg_rate = float((sh.get("rating") or {}).get("average") or 7.5)
                        if avg_rate < max(5.5, criteria.min_rating - 1.0):
                            continue

                        sh_genres = {str(x).lower() for x in (sh.get("genres") or [])}
                        if sh_type in ("animation", "documentary", "sports"):
                            sh_genres.add(sh_type)
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

        valid_genres: List[str] = []
        for g in criteria.genres or []:
            if not g or g.strip().lower() == "all":
                continue
            cg = resolve_canonical_genre("series", g)
            gk = cg if cg in SERIES_GENRES else g.strip().lower()
            if gk in SERIES_GENRES and gk not in valid_genres:
                valid_genres.append(gk)
        g_meta = SERIES_GENRES[valid_genres[0]] if valid_genres else SERIES_GENRES["scifi"]
        target_tvmaze_genres = {SERIES_GENRES[g]["tvmaze"].lower() for g in valid_genres}
        if "anime" in valid_genres or "animation" in valid_genres:
            target_tvmaze_genres.update({"anime", "animation"})
        if "nature" in valid_genres:
            target_tvmaze_genres.update({"nature", "documentary"})

        valid_decades = [
            d.strip().lower()
            for d in (criteria.decades or [])
            if d and d.strip().lower() in DECADE_BOUNDS
        ]

        try:
            async with create_async_http_client(timeout=5.5) as client:
                raw_shows: List[Dict[str, Any]] = []
                resp = await client.get(
                    "https://api.tvmaze.com/search/shows",
                    params={"q": q_clean},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    if isinstance(payload, list):
                        for entry in payload:
                            if isinstance(entry, dict):
                                sh = entry.get("show") if isinstance(entry.get("show"), dict) else entry
                                if isinstance(sh, dict) and sh.get("id"):
                                    raw_shows.append(sh)
                    elif isinstance(payload, dict) and payload.get("id"):
                        raw_shows.append(payload)

                if not raw_shows:
                    s_resp = await client.get(
                        "https://api.tvmaze.com/singlesearch/shows",
                        params={"q": q_clean},
                    )
                    if s_resp.status_code == 200 and isinstance(s_resp.json(), dict):
                        raw_shows.append(s_resp.json())

                filtered_shows: List[Dict[str, Any]] = []
                seen_ids: Set[str] = set()
                for idx, sh in enumerate(raw_shows):
                    if len(filtered_shows) >= target_limit:
                        break
                    sid = f"series_tvmaze_{sh.get('id')}"
                    if sid in seen_ids:
                        continue
                    img = sh.get("image") or {}
                    if not img.get("original") and not img.get("medium"):
                        if not (target_limit == 1 and idx == 0 and not filtered_shows):
                            continue

                    sh_type = str(sh.get("type") or "").strip().lower()
                    sh_genres = {str(x).lower() for x in (sh.get("genres") or [])}
                    if sh_type in ("animation", "documentary", "sports"):
                        sh_genres.add(sh_type)
                    if target_tvmaze_genres and not (sh_genres & target_tvmaze_genres):
                        continue

                    if valid_decades:
                        prem = str(sh.get("premiered") or "")[:4]
                        if not prem.isdigit():
                            continue
                        yr_val = int(prem)
                        if not any(
                            int(DECADE_BOUNDS[d][0][:4]) <= yr_val <= int(DECADE_BOUNDS[d][1][:4])
                            for d in valid_decades
                        ):
                            continue

                    seen_ids.add(sid)
                    filtered_shows.append(sh)

                if not filtered_shows:
                    return []

                sem = asyncio.Semaphore(4)

                async def _build_idx(i: int, show_obj: Dict[str, Any]) -> MediaCandidate:
                    async with sem:
                        return await self._enrich_show(
                            client,
                            show_obj,
                            short_lang,
                            g_meta,
                            enrich_heavy=(i < min(target_limit, 4)),
                        )

                built = await asyncio.gather(
                    *(_build_idx(i, s) for i, s in enumerate(filtered_shows)),
                    return_exceptions=True,
                )
                return [c for c in built if isinstance(c, MediaCandidate)][:target_limit]
        except Exception as exc:
            logger.debug("[TVMaze] Search error for '%s': %s", q_clean, exc)
        return []

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        cands = await self.search_candidates(
            query=query,
            criteria=FilterCriteria(domain="series", genres=[], decades=[], lang=lang),
            limit=1,
        )
        return cands[0] if cands else None
