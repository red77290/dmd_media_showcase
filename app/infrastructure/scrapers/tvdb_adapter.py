"""
Infrastructure Adapter: TheTVDB API v4 (`TvdbSeriesAdapter`).

Provides official multilingual metadata (French, Spanish, German, Italian, English, Japanese)
and direct trailers for TV Series, Anime, and Movies.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from app.config import settings
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.persistence.server_config_store import server_config_store
from app.infrastructure.scrapers.catalog_metadata import DECADE_BOUNDS, SERIES_GENRES
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_dailymotion_trailer_and_stills,
)

logger = logging.getLogger("dmd_showcase.scrapers.tvdb")

# ISO 639-1 to TVDB 3-letter language code mapping
TVDB_LANG_MAP = {
    "fr": "fra",
    "es": "spa",
    "de": "deu",
    "it": "ita",
    "en": "eng",
    "pt": "por",
    "ja": "jpn",
}

_tvdb_token_cache: Dict[str, Any] = {
    "api_key": "",
    "token": "",
    "expires_at": 0.0,
}


async def get_tvdb_auth_token(api_key: str, client: httpx.AsyncClient) -> Optional[str]:
    """Retrieve or return cached Bearer token from TheTVDB v4 login endpoint."""
    clean_key = (api_key or "").strip()
    if not clean_key:
        return None
    now = time.monotonic()
    if (
        _tvdb_token_cache["token"]
        and _tvdb_token_cache.get("api_key") == clean_key
        and now < _tvdb_token_cache["expires_at"]
    ):
        return str(_tvdb_token_cache["token"])

    try:
        resp = await client.post(
            "https://api4.thetvdb.com/v4/login",
            json={"apikey": clean_key},
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            token = (resp.json().get("data") or {}).get("token")
            if token:
                _tvdb_token_cache["api_key"] = clean_key
                _tvdb_token_cache["token"] = token
                _tvdb_token_cache["expires_at"] = now + 82800.0  # 23 hours cache
                return str(token)
    except Exception as exc:
        logger.debug("[TVDB] Login error: %s", exc)
    return None


async def fetch_tvdb_localized_overview(
    title: str,
    media_type: str = "series",
    lang: str = "fr",
    year: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Search TVDB by title and retrieve localized title, overview, and trailer URL in `lang`.
    Returns `(localized_title, localized_overview, trailer_url)`.
    """
    api_key = str(server_config_store.get_all().get("tvdb_api_key") or settings.tvdb_api_key or "").strip()
    if not api_key or not title.strip():
        return None, None, None

    lang_code = (lang or "fr").split("-")[0].lower()
    tvdb_lang = TVDB_LANG_MAP.get(lang_code, "fra" if lang_code == "fr" else "eng")

    async with create_async_http_client(timeout=5.0) as client:
        token = await get_tvdb_auth_token(api_key, client)
        if not token:
            return None, None, None

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        search_type = "series" if media_type in ("series", "anime", "tv") else "movie"
        params: Dict[str, str] = {
            "query": title.strip(),
            "type": search_type,
            "limit": "3",
        }
        if year and len(year) == 4 and year.isdigit():
            params["year"] = year

        try:
            search_resp = await client.get(
                "https://api4.thetvdb.com/v4/search", params=params, headers=headers
            )
            items = (search_resp.json().get("data") or []) if search_resp.status_code == 200 else []
            if not items and "year" in params:
                params_no_yr = {k: v for k, v in params.items() if k != "year"}
                search_resp2 = await client.get(
                    "https://api4.thetvdb.com/v4/search", params=params_no_yr, headers=headers
                )
                items = (search_resp2.json().get("data") or []) if search_resp2.status_code == 200 else []

            if not items:
                return None, None, None

            best = items[0]
            raw_id = str(best.get("tvdb_id") or best.get("id") or "").strip()
            tvdb_id = raw_id.split("-")[-1].strip()
            if not tvdb_id:
                return None, None, None

            # 1. Extract localized title & overview directly from `/v4/search` maps if present
            translations_map = best.get("translations") or {}
            overviews_map = best.get("overviews") or {}
            loc_title: Optional[str] = (
                str(translations_map.get(tvdb_lang)).strip()
                if isinstance(translations_map, dict) and translations_map.get(tvdb_lang)
                else None
            )
            raw_over: Optional[str] = (
                str(overviews_map.get(tvdb_lang)).strip()
                if isinstance(overviews_map, dict) and overviews_map.get(tvdb_lang)
                else None
            )
            loc_overview: Optional[str] = (
                clean_synopsis_text(raw_over, max_chars=320) if raw_over else None
            )

            # 2. Fallback to `/translations/{tvdb_lang}` endpoint if search hit lacked translation
            sub_path = "series" if search_type == "series" else "movies"
            if not loc_overview or not loc_title:
                trans_url = f"https://api4.thetvdb.com/v4/{sub_path}/{tvdb_id}/translations/{tvdb_lang}"
                trans_resp = await client.get(trans_url, headers=headers)
                if trans_resp.status_code == 200:
                    t_data = trans_resp.json().get("data") or {}
                    if t_data.get("name") and not loc_title:
                        loc_title = str(t_data["name"]).strip()
                    if t_data.get("overview") and not loc_overview:
                        loc_overview = clean_synopsis_text(t_data["overview"], max_chars=320)

            # 3. Fetch extended details for trailers (prioritize target language then English)
            ext_url = f"https://api4.thetvdb.com/v4/{sub_path}/{tvdb_id}/extended"
            ext_resp = await client.get(ext_url, headers=headers)
            trailer_url: Optional[str] = None
            if ext_resp.status_code == 200:
                ext_data = ext_resp.json().get("data") or {}
                trailers = ext_data.get("trailers") or []
                for tr in trailers:
                    u = str(tr.get("url") or "").strip()
                    tr_lang = str(tr.get("language") or "").lower()
                    if u.startswith("http") and tr_lang == tvdb_lang:
                        trailer_url = u
                        break
                if not trailer_url:
                    for tr in trailers:
                        u = str(tr.get("url") or "").strip()
                        if u.startswith("http"):
                            trailer_url = u
                            break

            return loc_title, loc_overview, trailer_url
        except Exception as exc:
            logger.debug("[TVDB] Fetch error for '%s': %s", title, exc)

    return None, None, None


class TvdbSeriesAdapter(IScraperSource):
    """
    TheTVDB v4 Scraper Source for TV Series (`tvdb_series`).
    Acts as a rich authenticated catalog & trailer source when `tvdb_api_key` is configured.
    """

    source_id: str = "tvdb_series"
    display_name: str = "TheTVDB"
    domain: DomainType = "series"
    role: str = "fallback"

    def _api_key(self) -> str:
        return str(server_config_store.get_all().get("tvdb_api_key") or settings.tvdb_api_key or "").strip()

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

    async def _build_tvdb_candidate(
        self,
        client: httpx.AsyncClient,
        headers: Dict[str, str],
        raw_item: Dict[str, Any],
        g_meta: Dict[str, Any],
        short_lang: str,
        tvdb_lang: str,
    ) -> Optional[MediaCandidate]:
        raw_id = str(raw_item.get("tvdb_id") or raw_item.get("id") or "").strip()
        tvdb_id = raw_id.split("-")[-1].strip()
        if not tvdb_id or not tvdb_id.isdigit():
            return None

        orig_title = str(raw_item.get("name") or "").strip()
        if not orig_title:
            return None

        poster_url = str(raw_item.get("image") or raw_item.get("image_url") or "").strip()
        if poster_url and not poster_url.startswith("http"):
            poster_url = f"https://artworks.thetvdb.com{poster_url}"

        first_aired = str(raw_item.get("firstAired") or raw_item.get("year") or "2015")
        year = first_aired[:4] if len(first_aired) >= 4 else "2015"

        # Fetch extended record for trailers, artworks, and translations
        loc_title = orig_title
        loc_overview = clean_synopsis_text(raw_item.get("overview"), max_chars=320)
        trailer_url: Optional[str] = None
        banner_url: Optional[str] = None
        snapshots: List[str] = []

        translations_map = raw_item.get("translations") or {}
        overviews_map = raw_item.get("overviews") or {}
        if isinstance(translations_map, dict) and translations_map.get(tvdb_lang):
            loc_title = str(translations_map[tvdb_lang]).strip()
        if isinstance(overviews_map, dict) and overviews_map.get(tvdb_lang):
            loc_overview = clean_synopsis_text(overviews_map[tvdb_lang], max_chars=320)

        try:
            if not loc_overview or short_lang != "en":
                t_resp = await client.get(
                    f"https://api4.thetvdb.com/v4/series/{tvdb_id}/translations/{tvdb_lang}",
                    headers=headers,
                )
                if t_resp.status_code == 200:
                    t_data = t_resp.json().get("data") or {}
                    if t_data.get("name"):
                        loc_title = str(t_data["name"]).strip()
                    if t_data.get("overview"):
                        loc_overview = clean_synopsis_text(t_data["overview"], max_chars=320)

            ext_resp = await client.get(
                f"https://api4.thetvdb.com/v4/series/{tvdb_id}/extended",
                headers=headers,
            )
            if ext_resp.status_code == 200:
                ext_data = ext_resp.json().get("data") or {}
                if not poster_url and ext_data.get("image"):
                    poster_url = str(ext_data["image"])
                for tr in ext_data.get("trailers") or []:
                    u = str(tr.get("url") or "").strip()
                    if u.startswith("http"):
                        trailer_url = u
                        if str(tr.get("language") or "").lower() == tvdb_lang:
                            break
                for art in (ext_data.get("artworks") or [])[:8]:
                    img_u = str(art.get("image") or "").strip()
                    if not img_u.startswith("http"):
                        continue
                    art_type = int(art.get("type") or 0)
                    if art_type == 1 and not banner_url:
                        banner_url = img_u
                    elif art_type in (3, 2) and len(snapshots) < 5:
                        snapshots.append(img_u)
        except Exception:
            pass

        if not poster_url:
            return None

        cand = MediaCandidate(
            id=f"series_tvdb_{tvdb_id}",
            domain="series",
            title=loc_title,
            year=year,
            badge=g_meta["label"][:8].upper(),
            badge_color=g_meta["color"],
            subtitle=f"{g_meta['label']} • {year}"[:28],
            rating="8.2",
            synopsis=loc_overview or f"{loc_title} ({year}).",
            lang=short_lang,
            poster_url=poster_url,
            jacket_url=poster_url,
            marquee_url=banner_url or poster_url,
            backdrop_url=snapshots[0] if snapshots else banner_url,
            youtube_url=trailer_url,
            source_id=self.source_id,
            theme_palette=g_meta["palette"],
        )
        for snap in snapshots:
            cand.add_unique_snapshot(snap)

        # Direct iTunes .m4v episode clip or Dailymotion trailer fallback if TVDB had no trailer
        if not cand.video_url:
            try:
                it_resp = await client.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": orig_title,
                        "media": "tvShow",
                        "entity": "tvEpisode",
                        "country": "US",
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
            except Exception:
                pass

        if not cand.video_url and not cand.youtube_url:
            dm_url, dm_stills = await fetch_dailymotion_trailer_and_stills(
                client, f"{orig_title} series trailer", max_snapshots=3
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
        api_key = self._api_key()
        if not api_key:
            return []

        short_lang = (criteria.lang or "fr").split("-")[0].lower()
        tvdb_lang = TVDB_LANG_MAP.get(short_lang, "fra" if short_lang == "fr" else "eng")

        genre_keys = [g.lower().strip() for g in criteria.genres if g.lower().strip() in SERIES_GENRES]
        chosen_genre_key = random.choice(genre_keys) if genre_keys else "scifi"
        g_meta = SERIES_GENRES[chosen_genre_key]

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=5.5) as client:
            token = await get_tvdb_auth_token(api_key, client)
            if not token:
                return []
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            for _ in range(max(2, count * 2)):
                if len(results) >= count:
                    break
                try:
                    resp = await client.get(
                        "https://api4.thetvdb.com/v4/series",
                        params={"page": str(random.randint(0, 15))},
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        continue
                    items = resp.json().get("data") or []
                    random.shuffle(items)
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        raw_id = str(item.get("id") or "").split("-")[-1]
                        if not raw_id or f"series_tvdb_{raw_id}" in seen_ids:
                            continue
                        cand = await self._build_tvdb_candidate(
                            client, headers, item, g_meta, short_lang, tvdb_lang
                        )
                        if cand:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    logger.debug("[TVDB Series] Discover error: %s", exc)

        return results

    async def search_by_query(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[MediaCandidate]:
        api_key = self._api_key()
        if not api_key or not query.strip():
            return None
        short_lang = (lang or "fr").split("-")[0].lower()
        tvdb_lang = TVDB_LANG_MAP.get(short_lang, "fra" if short_lang == "fr" else "eng")

        async with create_async_http_client(timeout=5.0) as client:
            token = await get_tvdb_auth_token(api_key, client)
            if not token:
                return None
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            try:
                search_resp = await client.get(
                    "https://api4.thetvdb.com/v4/search",
                    params={"query": query.strip(), "type": "series", "limit": "3"},
                    headers=headers,
                )
                if search_resp.status_code == 200:
                    items = search_resp.json().get("data") or []
                    for item in items:
                        if isinstance(item, dict):
                            cand = await self._build_tvdb_candidate(
                                client, headers, item, SERIES_GENRES["scifi"], short_lang, tvdb_lang
                            )
                            if cand:
                                return cand
            except Exception:
                pass
        return None

