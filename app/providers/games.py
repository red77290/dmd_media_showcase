"""
Multilingual Retro Games Provider:
- Primary / Configurable Sources:
  1. ScreenScraper API v2 (`http://api.screenscraper.fr/api2/jeuInfos.php` & `https://api.screenscraper.fr/api2/jeuInfos.php`)
  2. ArcadeItalia ADB (`https://adb.arcadeitalia.net/service_scraper.php?ajax=query_mame`) — Free keyless MAME/Neo-Geo/CPS artworks, marquees, snaps & shortplay MP4 / YouTube videos.
  3. Libretro Thumbnails CDN (`https://raw.githubusercontent.com/libretro-thumbnails/...`) — Free keyless Console boxarts, title screens & in-game snaps.
- Automatic fallback cascade when the primary source is unconfigured or missing assets.
"""

from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import httpx

from app.config import settings
from app.models import DomainType, SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate, clean_synopsis_text
from app.providers.curated_catalogs import CURATED_GAMES, pick_localized_text

logger = logging.getLogger("dmd_showcase.scraper.games")

SCREENSCRAPER_SYSTEM_IDS = {
    "mame": 75,
    "neogeo": 142,
    "cps": 75,
    "fbneo": 75,
    "nes": 3,
    "snes": 4,
    "n64": 14,
    "megadrive": 1,
    "mastersystem": 2,
    "dreamcast": 23,
    "psx": 57,
    "gba": 12,
    "pcengine": 31,
}


class GamesProvider(BaseMediaProvider):
    domain: DomainType = "games"

    def _resolve_screenscraper_creds(
        self, req: Optional[SessionRegisterRequest]
    ) -> Tuple[str, str, str, str]:
        user = (req.scraper_user.strip() if req and req.scraper_user else "") or settings.screenscraper_user
        passwd = (req.scraper_pass.strip() if req and req.scraper_pass else "") or settings.screenscraper_pass
        dev_id = settings.screenscraper_dev_id
        dev_pass = settings.screenscraper_dev_pass
        if req and req.scraper_api_key and ":" in req.scraper_api_key:
            parts = req.scraper_api_key.split(":", 1)
            dev_id = parts[0].strip()
            dev_pass = parts[1].strip()
        elif req and req.scraper_api_key and not dev_id:
            dev_id = req.scraper_api_key.strip()
        return user, passwd, dev_id, dev_pass

    async def _enrich_with_screenscraper(
        self,
        candidate: RawMediaCandidate,
        rom_query: str,
        system_key: str,
        lang: str,
        req: Optional[SessionRegisterRequest] = None,
    ) -> bool:
        ss_user, ss_pass, dev_id, dev_pass = self._resolve_screenscraper_creds(req)
        if not ss_user and not dev_id:
            logger.info(
                "[SCRAPER:games] ScreenScraper skipped for '%s' (no user/dev credentials configured)",
                candidate.title,
            )
            return False

        sys_id = SCREENSCRAPER_SYSTEM_IDS.get(system_key, 75)
        short_lang = (lang or "fr").split("-")[0].lower()
        params: Dict[str, str] = {
            "softname": "dmd-media-showcase",
            "output": "json",
            "systemeid": str(sys_id),
            "romnom": f"{rom_query}.zip",
        }
        if dev_id:
            params["devid"] = dev_id
            params["devpassword"] = dev_pass
        if ss_user:
            params["ssid"] = ss_user
            params["sspassword"] = ss_pass

        endpoints = [
            "http://api.screenscraper.fr/api2/jeuInfos.php",
            "https://api.screenscraper.fr/api2/jeuInfos.php",
        ]
        for endpoint in endpoints:
            try:
                async with httpx.AsyncClient(timeout=4.5, follow_redirects=True) as client:
                    resp = await client.get(endpoint, params=params)
                    if resp.status_code != 200:
                        logger.info(
                            "[SCRAPER:games] ScreenScraper (%s) returned HTTP %d for '%s'",
                            endpoint,
                            resp.status_code,
                            rom_query,
                        )
                        continue
                    data = resp.json().get("response", {}).get("jeu", {})
                    if not data:
                        continue

                    for syn in data.get("synopsis", []):
                        if syn.get("langue") == short_lang and syn.get("text"):
                            candidate.synopsis = clean_synopsis_text(syn["text"])
                            break

                    for media in data.get("medias", []):
                        mtype = media.get("type", "")
                        url = media.get("url")
                        if not url:
                            continue
                        if mtype in ("box-2D", "box-3D", "flyer") and not candidate.poster_url:
                            candidate.poster_url = url
                        elif mtype in ("wheel", "screenmarquee") and not candidate.marquee_url:
                            candidate.marquee_url = url
                        elif mtype in ("ss", "sstitle", "fanart", "box-2D"):
                            candidate.add_unique_image(url)
                        elif mtype in ("video-normalized", "video") and not candidate.video_url:
                            candidate.video_url = url

                    candidate.source_used = "screenscraper"
                    logger.info(
                        "[SCRAPER:games] ScreenScraper enriched '%s' -> poster=%s, images=%d, video=%s",
                        candidate.title,
                        bool(candidate.poster_url),
                        len(candidate.image_urls),
                        bool(candidate.video_url),
                    )
                    return True
            except Exception as exc:
                logger.info(
                    "[SCRAPER:games] ScreenScraper lookup error on %s for '%s': %s",
                    endpoint,
                    rom_query,
                    exc,
                )
        return False

    async def _enrich_with_arcadeitalia(
        self, candidate: RawMediaCandidate, mame_rom: str
    ) -> bool:
        if not mame_rom:
            return False
        url = "https://adb.arcadeitalia.net/service_scraper.php"
        params = {"ajax": "query_mame", "game_name": mame_rom}
        try:
            async with httpx.AsyncClient(timeout=4.5, follow_redirects=True) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.info(
                        "[SCRAPER:games] ArcadeItalia ADB returned HTTP %d for rom='%s'",
                        resp.status_code,
                        mame_rom,
                    )
                    return False
                results = resp.json().get("result") or []
                if not results:
                    return False
                info = results[0]

                # Poster: prefer flyer or title screen
                poster_cand = info.get("url_image_flyer") or info.get("url_image_title")
                if poster_cand and not candidate.poster_url:
                    candidate.poster_url = poster_cand.replace("http://", "https://")

                # Marquee
                marquee_cand = info.get("url_image_marquee") or info.get("url_image_title")
                if marquee_cand and not candidate.marquee_url:
                    candidate.marquee_url = marquee_cand.replace("http://", "https://")

                # Collect distinct artwork/screenshots for top-to-bottom panorama scrolling
                for img_key in (
                    "url_image_ingame",
                    "url_image_title",
                    "url_image_flyer",
                    "url_image_boss",
                    "url_image_cabinet",
                    "url_image_Select",
                    "url_image_scores",
                ):
                    raw_img = info.get(img_key)
                    if raw_img:
                        candidate.add_unique_image(raw_img.replace("http://", "https://"))

                # Direct shortplay MP4 video
                shortplay = info.get("url_video_shortplay")
                if shortplay and not candidate.video_url:
                    candidate.video_url = shortplay.replace("http://", "https://")

                # YouTube fallback ID from ArcadeItalia (if shortplay MP4 is missing or on YouTube)
                yt_id = (info.get("youtube_id") or "").strip()
                if yt_id and not candidate.youtube_url:
                    candidate.youtube_url = f"https://www.youtube.com/watch?v={yt_id}"

                if candidate.source_used == "curated":
                    candidate.source_used = "arcadeitalia"
                else:
                    candidate.source_used += "+arcadeitalia"

                logger.info(
                    "[SCRAPER:games] ArcadeItalia ADB enriched '%s' (rom=%s) -> poster=%s, images=%d, mp4=%s, youtube=%s",
                    candidate.title,
                    mame_rom,
                    bool(candidate.poster_url),
                    len(candidate.image_urls),
                    bool(candidate.video_url),
                    bool(candidate.youtube_url),
                )
                return True
        except Exception as exc:
            logger.info("[SCRAPER:games] ArcadeItalia ADB skipped for '%s': %s", mame_rom, exc)
            return False

    async def _enrich_with_libretro(
        self, candidate: RawMediaCandidate, libretro_system: str, libretro_title: str
    ) -> bool:
        if not libretro_system or not libretro_title:
            return False
        encoded_sys = quote(libretro_system, safe="_-")
        encoded_title = quote(libretro_title, safe="")
        base_cdn = f"https://raw.githubusercontent.com/libretro-thumbnails/{encoded_sys}/master"

        boxart_url = f"{base_cdn}/Named_Boxarts/{encoded_title}.png"
        title_url = f"{base_cdn}/Named_Titles/{encoded_title}.png"
        snap_url = f"{base_cdn}/Named_Snaps/{encoded_title}.png"

        if not candidate.poster_url:
            candidate.poster_url = boxart_url
        if not candidate.marquee_url:
            candidate.marquee_url = title_url

        candidate.add_unique_image(snap_url)
        candidate.add_unique_image(title_url)
        candidate.add_unique_image(boxart_url)

        if candidate.source_used == "curated":
            candidate.source_used = "libretro"
        else:
            candidate.source_used += "+libretro"

        logger.info(
            "[SCRAPER:games] Libretro CDN enriched '%s' (%s) -> poster=OK, images=%d (video=None)",
            candidate.title,
            libretro_system,
            len(candidate.image_urls),
        )
        return True

    def _entry_to_candidate(self, entry: dict, lang: str) -> RawMediaCandidate:
        return RawMediaCandidate(
            id=entry["id"],
            domain="games",
            title=pick_localized_text(entry["title"], lang),
            year=entry["year"],
            badge=entry["badge"],
            badge_color=entry["badge_color"],
            subtitle=entry["subtitle"],
            rating=entry["rating"],
            synopsis=clean_synopsis_text(pick_localized_text(entry["synopsis"], lang)),
            lang=lang,
            theme_palette=tuple(entry.get("palette", (229, 57, 53))),
            extra_meta={
                "system": entry["system"],
                "mame_rom": str(entry.get("mame_rom", "")),
                "libretro_system": str(entry.get("libretro_system", "")),
                "libretro_title": str(entry.get("libretro_title", "")),
            },
        )

    async def _enrich_candidate_cascade(
        self, cand: RawMediaCandidate, entry: dict, req: SessionRegisterRequest
    ) -> RawMediaCandidate:
        primary = (req.primary_source or "auto").lower().strip()
        mame_rom = entry.get("mame_rom", "")
        lib_sys = entry.get("libretro_system", "")
        lib_title = entry.get("libretro_title", "")
        rom_query = mame_rom or entry["id"].split("_")[-1]

        # Build ordered scraper pipeline: user's primary source first, then automatic fallbacks
        if primary == "screenscraper":
            order = ["screenscraper", "arcadeitalia", "libretro"]
        elif primary == "arcadeitalia":
            order = ["arcadeitalia", "screenscraper", "libretro"]
        elif primary == "libretro":
            order = ["libretro", "screenscraper", "arcadeitalia"]
        else:
            # Auto mode: ScreenScraper if credentials exist, then ArcadeItalia for MAME/NeoGeo/CPS, then Libretro for consoles
            order = ["screenscraper", "arcadeitalia", "libretro"]

        for source in order:
            if source == "screenscraper":
                await self._enrich_with_screenscraper(
                    cand, rom_query, entry["system"], req.lang, req=req
                )
            elif source == "arcadeitalia" and mame_rom:
                if not cand.poster_url or not cand.video_url or len(cand.image_urls) < max(1, req.image_count):
                    await self._enrich_with_arcadeitalia(cand, mame_rom)
            elif source == "libretro" and lib_sys and lib_title:
                if not cand.poster_url or len(cand.image_urls) < max(1, req.image_count):
                    await self._enrich_with_libretro(cand, lib_sys, lib_title)

        return cand

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        exclude = set(exclude_ids or set())
        requested_systems = {s.lower().strip() for s in req.systems if s.strip()}

        matching_all = [
            g
            for g in CURATED_GAMES
            if (
                not requested_systems
                or "all" in requested_systems
                or g["system"] in requested_systems
                or f"all_{g['group']}" in requested_systems
            )
        ]
        if not matching_all:
            matching_all = list(CURATED_GAMES)

        unseen_pool = [g for g in matching_all if g["id"] not in exclude]
        if len(unseen_pool) < req.count:
            # All or most items in this filter have been seen -> reset unseen pool while avoiding immediate duplicates
            unseen_pool = list(matching_all)

        shuffled = list(unseen_pool)
        random.shuffle(shuffled)
        selected: List[dict] = []
        while len(selected) < req.count:
            for item in shuffled:
                selected.append(item)
                if len(selected) >= req.count:
                    break

        logger.info(
            "[SCRAPER:games] Selected %d random games (primary_source=%s, systems=%s, unseen_pool=%d/%d): %s",
            len(selected),
            req.primary_source,
            list(requested_systems) or ["all"],
            len(unseen_pool),
            len(matching_all),
            [pick_localized_text(e["title"], req.lang) for e in selected],
        )

        results: List[RawMediaCandidate] = []
        for idx, entry in enumerate(selected):
            cand = self._entry_to_candidate(entry, req.lang)
            if len(selected) > len(matching_all):
                cand.id = f"{cand.id}_{idx}"
            cand = await self._enrich_candidate_cascade(cand, entry, req)
            results.append(cand)

        return results

    async def search_on_demand(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[RawMediaCandidate]:
        q_low = query.lower().strip()
        synth_req = SessionRegisterRequest(count=1, lang=lang)
        for entry in CURATED_GAMES:
            title_fr = entry["title"].get("fr", "").lower()
            title_en = entry["title"].get("en", "").lower()
            rom = entry.get("mame_rom", "").lower()
            if q_low in title_fr or q_low in title_en or q_low == rom or q_low in entry["system"]:
                cand = self._entry_to_candidate(entry, lang)
                return await self._enrich_candidate_cascade(cand, entry, synth_req)
        cand = self._entry_to_candidate(CURATED_GAMES[0], lang)
        return await self._enrich_candidate_cascade(cand, CURATED_GAMES[0], synth_req)

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "sources": [
                {"id": "auto", "label": "Auto (ScreenScraper -> ArcadeItalia -> Libretro)"},
                {"id": "screenscraper", "label": "ScreenScraper.fr (api.screenscraper.fr)"},
                {"id": "arcadeitalia", "label": "ArcadeItalia ADB (MAME / Neo-Geo / CPS)"},
                {"id": "libretro", "label": "Libretro Thumbnails CDN (Consoles)"},
            ],
            "arcade_systems": [
                {"id": "mame", "label": "MAME / Arcade"},
                {"id": "neogeo", "label": "SNK Neo-Geo"},
                {"id": "cps", "label": "Capcom CPS-1 / CPS-2 / CPS-3"},
                {"id": "fbneo", "label": "FinalBurn Neo"},
            ],
            "console_systems": [
                {"id": "snes", "label": "Super Nintendo (SNES)"},
                {"id": "megadrive", "label": "Sega Mega Drive / Genesis"},
                {"id": "nes", "label": "Nintendo Entertainment System (NES)"},
                {"id": "psx", "label": "Sony PlayStation (PS1)"},
                {"id": "dreamcast", "label": "Sega Dreamcast"},
                {"id": "gba", "label": "Game Boy Advance (GBA)"},
            ],
        }
