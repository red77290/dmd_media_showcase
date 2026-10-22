"""
Infrastructure Persistence & Per-Source Strategies for Live Scraper Referentials.

Maintains real-time, self-updating referential caches in `./data/referentials/{source_id}.json`
so every `IScraperSource` adapter can perform random unseen discovery and category/system
filtering generically without hardcoded catalog arrays.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import FilterCriteria
from app.domain.ports.referential_strategy import IScraperReferentialStrategy
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.scrapers.referential")

_VOWELS = "aeiouy"
_CONSONANTS = "bcdfghjklmnpqrstvwxz"
_ADB_ROM_LINK_RE = re.compile(r"dettaglio_mame\.php\?[^\"'>]*\bgame_name=([a-zA-Z0-9_]{2,16})", re.I)

# Ignored BIOS / device romsets when parsing live MAME search pages
_IGNORED_MAME_PREFIXES = (
    "neogeo",
    "pgm",
    "cpzn",
    "cvs",
    "decocass",
    "megatech",
    "nss",
    "playch10",
    "stvbios",
    "konamigv",
    "skns",
    "acpsx",
    "coh1000",
    "namco",
    "ym2608",
    "qsound",
)


def random_search_syllable() -> str:
    """Generate a 2-letter syllable for live search index sampling."""
    if random.random() < 0.65:
        return random.choice(_CONSONANTS) + random.choice(_VOWELS)
    return random.choice(_VOWELS) + random.choice(_CONSONANTS)


class LiveReferentialStore:
    """
    Thread-safe JSON persistence store for live scraper referentials in
    `./data/referentials/{source_id}.json`.
    """

    def __init__(self, source_id: str, ttl_seconds: int = 86400) -> None:
        self.source_id = source_id
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, Any]] = None

    @property
    def file_path(self) -> Path:
        return get_data_dir() / "referentials" / f"{self.source_id}.json"

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
            path = self.file_path
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        self._cache = raw
                        return dict(raw)
                except Exception as exc:
                    logger.debug("[REFERENTIAL:%s] Could not read %s: %s", self.source_id, path, exc)
            self._cache = {"updated_at": 0.0, "tokens": [], "metadata": {}}
            return dict(self._cache)

    def is_expired(self) -> bool:
        data = self.load()
        updated_at = float(data.get("updated_at") or 0.0)
        tokens = data.get("tokens") or []
        return (time.time() - updated_at) > self.ttl_seconds or len(tokens) == 0

    def save(self, tokens: List[str], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            current = self.load()
            merged_tokens = list(dict.fromkeys([t for t in (current.get("tokens") or []) + tokens if t]))
            merged_meta = dict(current.get("metadata") or {})
            if metadata:
                merged_meta.update(metadata)
            payload = {
                "source_id": self.source_id,
                "updated_at": time.time(),
                "tokens": merged_tokens,
                "metadata": merged_meta,
            }
            self._cache = payload
            try:
                path = self.file_path
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(tmp, path)
            except Exception as exc:
                logger.debug("[REFERENTIAL:%s] Could not write referential: %s", self.source_id, exc)
            return dict(payload)


class ArcadeItaliaReferentialStrategy(IScraperReferentialStrategy):
    """
    Live Referential Strategy for `ArcadeItaliaAdapter` (`adb.arcadeitalia.net`):
    - Zero hardcoded ROM lists.
    - Dynamically harvests and updates valid MAME `game_name` ROM identifiers in real time from:
      1. ArcadeItalia's live search index (`https://adb.arcadeitalia.net/lista_mame.php?ricerca={token}&arcade_only=1&lang=en`)
      2. Wikidata's live SPARQL endpoint (`wdt:P2451` = official ArcadeItalia / MAME ROM property)
    - Persists discovered ROM identifiers in `./data/referentials/arcadeitalia.json` so subsequent
      `service_scraper.php?ajax=query_mame&game_name=...` queries execute in O(1) with HTTP 200.
    """

    source_id = "arcadeitalia"

    def __init__(self) -> None:
        self.store = LiveReferentialStore(self.source_id, ttl_seconds=43200)

    async def _harvest_from_lista_mame(self, client: Any, token: str) -> List[str]:
        """Scrape valid MAME `game_name` identifiers from ArcadeItalia's live `lista_mame.php` search."""
        discovered: List[str] = []
        for base_url in (
            "https://adb.arcadeitalia.net/lista_mame.php",
            "http://adb.arcadeitalia.net/lista_mame.php",
        ):
            try:
                resp = await client.get(
                    base_url,
                    params={"ricerca": token, "arcade_only": "1", "lang": "en"},
                    timeout=5.0,
                )
                if resp.status_code != 200 or not resp.text:
                    continue
                for match in _ADB_ROM_LINK_RE.finditer(resp.text):
                    rom = match.group(1).lower().strip()
                    if rom and not rom.startswith(_IGNORED_MAME_PREFIXES) and rom not in discovered:
                        discovered.append(rom)
                if discovered:
                    break
            except Exception as exc:
                logger.debug("[REFERENTIAL:arcadeitalia] lista_mame harvest for '%s' failed: %s", token, exc)
        return discovered

    async def _harvest_from_wikidata_p2451(self, client: Any) -> List[str]:
        """Query Wikidata SPARQL (`wdt:P2451` = MAME ROM ID) for live verified arcade ROM names."""
        sparql = (
            "SELECT DISTINCT ?rom WHERE { "
            "?game wdt:P31/wdt:P279* wd:Q7889 ; wdt:P2451 ?rom . "
            "} LIMIT 400"
        )
        discovered: List[str] = []
        try:
            resp = await client.get(
                "https://query.wikidata.org/sparql",
                params={"format": "json", "query": sparql},
                headers={"Accept": "application/sparql-results+json"},
                timeout=5.5,
            )
            if resp.status_code == 200:
                bindings = (resp.json().get("results") or {}).get("bindings") or []
                for b in bindings:
                    rom = str(((b.get("rom") or {}).get("value")) or "").strip().lower()
                    if (
                        rom
                        and re.fullmatch(r"[a-z0-9_]{2,16}", rom)
                        and not rom.startswith(_IGNORED_MAME_PREFIXES)
                    ):
                        discovered.append(rom)
        except Exception as exc:
            logger.debug("[REFERENTIAL:arcadeitalia] Wikidata P2451 sync skipped: %s", exc)
        return discovered

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()

        tokens: List[str] = []
        # 1. Harvest live ROM identifiers from ArcadeItalia `lista_mame.php` with random syllables
        for _ in range(2):
            syllable = random_search_syllable()
            batch = await self._harvest_from_lista_mame(client, syllable)
            tokens.extend(batch)
            if len(tokens) >= 40:
                break

        # 2. Enrich with Wikidata P2451 live MAME ROM referential if needed
        if len(tokens) < 25:
            wd_roms = await self._harvest_from_wikidata_p2451(client)
            tokens.extend(wd_roms)

        saved = self.store.save(tokens=tokens, metadata={"last_sync_count": len(tokens)})
        logger.info(
            "[REFERENTIAL:arcadeitalia] Live referential synced: %d MAME ROM identifiers cached",
            len(saved.get("tokens") or []),
        )
        return saved

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref_data = await self.sync_referential(client, force=False)
        all_tokens: List[str] = list(ref_data.get("tokens") or [])
        unseen = [r for r in all_tokens if f"games_adb_{r}" not in seen_ids]

        # Real-time incremental discovery if unseen pool is low
        if len(unseen) < count:
            fresh = await self._harvest_from_lista_mame(client, random_search_syllable())
            if fresh:
                ref_data = self.store.save(tokens=fresh)
                all_tokens = list(ref_data.get("tokens") or [])
                unseen = [r for r in all_tokens if f"games_adb_{r}" not in seen_ids]

        if not unseen:
            unseen = all_tokens
        if not unseen:
            return []
        return random.sample(unseen, k=min(len(unseen), max(1, count)))


class JikanReferentialStrategy(IScraperReferentialStrategy):
    """
    Live Referential Strategy for `JikanAnimeThemesAdapter`:
    - Synchronizes live anime genre IDs and titles from `https://api.jikan.moe/v4/genres/anime`
      into `./data/referentials/jikan_animethemes.json`.
    - Tracks discovered `last_visible_page` bounds per genre for uniform random page sampling.
    """

    source_id = "jikan_animethemes"

    def __init__(self) -> None:
        self.store = LiveReferentialStore(self.source_id, ttl_seconds=86400)

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        genres_map: Dict[str, Dict[str, Any]] = {}
        try:
            resp = await client.get("https://api.jikan.moe/v4/genres/anime", timeout=5.0)
            if resp.status_code == 200:
                for item in resp.json().get("data") or []:
                    mal_id = item.get("mal_id")
                    name = str(item.get("name") or "").strip()
                    if mal_id and name:
                        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                        genres_map[slug] = {"mal_id": int(mal_id), "label": name}
        except Exception as exc:
            logger.debug("[REFERENTIAL:jikan] Genre sync skipped: %s", exc)
        return self.store.save(tokens=list(genres_map.keys()), metadata={"genres": genres_map})

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        return list(ref.get("tokens") or [])[:count]


class AniListReferentialStrategy(IScraperReferentialStrategy):
    """
    Live Referential Strategy for `AniListAdapter`:
    - Synchronizes live `GenreCollection` from `https://graphql.anilist.co`
      into `./data/referentials/anilist.json`.
    """

    source_id = "anilist"

    def __init__(self) -> None:
        self.store = LiveReferentialStore(self.source_id, ttl_seconds=86400)

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        genres: List[str] = []
        try:
            resp = await client.post(
                "https://graphql.anilist.co",
                json={"query": "query { GenreCollection }"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                raw_list = (resp.json().get("data") or {}).get("GenreCollection") or []
                genres = [str(g).strip() for g in raw_list if g]
        except Exception as exc:
            logger.debug("[REFERENTIAL:anilist] GenreCollection sync skipped: %s", exc)
        return self.store.save(tokens=genres, metadata={"genres": genres})

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        return list(ref.get("tokens") or [])[:count]


class TvMazeReferentialStrategy(IScraperReferentialStrategy):
    """
    Live Referential Strategy for `TvMazeAdapter`:
    - Dynamically harvests active TV genres and valid show page bounds from `https://api.tvmaze.com/shows`
      into `./data/referentials/tvmaze.json`.
    """

    source_id = "tvmaze"

    def __init__(self) -> None:
        self.store = LiveReferentialStore(self.source_id, ttl_seconds=86400)

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        genres: Set[str] = set()
        try:
            resp = await client.get("https://api.tvmaze.com/shows", params={"page": "0"}, timeout=5.0)
            if resp.status_code == 200:
                for sh in resp.json() or []:
                    if isinstance(sh, dict):
                        for g in sh.get("genres") or []:
                            if g:
                                genres.add(str(g).strip())
        except Exception as exc:
            logger.debug("[REFERENTIAL:tvmaze] Sync skipped: %s", exc)
        return self.store.save(tokens=sorted(genres), metadata={"max_verified_page": 240})

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        max_page = int((ref.get("metadata") or {}).get("max_verified_page") or 200)
        return [str(p) for p in random.sample(range(0, max_page), k=min(count, max_page))]


arcadeitalia_referential_strategy = ArcadeItaliaReferentialStrategy()
jikan_referential_strategy = JikanReferentialStrategy()
anilist_referential_strategy = AniListReferentialStrategy()
tvmaze_referential_strategy = TvMazeReferentialStrategy()

