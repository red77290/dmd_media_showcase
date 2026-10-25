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
    CANONICAL_DECADES,
    GAME_GENRE_CATALOG,
    RETRO_SYSTEM_CATALOG,
    ReferentialConsolidator,
    build_game_fingerprint,
    is_arcade_system,
    is_disallowed_non_fighting_title,
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
from app.infrastructure.scrapers.referential_store import (
    ArcadeItaliaReferentialStrategy,
    arcadeitalia_referential_strategy,
)

logger = logging.getLogger("dmd_showcase.scrapers.games.arcadeitalia")

_SYS_RNG = random.SystemRandom()

ADB_HTTPS_URL = "https://adb.arcadeitalia.net/service_scraper.php"

_NON_CPS_TITLES = (
    "marvel vs. capcom 2",
    "marvel vs capcom 2",
    "capcom vs. snk",
    "capcom vs snk",
)


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

    @staticmethod
    def _detect_hardware_system_from_row(row: Dict[str, Any], default_sys: str) -> str:
        """Resolve exact hardware system (`cps1`, `cps2`, `cps3`, `neogeo`) from ADB row driver or ROM."""
        canon = resolve_canonical_system(default_sys)
        if canon not in ("arcade", "mame", "fbneo"):
            return canon
        sourcefile = str(row.get("sourcefile") or row.get("driver") or "").strip().lower()
        rom = str(row.get("game_name") or "").strip().lower()
        cloneof = str(row.get("cloneof") or "").strip().lower()
        if "cps3" in sourcefile or rom in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["cps3"] or cloneof in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["cps3"]:
            return "cps3"
        if "cps2" in sourcefile or rom in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["cps2"] or cloneof in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["cps2"]:
            return "cps2"
        if "cps1" in sourcefile or rom in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["cps1"] or cloneof in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["cps1"]:
            return "cps1"
        if "neogeo" in sourcefile or rom in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["neogeo"] or cloneof in ArcadeItaliaReferentialStrategy._AUTHORITATIVE_SYSTEM_ROMS["neogeo"]:
            return "neogeo"
        return canon

    @staticmethod
    def _is_row_valid_for_system(row: Dict[str, Any], sys_key: str) -> bool:
        """Strictly verify that an ArcadeItalia MAME row belongs to the requested hardware system."""
        canon_sys = resolve_canonical_system(sys_key)
        rom = str(row.get("game_name") or "").strip().lower()
        cloneof = str(row.get("cloneof") or "").strip().lower()
        if not ArcadeItaliaReferentialStrategy.is_rom_allowed_for_system(rom, canon_sys):
            return False
        if cloneof and not ArcadeItaliaReferentialStrategy.is_rom_allowed_for_system(cloneof, canon_sys):
            return False

        title_low = str(row.get("title") or "").strip().lower()
        sourcefile = str(row.get("sourcefile") or row.get("driver") or "").strip().lower()

        if canon_sys in ("cps1", "cps2", "cps3"):
            if any(bad in title_low for bad in _NON_CPS_TITLES):
                return False
            if sourcefile:
                if "naomi" in sourcefile or "neogeo" in sourcefile or "zn" in sourcefile or "dc" in sourcefile:
                    return False
                if canon_sys == "cps1" and "cps1" not in sourcefile and ("cps2" in sourcefile or "cps3" in sourcefile or ".cpp" in sourcefile or ".c" in sourcefile):
                    return False
                if canon_sys == "cps2" and "cps2" not in sourcefile and ("cps1" in sourcefile or "cps3" in sourcefile or ".cpp" in sourcefile or ".c" in sourcefile):
                    return False
                if canon_sys == "cps3" and "cps3" not in sourcefile and ("cps1" in sourcefile or "cps2" in sourcefile or ".cpp" in sourcefile or ".c" in sourcefile):
                    return False
        elif canon_sys == "neogeo":
            if sourcefile and "neogeo" not in sourcefile and "neo" not in sourcefile:
                return False
        return True

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
        if not self._is_row_valid_for_system(info, sys_key):
            return None
        raw_title = str(info.get("title") or rom).strip()
        clean_title = re.sub(r"\s*\([^)]*\)\s*$", "", raw_title).strip() or raw_title
        year = str(info.get("year") or "1991")[:4]
        full_genre = str(info.get("genre") or "Arcade").strip()
        raw_genre = full_genre.split("/")[0].strip()
        canon_genre = resolve_canonical_genre("games", full_genre)
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        gmeta = GAME_GENRE_CATALOG.get(canon_genre)
        genre_label = localize_label(gmeta["labels"], short_lang, raw_genre) if gmeta else raw_genre
        manufacturer = str(info.get("manufacturer") or "Arcade").strip()
        rate = info.get("rate")
        rating_str = f"{float(rate) / 10.0:.1f}" if rate and str(rate).isdigit() else "8.8"

        canon_sys = self._detect_hardware_system_from_row(info, sys_key)
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
        req_decs_clean = [
            d.strip().lower()
            for d in (criteria.decades or [])
            if d and d.strip().lower() != "all"
        ]
        async with create_async_http_client(timeout=6.0) as client:
            for _ in range(max(3, count * 2)):
                if len(results) >= count:
                    break
                sys_key = _SYS_RNG.choice(req_systems)
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

                sys_criteria = FilterCriteria(
                    domain="games",
                    systems=[sys_key],
                    genres=list(criteria.genres),
                    decades=list(criteria.decades),
                    lang=criteria.lang,
                )
                picked_roms = await self.referential_strategy.sample_unseen_tokens(
                    client,
                    criteria=sys_criteria,
                    seen_ids=seen_ids,
                    count=max(8, (count - len(results)) * 6),
                )
                if picked_roms:
                    rows = await self._query_adb_roms(client, picked_roms)
                    # If any returned row is a clone without video, also fetch its parent ROM
                    parent_roms_to_fetch = [
                        str(r.get("cloneof") or "").strip()
                        for r in rows
                        if r.get("cloneof")
                        and not r.get("url_video_shortplay")
                        and f"games_adb_{str(r.get('cloneof') or '').strip()}" not in seen_ids
                    ]
                    if parent_roms_to_fetch:
                        parent_rows = await self._query_adb_roms(client, list(dict.fromkeys(parent_roms_to_fetch))[:8])
                        rows = parent_rows + rows
                    _SYS_RNG.shuffle(rows)
                    # Prioritize parent ROMs with shortplay video over clones
                    rows.sort(
                        key=lambda r: (
                            0 if r.get("url_video_shortplay") else 1,
                            0 if not r.get("cloneof") else 1,
                        )
                    )
                    req_genres = {
                        resolve_canonical_genre("games", g)
                        for g in (criteria.genres or [])
                        if g and g.lower() != "all"
                    }
                    for row in rows:
                        rom = str(row.get("game_name") or "").strip()
                        cand_id = f"games_adb_{rom}"
                        if not rom or cand_id in seen_ids:
                            continue
                        clone_parent = str(row.get("cloneof") or "").strip()
                        if clone_parent and f"games_adb_{clone_parent}" in seen_ids:
                            seen_ids.add(cand_id)
                            continue
                        if not self._is_row_valid_for_system(row, sys_key):
                            seen_ids.add(cand_id)
                            continue
                        if req_decs_clean:
                            yr_str = str(row.get("year") or "").strip()[:4]
                            if not yr_str.isdigit():
                                seen_ids.add(cand_id)
                                continue
                            yr_val = int(yr_str)
                            dec_ok = False
                            for dec_c in req_decs_clean:
                                if dec_c in CANONICAL_DECADES:
                                    lo_b, hi_b = CANONICAL_DECADES[dec_c]["bounds"]
                                    if lo_b <= yr_val <= hi_b:
                                        dec_ok = True
                                        break
                                elif len(dec_c) >= 4 and dec_c[:4].isdigit():
                                    base_y = int(dec_c[:4])
                                    if base_y <= yr_val <= base_y + 9:
                                        dec_ok = True
                                        break
                            if not dec_ok:
                                seen_ids.add(cand_id)
                                continue
                        if req_genres:
                            row_genre = resolve_canonical_genre(
                                "games", str(row.get("genre") or "").strip()
                            )
                            if row_genre not in req_genres:
                                seen_ids.add(cand_id)
                                continue
                            if (
                                "fighting" in req_genres
                                and "platform" not in req_genres
                                and "beatemup" not in req_genres
                                and is_disallowed_non_fighting_title(str(row.get("title") or ""))
                            ):
                                seen_ids.add(cand_id)
                                continue
                        if (
                            not row.get("url_image_ingame")
                            and not row.get("url_image_flyer")
                            and not row.get("url_image_title")
                        ):
                            seen_ids.add(cand_id)
                            continue
                        cand = await self._build_candidate(client, row, sys_key, criteria.lang)
                        if not cand:
                            seen_ids.add(cand_id)
                            continue
                        cand_sys = str((cand.extra_meta or {}).get("system") or sys_key)
                        title_slug = re.sub(r"[^a-z0-9]+", "_", cand.title.lower()).strip("_")
                        title_key = f"games_adb_title_{title_slug}"
                        fp_key = build_game_fingerprint(cand_sys, cand.title)
                        if title_key in seen_ids or (fp_key and fp_key in seen_ids):
                            seen_ids.add(cand.id)
                            continue
                        seen_ids.add(cand.id)
                        seen_ids.add(title_key)
                        if fp_key:
                            seen_ids.add(fp_key)
                        if clone_parent:
                            seen_ids.add(f"games_adb_{clone_parent}")
                        results.append(cand)
                        if len(results) >= count:
                            break

        if console_systems and len(results) < count:
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
