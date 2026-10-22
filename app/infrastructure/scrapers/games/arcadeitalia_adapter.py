"""
Fallback Games Scraper Adapter: ArcadeItalia ADB (`adb.arcadeitalia.net`) + Live Wikipedia/Libretro.

Keyless public MAME / Arcade & Retro Console adapter implementing `IScraperSource`:
- Queries `https://adb.arcadeitalia.net/service_scraper.php?ajax=query_mame&game_name=...`
  using valid MAME romset identifiers (preventing HTTP 301 redirects and HTTP 400 Bad Request).
- Provides `history.dat` synopses, high-res flyers, cabinet marquees, in-game snapshots,
  direct `url_video_shortplay` MP4 gameplay videos, and `youtube_id` / `ytsearch1:` HD gameplay fallbacks.
- Supports all 28 Arcade, Console, Handheld, and Home Computer systems via live Wikipedia
  MediaWiki Search API + Libretro Thumbnails CDN (`Named_Boxarts`, `Named_Titles`, `Named_Snaps`).
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.referential_strategy import IScraperReferentialStrategy
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.catalog_metadata import (
    GAME_GENRES,
    SYSTEM_METADATA,
)
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_wikipedia_localized_synopsis,
)
from app.infrastructure.scrapers.referential_store import arcadeitalia_referential_strategy

logger = logging.getLogger("dmd_showcase.scrapers.games.arcadeitalia")

ADB_HTTPS_URL = "https://adb.arcadeitalia.net/service_scraper.php"
ADB_HTTP_URL = "http://adb.arcadeitalia.net/service_scraper.php"


class ArcadeItaliaAdapter(IScraperSource):
    """
    Keyless Fallback `IScraperSource` for `games` querying `adb.arcadeitalia.net` (Arcade/MAME)
    via its `IScraperReferentialStrategy` (`arcadeitalia_referential_strategy`, zero hardcoded ROMs)
    and Live Wikipedia + Libretro CDN (Consoles/Handhelds/Computers & dynamic fallback).
    """

    source_id: str = "arcadeitalia"
    domain: DomainType = "games"
    role: str = "fallback"

    def __init__(self, referential_strategy: Optional[IScraperReferentialStrategy] = None) -> None:
        self.referential_strategy = referential_strategy or arcadeitalia_referential_strategy

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
        rating_str = f"{float(rate) / 10.0:.1f}" if rate and str(rate).isdigit() else "8.8"

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
        elif not cand.video_url:
            cand.youtube_url = f"ytsearch1:{clean_title} {sys_meta['badge']} arcade gameplay"

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

    async def _query_adb_roms(self, client: Any, rom_names: List[str]) -> List[Dict[str, Any]]:
        """Query ArcadeItalia ADB (`ajax=query_mame&game_name=...`) over HTTPS with HTTP fallback."""
        clean_roms = [r.strip() for r in rom_names if r and r.strip()]
        if not clean_roms:
            return []
        params = {
            "ajax": "query_mame",
            "lang": "en",
            "game_name": ",".join(clean_roms),
        }
        for url in (ADB_HTTPS_URL, ADB_HTTP_URL):
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    rows = resp.json().get("result") or []
                    if isinstance(rows, list) and rows:
                        return [r for r in rows if isinstance(r, dict)]
            except Exception as exc:
                logger.debug("[ArcadeItalia] Query '%s' on %s failed: %s", params["game_name"], url, exc)
        return []

    async def _discover_wikipedia_libretro_games(
        self,
        client: Any,
        sys_key: str,
        genre_key: str,
        decades: List[str],
        lang: str,
        seen_ids: Set[str],
        count: int,
    ) -> List[MediaCandidate]:
        """
        Live discovery of retro console/computer/arcade games via Wikipedia MediaWiki
        Search API + Libretro Thumbnails CDN (`Named_Boxarts`, `Named_Titles`, `Named_Snaps`)
        + YouTube HD gameplay resolution (`ytsearch1:`).
        """
        sys_meta = SYSTEM_METADATA.get(sys_key, SYSTEM_METADATA["snes"])
        genre_label = GAME_GENRES.get(genre_key, "")
        decade_hint = decades[0] if decades else ""
        search_query = f"{sys_meta.get('search_hint', sys_meta['label'])} {genre_label} {decade_hint}".strip()
        results: List[MediaCandidate] = []

        try:
            offset = random.randint(0, 30)
            resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": f"{search_query} incategory:Video_games",
                    "srlimit": str(max(12, count * 4)),
                    "sroffset": str(offset),
                    "format": "json",
                },
            )
            search_items = (
                (resp.json().get("query") or {}).get("search") or []
                if resp.status_code == 200
                else []
            )
            if not search_items:
                resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": str(sys_meta.get("search_hint", sys_meta["label"])),
                        "srlimit": "20",
                        "format": "json",
                    },
                )
                search_items = (
                    (resp.json().get("query") or {}).get("search") or []
                    if resp.status_code == 200
                    else []
                )

            random.shuffle(search_items)
            for item in search_items:
                if len(results) >= count:
                    break
                raw_title = str(item.get("title") or "").strip()
                if not raw_title or raw_title.startswith(("List of", "Category:", "History of")):
                    continue
                clean_title = re.sub(r"\s*\([^)]*video game[^)]*\)\s*$", "", raw_title, flags=re.I).strip()
                clean_title = re.sub(r"\s*\([^)]*game[^)]*\)\s*$", "", clean_title, flags=re.I).strip()
                slug = re.sub(r"[^a-z0-9]+", "_", clean_title.lower()).strip("_")
                cand_id = f"games_libretro_{sys_key}_{slug}"
                if not slug or cand_id in seen_ids:
                    continue

                snippet = clean_synopsis_text(item.get("snippet"), max_chars=280)
                year_match = re.search(r"\b(19[789]\d|200\d)\b", snippet)
                year_str = year_match.group(1) if year_match else (decade_hint[:4] if decade_hint[:4].isdigit() else "1993")

                wiki_syn, wiki_thumb = await fetch_wikipedia_localized_synopsis(
                    client, raw_title, lang=lang
                )
                synopsis = wiki_syn or snippet or f"{clean_title} ({year_str}) on {sys_meta['label']}."

                lib_sys = quote(str(sys_meta.get("libretro", "MAME")), safe="_-")
                lib_title = quote(clean_title, safe="")
                base_cdn = f"https://raw.githubusercontent.com/libretro-thumbnails/{lib_sys}/master"
                boxart_url = f"{base_cdn}/Named_Boxarts/{lib_title}.png"
                title_url = f"{base_cdn}/Named_Titles/{lib_title}.png"
                snap_url = f"{base_cdn}/Named_Snaps/{lib_title}.png"

                poster_url = wiki_thumb or boxart_url
                cand = MediaCandidate(
                    id=cand_id,
                    domain="games",
                    title=clean_title,
                    year=year_str,
                    badge=sys_meta["badge"],
                    badge_color=sys_meta["color"],
                    subtitle=f"{genre_label or 'Retro'} • {sys_meta['badge']}"[:28],
                    rating="8.7",
                    synopsis=synopsis,
                    lang=lang,
                    poster_url=poster_url,
                    jacket_url=poster_url,
                    marquee_url=title_url,
                    youtube_url=f"ytsearch1:{clean_title} {sys_meta['badge']} gameplay",
                    source_id=self.source_id,
                    theme_palette=sys_meta["palette"],
                    extra_meta={"system": sys_key},
                )
                cand.add_unique_snapshot(snap_url)
                if wiki_thumb:
                    cand.add_unique_snapshot(wiki_thumb)
                seen_ids.add(cand.id)
                results.append(cand)
        except Exception as exc:
            logger.debug("[ArcadeItalia] Wikipedia/Libretro discovery error: %s", exc)

        return results

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
        ] or ["arcade", "mame", "neogeo", "cps1", "cps2", "snes", "megadrive"]

        valid_genres = [
            g.lower().strip()
            for g in criteria.genres
            if g.lower().strip() in GAME_GENRES
        ]
        chosen_genre = random.choice(valid_genres) if valid_genres else "action"

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=6.0) as client:
            for _ in range(max(4, count * 3)):
                if len(results) >= count:
                    break
                sys_key = random.choice(valid_systems)
                sys_meta = SYSTEM_METADATA.get(sys_key, SYSTEM_METADATA["arcade"])

                if sys_meta.get("is_arcade"):
                    picked_roms = await self.referential_strategy.sample_unseen_tokens(
                        client,
                        criteria=criteria,
                        seen_ids=seen_ids,
                        count=max(3, (count - len(results)) * 3),
                    )
                    if picked_roms:
                        rows = await self._query_adb_roms(client, picked_roms)
                        random.shuffle(rows)
                        for row in rows:
                            rom = str(row.get("game_name") or "").strip()
                            cand_id = f"games_adb_{rom}"
                            if not rom or cand_id in seen_ids:
                                continue
                            if not row.get("url_image_ingame") and not row.get("url_image_flyer") and not row.get("url_image_title"):
                                continue
                            cand = await self._build_candidate(client, row, sys_key, criteria.lang)
                            if cand:
                                seen_ids.add(cand.id)
                                results.append(cand)
                                if len(results) >= count:
                                    break
                        if len(results) >= count:
                            break

                # Console/Computer system OR fallback when MAME ROM query was empty/exhausted
                needed = count - len(results)
                wiki_cands = await self._discover_wikipedia_libretro_games(
                    client,
                    sys_key=sys_key,
                    genre_key=chosen_genre,
                    decades=criteria.decades,
                    lang=criteria.lang,
                    seen_ids=seen_ids,
                    count=needed,
                )
                results.extend(wiki_cands)

        return results[:count]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        q_clean = query.strip()
        if not q_clean:
            return None
        try:
            async with create_async_http_client(timeout=5.5) as client:
                rom_slug = re.sub(r"[^a-z0-9]+", "", q_clean.lower())
                if rom_slug:
                    rows = await self._query_adb_roms(client, [rom_slug])
                    for row in rows:
                        cand = await self._build_candidate(client, row, "arcade", lang)
                        if cand:
                            return cand
                wiki_cands = await self._discover_wikipedia_libretro_games(
                    client,
                    sys_key="arcade",
                    genre_key="action",
                    decades=[],
                    lang=lang,
                    seen_ids=set(),
                    count=1,
                )
                if wiki_cands:
                    return wiki_cands[0]
        except Exception:
            pass
        return None
