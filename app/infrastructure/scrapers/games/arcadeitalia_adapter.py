"""
Fallback Games Scraper Adapter: ArcadeItalia ADB (`adb.arcadeitalia.net`).

Keyless public MAME / Arcade database adapter implementing `IScraperSource`:
- Zero static ROM seed lists: uses uniform random year (`1978..2004`) and dynamic 2-character
  title prefix pagination across the entire 30,000+ MAME catalog.
- Provides `history.dat` synopses, high-res flyers, cabinet marquees, in-game snapshots,
  direct `url_video_shortplay` MP4 gameplay videos, and `youtube_id` HD gameplay fallbacks.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.catalog_metadata import GAME_GENRES, SYSTEM_METADATA
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_wikipedia_localized_synopsis,
)

logger = logging.getLogger("dmd_showcase.scrapers.games.arcadeitalia")

_VOWELS = "aeiouy"
_CONSONANTS = "bcdfghjklmnpqrstvwxz"
_DECADE_YEARS: Dict[str, range] = {
    "1970s": range(1975, 1980),
    "1980s": range(1980, 1990),
    "1990s": range(1990, 2000),
    "2000s": range(2000, 2006),
}


def _random_syllable_query() -> str:
    """Generate a uniform 2-letter syllable to sample ArcadeItalia's `title` index uniformly."""
    if random.random() < 0.65:
        return random.choice(_CONSONANTS) + random.choice(_VOWELS)
    return random.choice(_VOWELS) + random.choice(_CONSONANTS)


class ArcadeItaliaAdapter(IScraperSource):
    """
    Keyless Fallback `IScraperSource` for `games` querying `adb.arcadeitalia.net`.
    """

    source_id: str = "arcadeitalia"
    domain: DomainType = "games"
    role: str = "fallback"

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "systems": [
                {"id": sys_id, "label": f"{meta['badge']} ({meta['label']})"}
                for sys_id, meta in SYSTEM_METADATA.items()
            ],
            "genres": [{"id": gid, "label": label} for gid, label in GAME_GENRES.items()],
            "decades": [
                {"id": "1970s", "label": "1970s"},
                {"id": "1980s", "label": "1980s"},
                {"id": "1990s", "label": "1990s"},
                {"id": "2000s", "label": "2000s"},
            ],
        }

    async def _build_candidate(
        self,
        client: Any,
        info: Dict[str, Any],
        sys_key: str,
        lang: str,
    ) -> Optional[MediaCandidate]:
        rom = str(info.get("game_name") or "").strip()
        if not rom:
            return None
        raw_title = str(info.get("title") or rom).strip()
        clean_title = re.sub(r"\s*\([^)]*\)\s*$", "", raw_title).strip() or raw_title
        year = str(info.get("year") or "1991")[:4]
        genre = str(info.get("genre") or "Arcade").split("/")[0].strip()
        manufacturer = str(info.get("manufacturer") or "Arcade").strip()
        rate = info.get("rate")
        rating_str = f"{float(rate) / 10.0:.1f}" if rate and str(rate).isdigit() else "8.5"

        sys_meta = SYSTEM_METADATA.get(sys_key, SYSTEM_METADATA["arcade"])
        history = clean_synopsis_text(info.get("history"), max_chars=300)

        cand = MediaCandidate(
            id=f"games_adb_{rom}",
            domain="games",
            title=clean_title,
            year=year,
            badge=sys_meta["badge"],
            badge_color=sys_meta["color"],
            subtitle=f"{genre} • {manufacturer}"[:28],
            rating=rating_str,
            synopsis=history or f"{clean_title} ({year}) — {genre} arcade classic by {manufacturer}.",
            lang=lang,
            source_id=self.source_id,
            theme_palette=sys_meta["palette"],
            extra_meta={"system": sys_key, "mame_rom": rom},
        )

        flyer = info.get("url_image_flyer") or info.get("url_image_cabinet")
        title_img = info.get("url_image_title")
        marquee_img = info.get("url_image_marquee") or title_img

        if flyer:
            cand.poster_url = str(flyer)
            cand.jacket_url = str(flyer)
        elif title_img:
            cand.poster_url = str(title_img)
            cand.jacket_url = str(title_img)

        if marquee_img:
            cand.marquee_url = str(marquee_img)

        for key in (
            "url_image_ingame",
            "url_image_boss",
            "url_image_title",
            "url_image_select",
            "url_image_flyer",
        ):
            val = info.get(key)
            if val:
                cand.add_unique_snapshot(str(val))

        shortplay = info.get("url_video_shortplay")
        if shortplay:
            cand.video_url = str(shortplay)

        yt_id = str(info.get("youtube_id") or "").strip()
        if yt_id:
            cand.youtube_url = f"https://www.youtube.com/watch?v={yt_id}"

        if not cand.poster_url and not cand.snapshot_urls:
            return None

        if (not history or lang != "en") and client is not None:
            wiki_syn, wiki_thumb = await fetch_wikipedia_localized_synopsis(
                client, clean_title, lang=lang, domain_hint="video game"
            )
            if wiki_syn:
                cand.synopsis = wiki_syn
            if wiki_thumb and not cand.poster_url:
                cand.poster_url = wiki_thumb
                cand.jacket_url = wiki_thumb

        return cand

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        valid_systems = [
            s.lower().strip()
            for s in criteria.systems
            if s.lower().strip() in SYSTEM_METADATA
        ] or ["arcade", "mame", "neogeo", "cps1", "cps2"]

        decade_years: List[int] = []
        for dec in criteria.decades:
            if dec in _DECADE_YEARS:
                decade_years.extend(_DECADE_YEARS[dec])
        if not decade_years:
            decade_years = list(range(1980, 2003))

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=5.5) as client:
            for _ in range(max(5, count * 3)):
                if len(results) >= count:
                    break
                sys_key = random.choice(valid_systems)
                query_token = _random_syllable_query()
                target_year = str(random.choice(decade_years))
                params: Dict[str, str] = {
                    "ajax": "query_mame",
                    "title": query_token,
                    "year": target_year,
                }
                try:
                    resp = await client.get(
                        "http://adb.arcadeitalia.net/service_scraper.php",
                        params=params,
                    )
                    rows = (resp.json().get("result") or []) if resp.status_code == 200 else []
                    if not rows:
                        # Retry without year restriction if syllable+year intersection was empty
                        resp = await client.get(
                            "http://adb.arcadeitalia.net/service_scraper.php",
                            params={"ajax": "query_mame", "title": query_token},
                        )
                        rows = (resp.json().get("result") or []) if resp.status_code == 200 else []

                    random.shuffle(rows)
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        rom = str(row.get("game_name") or "").strip()
                        cand_id = f"games_adb_{rom}"
                        if not rom or cand_id in seen_ids:
                            continue
                        # Skip BIOS / fruit machines / non-playable clones without screenshots
                        if not row.get("url_image_ingame") and not row.get("url_image_flyer"):
                            continue
                        cand = await self._build_candidate(client, row, sys_key, criteria.lang)
                        if cand:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    logger.debug("[ArcadeItalia] Random catalog query error: %s", exc)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        q_clean = query.strip()
        if not q_clean:
            return None
        try:
            async with create_async_http_client(timeout=5.0) as client:
                for param_key in ("game_name", "title"):
                    resp = await client.get(
                        "http://adb.arcadeitalia.net/service_scraper.php",
                        params={"ajax": "query_mame", param_key: q_clean},
                    )
                    if resp.status_code == 200:
                        rows = resp.json().get("result") or []
                        for row in rows:
                            if isinstance(row, dict):
                                cand = await self._build_candidate(client, row, "arcade", lang)
                                if cand:
                                    return cand
        except Exception:
            pass
        return None
