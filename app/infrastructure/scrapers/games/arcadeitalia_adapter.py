"""
Fallback Games Scraper Adapter: ArcadeItalia ADB (`https://adb.arcadeitalia.net`) + `LibretroWikidataAdapter`.

Keyless public MAME / Arcade & Retro Console adapter implementing `IScraperSource`:
- For Arcade systems (`arcade`, `mame`, `neogeo`, `cps1`, `cps2`, `cps3`, `fbneo`):
  Queries `https://adb.arcadeitalia.net/service_scraper.php?ajax=query_mame&game_name=...`
  strictly over HTTPS using valid MAME romset identifiers harvested by
  `arcadeitalia_referential_strategy` (zero HTTP 301 redirects, zero HTTP 400 Bad Request).
- For Console, Handheld & Home Computer systems (`nes`, `snes`, `megadrive`, `psx`, `n64`, etc.):
  Delegates to `LibretroWikidataAdapter` (`libretro_wikidata_referential_strategy`), which
  queries Wikidata SPARQL strictly for Video Games (`wd:Q7889`) on that exact console + genre
  and matches the verified GitHub file tree of `libretro-thumbnails/{repo}` with regional
  `(France) -> (Europe) -> (World) -> (USA)` priority.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.referential_strategy import IScraperReferentialStrategy
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import (
    GAME_GENRE_CATALOG,
    RETRO_SYSTEM_CATALOG,
    ReferentialConsolidator,
    is_arcade_system,
    localize_label,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.scrapers.games.libretro_wikidata_adapter import LibretroWikidataAdapter
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_wikipedia_localized_synopsis,
)
from app.infrastructure.scrapers.referential_store import arcadeitalia_referential_strategy

logger = logging.getLogger("dmd_showcase.scrapers.games.arcadeitalia")

ADB_HTTPS_URL = "https://adb.arcadeitalia.net/service_scraper.php"


class ArcadeItaliaAdapter(IScraperSource):
    """
    Keyless Fallback `IScraperSource` for `games`:
    - Arcade/MAME via `https://adb.arcadeitalia.net` (`arcadeitalia_referential_strategy`)
    - Home Consoles/Handhelds/Computers via `LibretroWikidataAdapter` (`libretro_wikidata_referential_strategy`)
    """

    source_id: str = "arcadeitalia"
    domain: DomainType = "games"
    role: str = "fallback"

    def __init__(
        self,
        referential_strategy: Optional[IScraperReferentialStrategy] = None,
        console_adapter: Optional[LibretroWikidataAdapter] = None,
    ) -> None:
        self.referential_strategy = referential_strategy or arcadeitalia_referential_strategy
        self.console_adapter = console_adapter or LibretroWikidataAdapter()

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        ref = ReferentialConsolidator.build_domain_referential("games", lang=lang)
        return {
            "systems": [{"id": s["id"], "label": s["label"]} for s in ref["systems"]],
            "genres": [{"id": g["id"], "label": g["label"]} for g in ref["genres"]],
            "decades": [{"id": d["id"], "label": d["label"]} for d in ref["decades"]],
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
        raw_genre = str(info.get("genre") or "Arcade").split("/")[0].strip()
        canon_genre = resolve_canonical_genre("games", raw_genre)
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        gmeta = GAME_GENRE_CATALOG.get(canon_genre)
        genre_label = localize_label(gmeta["labels"], short_lang, raw_genre) if gmeta else raw_genre
        manufacturer = str(info.get("manufacturer") or "Arcade").strip()
        rate = info.get("rate")
        rating_str = f"{float(rate) / 10.0:.1f}" if rate and str(rate).isdigit() else "8.8"

        canon_sys = resolve_canonical_system(sys_key)
        sys_meta = RETRO_SYSTEM_CATALOG.get(canon_sys, RETRO_SYSTEM_CATALOG["arcade"])
        history = clean_synopsis_text(info.get("history"), max_chars=300)

        cand = MediaCandidate(
            id=f"games_adb_{rom}",
            domain="games",
            title=clean_title,
            year=year,
            badge=sys_meta["badge"],
            badge_color=sys_meta["color"],
            subtitle=f"{genre_label} • {manufacturer}"[:28],
            rating=rating_str,
            synopsis=history or f"{clean_title} ({year}) — {genre_label} ({manufacturer}).",
            lang=short_lang,
            source_id=self.source_id,
            theme_palette=tuple(sys_meta["palette"]),
            extra_meta={"system": canon_sys, "mame_rom": rom, "genre": canon_genre},
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

        if (not history or short_lang != "en") and client is not None:
            wiki_syn, wiki_thumb = await fetch_wikipedia_localized_synopsis(
                client,
                clean_title,
                lang=short_lang,
                domain_hint="jeu vidéo" if short_lang == "fr" else "video game",
            )
            if wiki_syn:
                cand.synopsis = wiki_syn
            if wiki_thumb and not cand.poster_url:
                cand.poster_url = wiki_thumb
                cand.jacket_url = wiki_thumb

        return cand

    async def _query_adb_roms(self, client: Any, rom_names: List[str]) -> List[Dict[str, Any]]:
        """Query ArcadeItalia ADB (`ajax=query_mame&game_name=...`) strictly over HTTPS."""
        clean_roms = [r.strip() for r in rom_names if r and r.strip()]
        if not clean_roms:
            return []
        params = {
            "ajax": "query_mame",
            "lang": "en",
            "game_name": ";".join(clean_roms),
        }
        try:
            resp = await client.get(ADB_HTTPS_URL, params=params)
            if resp.status_code == 200:
                rows = resp.json().get("result") or []
                if isinstance(rows, list) and rows:
                    return [r for r in rows if isinstance(r, dict)]
        except Exception as exc:
            logger.debug("[ArcadeItalia] HTTPS query '%s' failed: %s", params["game_name"], exc)
        return []

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        req_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.lower() != "all" and resolve_canonical_system(s) in RETRO_SYSTEM_CATALOG
        ]
        if not req_systems:
            req_systems = ["arcade", "mame", "neogeo", "cps1", "cps2", "snes", "nes", "megadrive"]

        arcade_systems = [s for s in req_systems if is_arcade_system(s)]
        console_systems = [s for s in req_systems if not is_arcade_system(s)]

        # If the user exclusively requested console/handheld/computer systems (e.g. NES),
        # delegate 100% to LibretroWikidataAdapter without ever calling ArcadeItalia!
        if console_systems and not arcade_systems:
            return await self.console_adapter.fetch_random_unseen(
                criteria=criteria,
                seen_ids=seen_ids,
                count=count,
            )

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=6.0) as client:
            for _ in range(max(3, count * 2)):
                if len(results) >= count:
                    break
                sys_key = random.choice(req_systems)
                if not is_arcade_system(sys_key):
                    console_criteria = FilterCriteria(
                        domain="games",
                        systems=[sys_key],
                        genres=list(criteria.genres),
                        decades=list(criteria.decades),
                        lang=criteria.lang,
                    )
                    c_batch = await self.console_adapter.fetch_random_unseen(
                        criteria=console_criteria,
                        seen_ids=seen_ids,
                        count=count - len(results),
                    )
                    results.extend(c_batch)
                    continue

                picked_roms = await self.referential_strategy.sample_unseen_tokens(
                    client,
                    criteria=criteria,
                    seen_ids=seen_ids,
                    count=max(3, (count - len(results)) * 3),
                )
                if picked_roms:
                    rows = await self._query_adb_roms(client, picked_roms)
                    random.shuffle(rows)
                    req_genres = {
                        resolve_canonical_genre("games", g)
                        for g in (criteria.genres or [])
                        if g and g.lower() != "all"
                    }
                    if req_genres:
                        rows.sort(
                            key=lambda r: (
                                0
                                if resolve_canonical_genre(
                                    "games", str(r.get("genre") or "").split("/")[0].strip()
                                )
                                in req_genres
                                else 1
                            )
                        )
                    for row in rows:
                        rom = str(row.get("game_name") or "").strip()
                        cand_id = f"games_adb_{rom}"
                        if not rom or cand_id in seen_ids:
                            continue
                        if (
                            not row.get("url_image_ingame")
                            and not row.get("url_image_flyer")
                            and not row.get("url_image_title")
                        ):
                            continue
                        cand = await self._build_candidate(client, row, sys_key, criteria.lang)
                        if cand:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break

        if len(results) < count:
            fallback_batch = await self.console_adapter.fetch_random_unseen(
                criteria=criteria,
                seen_ids=seen_ids,
                count=count - len(results),
            )
            results.extend(fallback_batch)

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
            return await self.console_adapter.search_by_query(q_clean, lang=lang)
        except Exception:
            pass
        return None
