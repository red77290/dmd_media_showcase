"""
Multilingual Games Provider (ScreenScraper.fr + Steam Store Public API + Curated Retro Catalog).
"""

from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Set

import httpx

from app.config import settings
from app.models import DomainType, SessionRegisterRequest
from app.providers.base import BaseMediaProvider, RawMediaCandidate, clean_synopsis_text
from app.providers.curated_catalogs import CURATED_GAMES, pick_localized_text

logger = logging.getLogger(__name__)

STEAM_LANG_MAP = {
    "fr": "french",
    "en": "english",
    "es": "spanish",
    "de": "german",
    "it": "italian",
    "pt": "brazilian",
    "ja": "japanese",
}

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

    async def _enrich_with_screenscraper(
        self, candidate: RawMediaCandidate, rom_query: str, system_key: str, lang: str
    ) -> RawMediaCandidate:
        if not settings.screenscraper_user or not settings.screenscraper_dev_id:
            return candidate

        sys_id = SCREENSCRAPER_SYSTEM_IDS.get(system_key, 75)
        short_lang = (lang or "fr").split("-")[0].lower()
        params = {
            "devid": settings.screenscraper_dev_id,
            "devpassword": settings.screenscraper_dev_pass,
            "softname": "dmd-media-showcase",
            "ssid": settings.screenscraper_user,
            "sspassword": settings.screenscraper_pass,
            "output": "json",
            "systemeid": str(sys_id),
            "romnom": f"{rom_query}.zip",
        }
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(
                    "https://www.screenscraper.fr/api2/jeuInfos.php", params=params
                )
                if resp.status_code != 200:
                    return candidate
                data = resp.json().get("response", {}).get("jeu", {})
                if not data:
                    return candidate

                for syn in data.get("synopsis", []):
                    if syn.get("langue") == short_lang and syn.get("text"):
                        candidate.synopsis = clean_synopsis_text(syn["text"])
                        break

                for media in data.get("medias", []):
                    mtype = media.get("type", "")
                    url = media.get("url")
                    if not url:
                        continue
                    if mtype == "box-2D" and not candidate.poster_url:
                        candidate.poster_url = url
                    elif mtype == "wheel" and not candidate.marquee_url:
                        candidate.marquee_url = url
                    elif mtype == "ss" and not candidate.backdrop_url:
                        candidate.backdrop_url = url
                    elif mtype in ("video-normalized", "video") and not candidate.video_url:
                        candidate.video_url = url
        except Exception as exc:
            logger.debug("ScreenScraper enrichment skipped for %s: %s", rom_query, exc)
        return candidate

    async def _enrich_with_steam(
        self, candidate: RawMediaCandidate, appid: str, lang: str
    ) -> RawMediaCandidate:
        steam_lang = STEAM_LANG_MAP.get((lang or "fr").split("-")[0].lower(), "english")
        try:
            async with httpx.AsyncClient(timeout=3.5) as client:
                resp = await client.get(
                    "https://store.steampowered.com/api/appdetails",
                    params={"appids": appid, "l": steam_lang},
                )
                if resp.status_code != 200:
                    return candidate
                payload = resp.json().get(str(appid), {})
                if not payload.get("success"):
                    return candidate
                data = payload.get("data", {})
                if data.get("short_description"):
                    candidate.synopsis = clean_synopsis_text(data["short_description"])
                if data.get("header_image") and not candidate.poster_url:
                    candidate.poster_url = data["header_image"]
                screenshots = data.get("screenshots") or []
                if screenshots and not candidate.backdrop_url:
                    candidate.backdrop_url = screenshots[0].get("path_thumbnail")
                movies = data.get("movies") or []
                if movies and not candidate.video_url:
                    mp4_dict = movies[0].get("mp4") or {}
                    candidate.video_url = mp4_dict.get("480") or mp4_dict.get("max")
        except Exception as exc:
            logger.debug("Steam enrichment skipped for appid %s: %s", appid, exc)
        return candidate

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
                "steam_appid": str(entry.get("steam_appid", "")),
            },
        )

    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        exclude = exclude_ids or set()
        requested_systems = {s.lower().strip() for s in req.systems if s.strip()}

        pool = [
            g
            for g in CURATED_GAMES
            if (
                not requested_systems
                or "all" in requested_systems
                or g["system"] in requested_systems
                or f"all_{g['group']}" in requested_systems
            )
            and g["id"] not in exclude
        ]
        if not pool:
            pool = list(CURATED_GAMES)

        shuffled = list(pool)
        random.shuffle(shuffled)
        selected: List[dict] = []
        while len(selected) < req.count:
            for item in shuffled:
                selected.append(item)
                if len(selected) >= req.count:
                    break

        results: List[RawMediaCandidate] = []
        for idx, entry in enumerate(selected):
            cand = self._entry_to_candidate(entry, req.lang)
            if len(selected) > len(pool):
                cand.id = f"{cand.id}_{idx}"
            if settings.screenscraper_user:
                cand = await self._enrich_with_screenscraper(
                    cand, cand.title, entry["system"], req.lang
                )
            elif entry.get("steam_appid"):
                cand = await self._enrich_with_steam(cand, str(entry["steam_appid"]), req.lang)
            results.append(cand)

        return results

    async def search_on_demand(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[RawMediaCandidate]:
        q_low = query.lower().strip()
        for entry in CURATED_GAMES:
            title_fr = entry["title"].get("fr", "").lower()
            title_en = entry["title"].get("en", "").lower()
            if q_low in title_fr or q_low in title_en or q_low in entry["system"]:
                return self._entry_to_candidate(entry, lang)
        return self._entry_to_candidate(CURATED_GAMES[0], lang)

    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
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
