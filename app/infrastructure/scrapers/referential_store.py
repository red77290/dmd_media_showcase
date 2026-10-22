"""
Real-Time Scraper Referential Store & Per-Source Strategies (`./data/referentials/`).

DDD Architecture:
- Instead of hardcoding static lists of game ROMs, movie IDs, or genres in code, each
  scraper source (`ArcadeItalia`, `Libretro+Wikidata`, `Jikan`, `AniList`, `TVMaze`, `TMDB`)
  uses a dedicated `IScraperReferentialStrategy` that harvests and refreshes its live
  referential from the upstream provider in real time and persists it locally in
  `./data/referentials/{source_id}.json`.
- Random discovery (`fetch_random_unseen`) samples directly from these live referentials
  while excluding `seen_history.json` (`ISeenHistoryRepository`).
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import FilterCriteria
from app.domain.taxonomy import (
    CANONICAL_DECADES,
    GAME_GENRE_CATALOG,
    RETRO_SYSTEM_CATALOG,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.scrapers.referential")

# Dynamically generated 2-letter alphanumeric search syllables for live catalog harvesting
_VOWELS = "aeiou"
_CONSONANTS = "bcdfghjklmnpqrstvwxz"
_ADB_ROM_LINK_RE = re.compile(r"dettaglio_mame\.php\?[^>]*?\bgame_name=([a-z0-9_]+)", re.IGNORECASE)
_IGNORED_MAME_PREFIXES = ("bios", "neogeo", "cvs", "playch10", "nss", "megatech", "stvbios", "cpzn")
_REGION_TAG_RE = re.compile(r"\s*\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def random_search_syllable() -> str:
    """Generate a natural 2-letter syllable (`ca`, `ma`, `st`, `ra`, `so`, ...) for live index harvesting."""
    if random.random() < 0.75:
        return random.choice(_CONSONANTS) + random.choice(_VOWELS)
    return random.choice(_VOWELS) + random.choice(_CONSONANTS)


def normalize_game_lookup_key(title: str) -> str:
    """
    Normalize a game title or No-Intro filename into a canonical lookup key:
    - Strip `.png` and all parenthetical tags `(Europe)`, `(France)`, `(USA)`, `(Rev A)`
    - Handle trailing `, The` or leading `The `
    - Strip punctuation/subtitles as needed
    """
    s = (title or "").strip()
    if s.lower().endswith(".png"):
        s = s[:-4]
    s = _REGION_TAG_RE.sub("", s).strip()
    lower = s.lower()
    if lower.endswith(", the"):
        s = s[:-5].strip()
    elif lower.startswith("the "):
        s = s[4:].strip()
    return _NON_ALNUM_RE.sub("", s.lower())


def score_libretro_filename_for_lang(filename: str, lang: str = "fr") -> int:
    """
    Score a No-Intro Libretro filename by regional preference so French DMDs receive
    `(France)` or `(Europe)` boxarts/titles before `(World)` or `(USA)`.
    """
    low = filename.lower()
    short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
    score = 10
    if "proto" in low or "beta" in low or "sample" in low or "demo" in low or "pirate" in low or "unl" in low:
        score -= 50

    if short_lang == "fr":
        if "(france" in low or ", fr" in low or "(fr)" in low:
            score += 100
        elif "(europe" in low:
            score += 85
        elif "(world" in low:
            score += 70
        elif "(usa" in low:
            score += 50
        elif "(japan" in low:
            score += 30
    elif short_lang in ("es", "de", "it"):
        country_map = {"es": "(spain", "de": "(germany", "it": "(italy"}
        if country_map[short_lang] in low:
            score += 100
        elif "(europe" in low:
            score += 85
        elif "(world" in low:
            score += 70
        elif "(usa" in low:
            score += 55
    else:
        if "(world" in low or "(usa" in low:
            score += 90
        elif "(europe" in low:
            score += 80
        elif "(japan" in low:
            score += 40
    return score


class IScraperReferentialStrategy(ABC):
    """
    DDD Port for per-scraper live referential synchronization and random token sampling.
    """

    source_id: str

    @abstractmethod
    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        """Synchronize or refresh the local referential store from the live scraper."""

    @abstractmethod
    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        """Sample unseen query tokens/IDs from the live referential for `criteria`."""


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


class LibretroWikidataReferentialStrategy(IScraperReferentialStrategy):
    """
    Live Referential Strategy for Console & Retro Games (`LibretroWikidataAdapter`):
    1. Queries Wikidata SPARQL (`wd:Q7889` = Video Game ONLY) filtered by exact platform QID
       (`wdt:P400`) and genre QID (`wdt:P136/wdt:P279*`), extracting French & English labels,
       release years, and exact Wikipedia sitelinks (`frwiki` / `enwiki`).
       - Guarantees hardware consoles (`NES`, `SNES`) or chips (`CIC`) are NEVER returned.
       - Guarantees SNES-only games (`Goof Troop`, `F-Zero`, `Pilotwings`) are NEVER returned for NES.
    2. Harvests and caches the real GitHub file tree of `libretro-thumbnails/{repo}` in
       `./data/referentials/libretro_tree_{sys_key}.json` so every game resolves its exact
       existing `.png` filename with `(France) -> (Europe) -> (World) -> (USA)` priority
       and ZERO HTTP 404 errors.
    """

    source_id = "libretro_wikidata"

    def __init__(self) -> None:
        self.store = LiveReferentialStore(self.source_id, ttl_seconds=86400)
        self._tree_stores: Dict[str, LiveReferentialStore] = {}

    def _get_tree_store(self, sys_key: str) -> LiveReferentialStore:
        canon = resolve_canonical_system(sys_key)
        if canon not in self._tree_stores:
            self._tree_stores[canon] = LiveReferentialStore(f"libretro_tree_{canon}", ttl_seconds=604800)
        return self._tree_stores[canon]

    async def sync_system_libretro_tree(self, client: Any, sys_key: str) -> Dict[str, List[str]]:
        """
        Fetch and cache the exact `.png` filenames in `Named_Boxarts`, `Named_Titles`, and `Named_Snaps`
        for `sys_key` from `api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master`.
        """
        canon = resolve_canonical_system(sys_key)
        tstore = self._get_tree_store(canon)
        cached = tstore.load()
        folders = (cached.get("metadata") or {}).get("folders")
        if isinstance(folders, dict) and folders.get("Named_Boxarts"):
            return folders

        smeta = RETRO_SYSTEM_CATALOG.get(canon) or RETRO_SYSTEM_CATALOG["nes"]
        repo = smeta.get("libretro_repo") or "Nintendo_-_Nintendo_Entertainment_System"
        result_folders: Dict[str, List[str]] = {
            "Named_Boxarts": [],
            "Named_Titles": [],
            "Named_Snaps": [],
        }
        try:
            root_url = f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master"
            resp = await client.get(root_url, timeout=6.0)
            if resp.status_code == 200:
                tree_nodes = resp.json().get("tree") or []
                for node in tree_nodes:
                    folder_name = str(node.get("path") or "")
                    sha = str(node.get("sha") or "")
                    if folder_name in result_folders and sha:
                        sub_url = f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/{sha}"
                        sub_resp = await client.get(sub_url, timeout=7.0)
                        if sub_resp.status_code == 200:
                            files = [
                                str(item.get("path"))
                                for item in (sub_resp.json().get("tree") or [])
                                if str(item.get("path") or "").endswith(".png")
                            ]
                            result_folders[folder_name] = files
            total_files = sum(len(v) for v in result_folders.values())
            if total_files > 0:
                tstore.save(
                    tokens=result_folders["Named_Boxarts"][:500],
                    metadata={"repo": repo, "folders": result_folders},
                )
                logger.info(
                    "[REFERENTIAL:libretro] Indexed %d verified PNG assets for system '%s' (%s)",
                    total_files,
                    canon,
                    repo,
                )
        except Exception as exc:
            logger.debug("[REFERENTIAL:libretro] Tree index skipped for %s: %s", canon, exc)
        return result_folders

    async def resolve_verified_libretro_urls(
        self,
        client: Any,
        sys_key: str,
        game_title: str,
        lang: str = "fr",
    ) -> Dict[str, Optional[str]]:
        """
        Match `game_title` against the cached Libretro file tree for `sys_key` and return
        verified HTTP 200 URLs for `boxart`, `title`, and `snap` with regional priority
        `(France) -> (Europe) -> (World) -> (USA)`.
        """
        canon = resolve_canonical_system(sys_key)
        smeta = RETRO_SYSTEM_CATALOG.get(canon) or RETRO_SYSTEM_CATALOG["nes"]
        repo = smeta.get("libretro_repo") or "Nintendo_-_Nintendo_Entertainment_System"
        folders = await self.sync_system_libretro_tree(client, canon)
        target_key = normalize_game_lookup_key(game_title)
        if not target_key:
            return {"boxart": None, "title": None, "snap": None}

        # Also prepare base key without subtitle (before ':' or ' - ')
        short_title = re.split(r"[:\-–]", game_title)[0].strip()
        short_key = normalize_game_lookup_key(short_title) if len(short_title) >= 4 else target_key

        out: Dict[str, Optional[str]] = {"boxart": None, "title": None, "snap": None}
        folder_map = {
            "boxart": "Named_Boxarts",
            "title": "Named_Titles",
            "snap": "Named_Snaps",
        }

        for slot_key, folder_name in folder_map.items():
            file_list = folders.get(folder_name) or []
            if not file_list:
                continue
            exact_matches: List[Tuple[int, str]] = []
            prefix_matches: List[Tuple[int, str]] = []
            for fname in file_list:
                fkey = normalize_game_lookup_key(fname)
                if not fkey:
                    continue
                if fkey == target_key or (short_key and fkey == short_key):
                    exact_matches.append((score_libretro_filename_for_lang(fname, lang), fname))
                elif len(target_key) >= 5 and (fkey.startswith(target_key) or target_key.startswith(fkey)):
                    prefix_matches.append((score_libretro_filename_for_lang(fname, lang) - 15, fname))

            chosen_pool = exact_matches if exact_matches else prefix_matches
            if chosen_pool:
                chosen_pool.sort(key=lambda item: item[0], reverse=True)
                best_fname = chosen_pool[0][1]
                encoded = urllib.parse.quote(best_fname)
                out[slot_key] = (
                    f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/"
                    f"{folder_name}/{encoded}"
                )
        return out

    async def harvest_wikidata_games(
        self,
        client: Any,
        sys_key: str,
        genre_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query Wikidata SPARQL for strictly verified Video Games (`wd:Q7889`) released on
        platform `sys_key` (`wdt:P400`) and matching `genre_key` (`wdt:P136/wdt:P279*`),
        returning localized titles (`fr`, `en`, `es`), year, and Wikipedia sitelinks.
        """
        canon_sys = resolve_canonical_system(sys_key)
        canon_genre = resolve_canonical_genre("games", genre_key) if genre_key else ""
        cache_bucket = f"{canon_sys}:{canon_genre or 'all'}"

        data = self.store.load()
        by_bucket = (data.get("metadata") or {}).get("by_system_genre") or {}
        cached_list = by_bucket.get(cache_bucket)
        if isinstance(cached_list, list) and len(cached_list) >= 5:
            return cached_list

        smeta = RETRO_SYSTEM_CATALOG.get(canon_sys) or RETRO_SYSTEM_CATALOG["nes"]
        platform_qid = smeta.get("wikidata_qid") or "Q172742"
        gmeta = GAME_GENRE_CATALOG.get(canon_genre) if canon_genre else None
        genre_qid = gmeta.get("wikidata_qid") if gmeta else None

        genre_clause = (
            f"?game wdt:P136/wdt:P279* wd:{genre_qid} . "
            if genre_qid
            else ""
        )

        sparql = (
            "SELECT DISTINCT ?game ?titleFr ?titleEn ?year ?frTitle ?enTitle WHERE { "
            f"?game wdt:P31/wdt:P279* wd:Q7889 ; wdt:P400 wd:{platform_qid} . "
            f"{genre_clause}"
            "OPTIONAL { ?game wdt:P577 ?pubDate . BIND(YEAR(?pubDate) AS ?year) } "
            "OPTIONAL { ?game rdfs:label ?titleFr . FILTER(LANG(?titleFr) = 'fr') } "
            "OPTIONAL { ?game rdfs:label ?titleEn . FILTER(LANG(?titleEn) = 'en') } "
            "OPTIONAL { ?frArticle schema:about ?game ; schema:isPartOf <https://fr.wikipedia.org/> ; schema:name ?frTitle . } "
            "OPTIONAL { ?enArticle schema:about ?game ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?enTitle . } "
            "} LIMIT 100"
        )

        games_by_qid: Dict[str, Dict[str, Any]] = {}
        try:
            resp = await client.get(
                "https://query.wikidata.org/sparql",
                params={"format": "json", "query": sparql},
                headers={"Accept": "application/sparql-results+json"},
                timeout=7.0,
            )
            if resp.status_code == 200:
                bindings = (resp.json().get("results") or {}).get("bindings") or []
                for b in bindings:
                    uri = str(((b.get("game") or {}).get("value")) or "")
                    qid = uri.rsplit("/", 1)[-1] if "/" in uri else uri
                    if not qid.startswith("Q"):
                        continue
                    title_en = str(((b.get("titleEn") or {}).get("value")) or "").strip()
                    title_fr = str(((b.get("titleFr") or {}).get("value")) or "").strip()
                    if not title_en and not title_fr:
                        continue
                    # Skip raw QID labels
                    if re.fullmatch(r"Q\d+", title_en or title_fr):
                        continue
                    yr_raw = str(((b.get("year") or {}).get("value")) or "").strip()
                    yr = yr_raw[:4] if len(yr_raw) >= 4 and yr_raw[:4].isdigit() else ""
                    fr_wiki = str(((b.get("frTitle") or {}).get("value")) or "").strip()
                    en_wiki = str(((b.get("enTitle") or {}).get("value")) or "").strip()

                    existing = games_by_qid.get(qid)
                    if existing is None:
                        games_by_qid[qid] = {
                            "qid": qid,
                            "system": canon_sys,
                            "genre": canon_genre or "action",
                            "title_en": title_en or title_fr,
                            "title_fr": title_fr or title_en,
                            "year": yr,
                            "fr_wiki": fr_wiki,
                            "en_wiki": en_wiki,
                        }
                    else:
                        if yr and (not existing["year"] or yr < existing["year"]):
                            existing["year"] = yr
                        if fr_wiki and not existing["fr_wiki"]:
                            existing["fr_wiki"] = fr_wiki
                        if en_wiki and not existing["en_wiki"]:
                            existing["en_wiki"] = en_wiki
        except Exception as exc:
            logger.debug("[REFERENTIAL:wikidata] SPARQL query skipped for %s/%s: %s", canon_sys, canon_genre, exc)

        harvested = list(games_by_qid.values())
        if harvested:
            by_bucket[cache_bucket] = harvested
            tokens = [f"{canon_sys}:{g['qid']}" for g in harvested]
            self.store.save(tokens=tokens, metadata={"by_system_genre": by_bucket})
            logger.info(
                "[REFERENTIAL:wikidata] Harvested %d verified '%s' games for genre '%s'",
                len(harvested),
                canon_sys,
                canon_genre or "all",
            )
        return harvested

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        await self.harvest_wikidata_games(client, "nes", "platform")
        return self.store.load()

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        return list(ref.get("tokens") or [])[:count]


class ArcadeItaliaReferentialStrategy(IScraperReferentialStrategy):
    """
    Live Referential Strategy for `ArcadeItaliaAdapter` (`https://adb.arcadeitalia.net`):
    - Strictly dedicated to Arcade / MAME / NeoGeo / CPS systems (never home consoles).
    - Always uses HTTPS (`https://adb.arcadeitalia.net`) to avoid `301 -> 400 Bad Request`.
    - Dynamically harvests and updates valid MAME `game_name` ROM identifiers from:
      1. ArcadeItalia's live search index (`https://adb.arcadeitalia.net/lista_mame.php?ricerca={token}&arcade_only=1&lang=en`)
      2. Wikidata's live SPARQL endpoint (`wdt:P2451` = official ArcadeItalia / MAME ROM property)
    """

    source_id = "arcadeitalia"

    def __init__(self) -> None:
        self.store = LiveReferentialStore(self.source_id, ttl_seconds=43200)

    async def _harvest_from_lista_mame(self, client: Any, token: str) -> List[str]:
        """Scrape valid MAME `game_name` identifiers from ArcadeItalia's HTTPS `lista_mame.php` search."""
        discovered: List[str] = []
        try:
            resp = await client.get(
                "https://adb.arcadeitalia.net/lista_mame.php",
                params={"ricerca": token, "arcade_only": "1", "lang": "en"},
                timeout=5.0,
            )
            if resp.status_code == 200 and resp.text:
                for match in _ADB_ROM_LINK_RE.finditer(resp.text):
                    rom = match.group(1).lower().strip()
                    if rom and not rom.startswith(_IGNORED_MAME_PREFIXES) and rom not in discovered:
                        discovered.append(rom)
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
        for _ in range(2):
            syllable = random_search_syllable()
            batch = await self._harvest_from_lista_mame(client, syllable)
            tokens.extend(batch)
            if len(tokens) >= 40:
                break

        if len(tokens) < 25:
            wd_roms = await self._harvest_from_wikidata_p2451(client)
            tokens.extend(wd_roms)

        saved = self.store.save(
            tokens=tokens,
            metadata={
                "last_sync_count": len(tokens),
                "supported_systems": ["arcade", "mame", "neogeo", "cps1", "cps2", "cps3"],
            },
        )
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


libretro_wikidata_referential_strategy = LibretroWikidataReferentialStrategy()
arcadeitalia_referential_strategy = ArcadeItaliaReferentialStrategy()
jikan_referential_strategy = JikanReferentialStrategy()
anilist_referential_strategy = AniListReferentialStrategy()
tvmaze_referential_strategy = TvMazeReferentialStrategy()
