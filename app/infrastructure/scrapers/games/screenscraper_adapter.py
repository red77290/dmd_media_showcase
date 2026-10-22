"""
Primary Games Scraper Adapter: ScreenScraper API v2 (`api.screenscraper.fr`).
Implements `IScraperSource` with uniform random discovery across all 28 retro systems.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.persistence.server_config_store import server_config_store
from app.infrastructure.scrapers.catalog_metadata import GAME_GENRES, SYSTEM_METADATA
from app.infrastructure.scrapers.http_utils import clean_synopsis_text, create_async_http_client

logger = logging.getLogger("dmd_showcase.scrapers.games.screenscraper")

_SEARCH_SYLLABLES = [
    "ma", "st", "ca", "so", "dr", "fi", "ki", "me", "su", "ba",
    "to", "ra", "da", "ko", "sh", "ga", "le", "pa", "ch", "bo",
    "al", "ar", "ta", "mo", "ro", "ni", "sa", "te", "ka", "cr",
]
_WARNED_MISSING_DEVID = False


class ScreenScraperAdapter(IScraperSource):
    """
    Primary `IScraperSource` for `games` using ScreenScraper API v2 (`jeuRecherche.php` & `jeuInfos.php`).
    """

    source_id: str = "screenscraper"
    domain: DomainType = "games"
    role: str = "primary"

    def _credentials(self) -> Dict[str, str]:
        cfg = server_config_store.get_all()
        return {
            "user": str(cfg.get("screenscraper_user") or "").strip(),
            "pass": str(cfg.get("screenscraper_pass") or "").strip(),
            "devid": str(cfg.get("screenscraper_dev_id") or "").strip(),
            "devpass": str(cfg.get("screenscraper_dev_pass") or "").strip(),
        }

    def is_configured(self) -> bool:
        global _WARNED_MISSING_DEVID
        creds = self._credentials()
        if creds["devid"] and creds["devpass"]:
            return True
        if creds["user"] and not _WARNED_MISSING_DEVID:
            _WARNED_MISSING_DEVID = True
            logger.warning(
                "[ScreenScraper] SCREENSCRAPER_USER is set, but SCREENSCRAPER_DEV_ID / SCREENSCRAPER_DEV_PASS "
                "are empty (required by ScreenScraper API v2) -> using fallback game scrapers."
            )
        return False

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

    def _parse_game_node(
        self, jeu: Dict[str, Any], sys_key: str, lang: str
    ) -> Optional[MediaCandidate]:
        game_id = str(jeu.get("id") or "").strip()
        if not game_id:
            return None
        short_lang = (lang or "fr").split("-")[0].lower()
        sys_meta = SYSTEM_METADATA.get(sys_key, SYSTEM_METADATA["arcade"])

        title = ""
        for nom in jeu.get("noms") or []:
            if isinstance(nom, dict) and nom.get("text"):
                if nom.get("region") in (short_lang, "wor", "eu", "us", "ss", "jp"):
                    title = str(nom["text"]).strip()
                    break
                if not title:
                    title = str(nom["text"]).strip()
        if not title:
            return None

        synopsis = ""
        for syn in jeu.get("synopsis") or []:
            if isinstance(syn, dict) and syn.get("text"):
                if syn.get("langue") == short_lang:
                    synopsis = clean_synopsis_text(str(syn["text"]))
                    break
                if not synopsis:
                    synopsis = clean_synopsis_text(str(syn["text"]))

        year = "1992"
        for d_node in jeu.get("dates") or []:
            if isinstance(d_node, dict) and d_node.get("text"):
                year = str(d_node["text"])[:4]
                break

        note_raw = (jeu.get("note") or {}).get("text") if isinstance(jeu.get("note"), dict) else None
        rating = f"{float(note_raw) / 2.0:.1f}" if note_raw and str(note_raw).isdigit() else "8.5"

        cand = MediaCandidate(
            id=f"games_ss_{game_id}",
            domain="games",
            title=title,
            year=year,
            badge=sys_meta["badge"],
            badge_color=sys_meta["color"],
            subtitle=f"{sys_meta['badge']} • {year}",
            rating=rating,
            synopsis=synopsis or f"{title} ({year}) on {sys_meta['label']}.",
            lang=short_lang,
            source_id=self.source_id,
            theme_palette=sys_meta["palette"],
            extra_meta={"system": sys_key},
        )

        for media in jeu.get("medias") or []:
            if not isinstance(media, dict):
                continue
            mtype = str(media.get("type") or "")
            url = str(media.get("url") or "")
            if not url.startswith("http"):
                continue
            if mtype in ("box-2D", "flyer", "box-3D") and not cand.poster_url:
                cand.poster_url = url
                cand.jacket_url = url
            elif mtype in ("wheel", "screenmarquee") and not cand.marquee_url:
                cand.marquee_url = url
            elif mtype in ("ss", "sstitle", "fanart"):
                cand.add_unique_snapshot(url)
            elif mtype in ("video-normalized", "video") and not cand.video_url:
                cand.video_url = url

        return cand if (cand.poster_url or cand.snapshot_urls) else None

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        if not self.is_configured():
            return []

        creds = self._credentials()
        valid_systems = [
            s.lower().strip()
            for s in criteria.systems
            if s.lower().strip() in SYSTEM_METADATA
        ] or list(SYSTEM_METADATA.keys())

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=6.0) as client:
            for _ in range(max(3, count * 2)):
                if len(results) >= count:
                    break
                sys_key = random.choice(valid_systems)
                sys_meta = SYSTEM_METADATA[sys_key]
                token = random.choice(_SEARCH_SYLLABLES)
                params: Dict[str, str] = {
                    "softname": "dmd-media-showcase",
                    "output": "json",
                    "systemeid": str(sys_meta["ss_id"]),
                    "recherche": token,
                }
                if creds["devid"]:
                    params["devid"] = creds["devid"]
                    params["devpassword"] = creds["devpass"]
                if creds["user"]:
                    params["ssid"] = creds["user"]
                    params["sspassword"] = creds["pass"]

                try:
                    resp = await client.get(
                        "https://api.screenscraper.fr/api2/jeuRecherche.php",
                        params=params,
                    )
                    if resp.status_code != 200:
                        continue
                    jeux = (resp.json().get("response") or {}).get("jeux") or []
                    random.shuffle(jeux)
                    for jeu in jeux:
                        if not isinstance(jeu, dict):
                            continue
                        cand = self._parse_game_node(jeu, sys_key, criteria.lang)
                        if cand and cand.id not in seen_ids:
                            seen_ids.add(cand.id)
                            results.append(cand)
                            if len(results) >= count:
                                break
                except Exception as exc:
                    logger.debug("[ScreenScraper] Discovery error: %s", exc)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        if not self.is_configured() or not query.strip():
            return None
        creds = self._credentials()
        params: Dict[str, str] = {
            "softname": "dmd-media-showcase",
            "output": "json",
            "recherche": query.strip(),
        }
        if creds["devid"]:
            params["devid"] = creds["devid"]
            params["devpassword"] = creds["devpass"]
        if creds["user"]:
            params["ssid"] = creds["user"]
            params["sspassword"] = creds["pass"]
        try:
            async with create_async_http_client(timeout=6.0) as client:
                resp = await client.get(
                    "https://api.screenscraper.fr/api2/jeuRecherche.php",
                    params=params,
                )
                if resp.status_code == 200:
                    jeux = (resp.json().get("response") or {}).get("jeux") or []
                    for jeu in jeux:
                        if isinstance(jeu, dict):
                            cand = self._parse_game_node(jeu, "arcade", lang)
                            if cand:
                                return cand
        except Exception:
            pass
        return None
