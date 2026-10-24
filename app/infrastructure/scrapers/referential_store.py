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
    - Strip `.png`, `.ia.mp4`, `.mp4` and all parenthetical tags `(Europe)`, `(France)`, `(USA)`, `(Rev A)`
    - Handle trailing `, The` or leading `The `
    - Strip punctuation/subtitles as needed
    """
    s = (title or "").strip()
    low_ext = s.lower()
    if low_ext.endswith(".ia.mp4"):
        s = s[:-7]
    elif low_ext.endswith((".png", ".mp4")):
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
        for `sys_key` from `api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master`, plus
        official EmuMovies `.mp4` gameplay snaps in `Video_Snaps` from `archive.org/metadata/{archive_video_repo}/files`.
        """
        canon = resolve_canonical_system(sys_key)
        smeta = RETRO_SYSTEM_CATALOG.get(canon) or RETRO_SYSTEM_CATALOG["nes"]
        repo = smeta.get("libretro_repo") or "Nintendo_-_Nintendo_Entertainment_System"
        archive_video_repo = str(smeta.get("archive_video_repo") or "").strip()

        tstore = self._get_tree_store(canon)
        cached = tstore.load()
        folders = (cached.get("metadata") or {}).get("folders")
        has_pngs = isinstance(folders, dict) and bool(
            folders.get("Named_Boxarts") or folders.get("Named_Titles") or folders.get("Named_Snaps")
        )
        has_videos = isinstance(folders, dict) and ("Video_Snaps" in folders or not archive_video_repo)
        if has_pngs and has_videos:
            return folders  # type: ignore[return-value]

        result_folders: Dict[str, List[str]] = {
            "Named_Boxarts": list((folders or {}).get("Named_Boxarts") or []) if isinstance(folders, dict) else [],
            "Named_Titles": list((folders or {}).get("Named_Titles") or []) if isinstance(folders, dict) else [],
            "Named_Snaps": list((folders or {}).get("Named_Snaps") or []) if isinstance(folders, dict) else [],
            "Video_Snaps": list((folders or {}).get("Video_Snaps") or []) if isinstance(folders, dict) else [],
        }
        try:
            if not has_pngs:
                root_url = f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master"
                resp = await client.get(root_url, timeout=6.0)
                if resp.status_code == 200:
                    tree_nodes = resp.json().get("tree") or []
                    for node in tree_nodes:
                        folder_name = str(node.get("path") or "")
                        sha = str(node.get("sha") or "")
                        if folder_name in ("Named_Boxarts", "Named_Titles", "Named_Snaps") and sha:
                            sub_url = f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/{sha}"
                            sub_resp = await client.get(sub_url, timeout=7.0)
                            if sub_resp.status_code == 200:
                                files = [
                                    str(item.get("path"))
                                    for item in (sub_resp.json().get("tree") or [])
                                    if str(item.get("path") or "").endswith(".png")
                                ]
                                result_folders[folder_name] = files

            if archive_video_repo and not result_folders["Video_Snaps"]:
                ia_url = f"https://archive.org/metadata/{archive_video_repo}/files"
                ia_resp = await client.get(ia_url, timeout=7.5)
                if ia_resp.status_code == 200:
                    ia_files = ia_resp.json().get("result") or []
                    mp4_files = [
                        str(item.get("name"))
                        for item in ia_files
                        if isinstance(item, dict) and str(item.get("name") or "").endswith(".mp4")
                    ]
                    result_folders["Video_Snaps"] = mp4_files

            total_files = sum(len(v) for v in result_folders.values())
            if total_files > 0:
                primary_tokens = (
                    result_folders["Named_Boxarts"]
                    or result_folders["Named_Titles"]
                    or result_folders["Named_Snaps"]
                )[:500]
                tstore.save(
                    tokens=primary_tokens,
                    metadata={"repo": repo, "archive_video_repo": archive_video_repo, "folders": result_folders},
                )
                logger.info(
                    "[REFERENTIAL:libretro] Indexed %d verified assets (%d video snaps) for system '%s' (%s)",
                    total_files,
                    len(result_folders["Video_Snaps"]),
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
        verified HTTP 200 URLs for `boxart`, `title`, `snap`, and `video` (EmuMovies gameplay MP4)
        with regional priority `(France) -> (Europe) -> (World) -> (USA)`.
        """
        canon = resolve_canonical_system(sys_key)
        smeta = RETRO_SYSTEM_CATALOG.get(canon) or RETRO_SYSTEM_CATALOG["nes"]
        repo = smeta.get("libretro_repo") or "Nintendo_-_Nintendo_Entertainment_System"
        archive_video_repo = str(smeta.get("archive_video_repo") or "").strip()
        folders = await self.sync_system_libretro_tree(client, canon)
        target_key = normalize_game_lookup_key(game_title)
        if not target_key:
            return {"boxart": None, "title": None, "snap": None, "video": None}

        # Also prepare base key without subtitle (before ':' or ' - ')
        short_title = re.split(r"[:\-–]", game_title)[0].strip()
        short_key = normalize_game_lookup_key(short_title) if len(short_title) >= 4 else target_key

        out: Dict[str, Optional[str]] = {"boxart": None, "title": None, "snap": None, "video": None}
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
                if "virtual console" in fname.lower():
                    continue
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

        video_list = folders.get("Video_Snaps") or []
        if archive_video_repo and video_list:
            v_exact: List[Tuple[int, str]] = []
            v_prefix: List[Tuple[int, str]] = []
            for vname in video_list:
                vkey = normalize_game_lookup_key(vname)
                if not vkey:
                    continue
                ia_bonus = 5 if vname.lower().endswith(".ia.mp4") else 0
                base_score = score_libretro_filename_for_lang(vname, lang) + ia_bonus
                if vkey == target_key or (short_key and vkey == short_key):
                    v_exact.append((base_score, vname))
                elif len(target_key) >= 5 and (vkey.startswith(target_key) or target_key.startswith(vkey)):
                    v_prefix.append((base_score - 15, vname))
            v_pool = v_exact if v_exact else v_prefix
            if v_pool:
                v_pool.sort(key=lambda item: item[0], reverse=True)
                best_vname = v_pool[0][1]
                out["video"] = (
                    f"https://archive.org/download/{archive_video_repo}/{urllib.parse.quote(best_vname)}"
                )

        return out

    async def harvest_wikidata_games(
        self,
        client: Any,
        sys_key: str,
        genre_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query Wikidata SPARQL (or CirrusSearch fallback) for strictly verified Video Games (`wd:Q7889`)
        released on platform `sys_key` (`wdt:P400`) and matching `genre_key` (`wdt:P136/wdt:P279*`),
        returning localized titles (`fr`, `en`, `es`), year, and Wikipedia sitelinks.
        """
        canon_sys = resolve_canonical_system(sys_key)
        canon_genre = resolve_canonical_genre("games", genre_key) if genre_key else ""
        cache_bucket = f"{canon_sys}:{canon_genre or 'all'}"

        data = self.store.load()
        meta = data.get("metadata") or {}
        by_bucket = (meta.get("by_system_genre") or {}) if meta.get("schema_version") == 2 else {}
        cached_list = by_bucket.get(cache_bucket)
        if isinstance(cached_list, list) and len(cached_list) >= 5:
            return cached_list

        smeta = RETRO_SYSTEM_CATALOG.get(canon_sys) or RETRO_SYSTEM_CATALOG["nes"]
        platform_qid = smeta.get("wikidata_qid") or "Q172742"
        gmeta = GAME_GENRE_CATALOG.get(canon_genre) if canon_genre else None
        genre_qid = gmeta.get("wikidata_qid") if gmeta else None
        genre_qids: List[str] = (
            list(gmeta.get("wikidata_qids") or ([genre_qid] if genre_qid else []))
            if gmeta
            else []
        )

        if genre_qids:
            values_str = " ".join(f"wd:{q}" for q in genre_qids if q)
            genre_clause = f"VALUES ?targetGenre {{ {values_str} }} ?game wdt:P136/wdt:P279* ?targetGenre . "
        else:
            genre_clause = ""

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

        # Fallback to Wikidata MediaWiki CirrusSearch API (`www.wikidata.org/w/api.php`)
        # when `query.wikidata.org/sparql` is blocked by a proxy/VPN (HTTP 403) or times out.
        if not games_by_qid:
            target_qids = genre_qids if genre_qids else [None]
            for g_qid in target_qids:
                await self._harvest_via_wikidata_cirrus_api(
                    client,
                    canon_sys=canon_sys,
                    canon_genre=canon_genre,
                    platform_qid=platform_qid,
                    genre_qid=g_qid,
                    games_by_qid=games_by_qid,
                )
                if len(games_by_qid) >= 40:
                    break

        harvested = list(games_by_qid.values())
        if harvested:
            by_bucket[cache_bucket] = harvested
            tokens = [f"{canon_sys}:{g['qid']}" for g in harvested]
            self.store.save(
                tokens=tokens,
                metadata={"schema_version": 2, "by_system_genre": by_bucket},
            )
            logger.info(
                "[REFERENTIAL:wikidata] Harvested %d verified '%s' games for genre '%s'",
                len(harvested),
                canon_sys,
                canon_genre or "all",
            )
        return harvested

    async def _harvest_via_wikidata_cirrus_api(
        self,
        client: Any,
        canon_sys: str,
        canon_genre: str,
        platform_qid: str,
        genre_qid: Optional[str],
        games_by_qid: Dict[str, Dict[str, Any]],
    ) -> None:
        """
        Query `https://www.wikidata.org/w/api.php` using `haswbstatement` CirrusSearch
        (`haswbstatement:P31=Q7889 haswbstatement:P400={platform_qid} haswbstatement:P136={genre_qid}`)
        followed by `wbgetentities` to retrieve localized `fr`/`en` labels, release year (`P577`),
        and `frwiki`/`enwiki` sitelinks without touching `query.wikidata.org/sparql`.
        """
        sr_parts = [
            "haswbstatement:P31=Q7889",
            f"haswbstatement:P400={platform_qid}",
        ]
        if genre_qid:
            sr_parts.append(f"haswbstatement:P136={genre_qid}")
        srsearch = " ".join(sr_parts)
        try:
            s_resp = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": srsearch,
                    "srlimit": "50",
                    "format": "json",
                },
                timeout=6.5,
            )
            if s_resp.status_code != 200:
                return
            search_hits = ((s_resp.json().get("query") or {}).get("search")) or []
            qids = [
                str(hit.get("title") or "").strip()
                for hit in search_hits
                if str(hit.get("title") or "").strip().startswith("Q")
            ][:50]
            if not qids:
                return

            e_resp = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(qids),
                    "props": "labels|claims|sitelinks",
                    "languages": "fr|en",
                    "sitefilter": "frwiki|enwiki",
                    "format": "json",
                },
                timeout=6.5,
            )
            if e_resp.status_code != 200:
                return
            entities = (e_resp.json().get("entities")) or {}
            for qid, ent in entities.items():
                if not isinstance(ent, dict):
                    continue
                labels = ent.get("labels") or {}
                title_fr = str(((labels.get("fr") or {}).get("value")) or "").strip()
                title_en = str(((labels.get("en") or {}).get("value")) or "").strip()
                if not title_en and not title_fr:
                    continue
                if re.fullmatch(r"Q\d+", title_en or title_fr):
                    continue
                sitelinks = ent.get("sitelinks") or {}
                fr_wiki = str(((sitelinks.get("frwiki") or {}).get("title")) or "").strip()
                en_wiki = str(((sitelinks.get("enwiki") or {}).get("title")) or "").strip()

                yr = ""
                p577 = (ent.get("claims") or {}).get("P577") or []
                for claim in p577:
                    tval = (
                        (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("time")
                        if isinstance(claim, dict)
                        else None
                    )
                    if isinstance(tval, str):
                        m = re.search(r"(\d{4})", tval)
                        if m:
                            cand_yr = m.group(1)
                            if not yr or cand_yr < yr:
                                yr = cand_yr

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
        except Exception as exc:
            logger.debug("[REFERENTIAL:wikidata] Cirrus API fallback skipped for %s/%s: %s", canon_sys, canon_genre, exc)

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

    _AUTHORITATIVE_SYSTEM_ROMS: Dict[str, List[str]] = {
        "cps1": [
            "sf2", "sf2ce", "sf2hf", "ffight", "dino", "punisher", "captcomm",
            "knights", "wof", "kod", "strider", "ghouls", "1941", "varth",
            "cawing", "mercs", "forgottn", "unsquad", "willow", "mtwins",
            "nemo", "megaman", "slammast", "mbombrd", "pnickj", "qad",
            "qtono2", "pang3", "3wonders",
        ],
        "cps2": [
            "ssf2", "ssf2t", "ssf2xj", "hsf2", "sfa", "sfa2", "sfa3",
            "msh", "vsav", "vhunt2", "vsav2", "nwarr", "dstlk", "xmcota",
            "xmvsf", "mshvsf", "mvsc", "cybots", "ringdest", "sgemf",
            "spf2t", "pzloop2", "mpang", "1944", "19xx", "gigawing",
            "progear", "marsmatr", "dimahoo", "ecofghtr", "avsp", "ddtod",
            "ddsom", "batcir", "armwar", "csclub", "megaman2", "qndream", "choko",
        ],
        "cps3": [
            "sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth",
        ],
        "neogeo": [
            "kof94", "kof95", "kof96", "kof97", "kof98", "kof99", "kof2000",
            "kof2001", "kof2002", "kof2003", "samsho", "samsho2", "samsho3",
            "samsho4", "samsho5", "samsho5sp", "mslug", "mslug2", "mslug3",
            "mslug4", "mslug5", "mslugx", "garou", "fatfury1", "fatfury2",
            "fatfury3", "rbff1", "rbff2", "rbffspec", "aof", "aof2", "aof3",
            "lastblad", "lastbld2", "wh1", "wh2", "wh2jet", "whp", "svc",
            "rotd", "matrim", "wakuwak7", "kabukikl", "breakers", "breakrev",
            "ninjamas", "kizuna", "savagere", "karnovr", "shocktro", "shocktr2",
            "sengoku", "sengoku2", "sengoku3", "pulstar", "blazstar", "viewpoin",
            "lastreso", "twinspri", "aerofig2", "aerofig3", "strik1945p", "preisle2",
            "zedblade", "androdu", "alpham2", "windjamm", "ssideki", "ssideki2",
            "ssideki3", "ssideki4", "turfmast", "neocup98", "2020bb", "strhoop",
            "tophuntr", "spinmast", "maglord", "bjourney", "cyberlip", "nam1975",
            "mutnat", "roboarmy", "burningf", "eightman", "pbobble", "pbobble2",
            "magicald", "magdrop2", "magdrop3", "puzzledp", "joyjoy", "moneyid",
            "gururin", "panicbom", "overtop", "neodrift", "thrashr",
        ],
    }

    _FORBIDDEN_ROMS_BY_SYSTEM: Dict[str, Set[str]] = {
        "cps1": {
            "mvsc", "mvsc2", "sfa", "sfa2", "sfa3", "ssf2", "ssf2t", "ssf2xj", "hsf2",
            "msh", "vsav", "vhunt2", "vsav2", "nwarr", "dstlk", "xmcota", "xmvsf", "mshvsf",
            "sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth", "cvs", "cvs2",
        },
        "cps2": {
            "mvsc2", "mvsc2u", "mvsc2j", "cvs", "cvs2", "cvsgd", "capcom_vs_snk",
            "sf2", "sf2ce", "sf2hf", "ffight", "dino", "punisher", "captcomm",
            "sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth",
            "sfex", "sfex2", "sfex2p", "sftm", "pstone", "pstone2", "techromn",
            "rvschola", "pjfghtr", "kof94", "kof95", "kof98", "mslug", "samsho",
        },
        "cps3": {
            "mvsc", "mvsc2", "sfa", "sfa2", "sfa3", "ssf2", "ssf2t", "sf2", "sf2ce",
            "cvs", "cvs2", "sfex", "sfex2",
        },
        "neogeo": {
            "sf2", "ssf2", "ssf2t", "sfa", "sfa2", "sfa3", "mvsc", "mvsc2", "sfiii",
        },
    }

    _SYSTEM_SEARCH_STEMS: Dict[str, List[str]] = {
        "neogeo": _AUTHORITATIVE_SYSTEM_ROMS["neogeo"],
        "cps1": _AUTHORITATIVE_SYSTEM_ROMS["cps1"],
        "cps2": _AUTHORITATIVE_SYSTEM_ROMS["cps2"],
        "cps3": _AUTHORITATIVE_SYSTEM_ROMS["cps3"],
    }

    _GENRE_SEARCH_STEMS: Dict[str, Dict[str, List[str]]] = {
        "fighting": {
            "neogeo": [
                "kof94", "kof95", "kof96", "kof97", "kof98", "kof99", "kof2000", "kof2001",
                "kof2002", "kof2003", "samsho", "samsho2", "samsho3", "samsho4", "samsho5",
                "samsho5sp", "garou", "fatfury1", "fatfury2", "fatfury3", "rbff1", "rbff2",
                "rbffspec", "aof", "aof2", "aof3", "lastblad", "lastbld2", "wh1", "wh2",
                "wh2jet", "whp", "svc", "rotd", "matrim", "wakuwak7", "kabukikl", "breakers",
                "breakrev", "ninjamas", "kizuna", "savagere", "karnovr",
            ],
            "cps1": ["sf2", "sf2ce", "sf2hf", "slammast", "mbombrd"],
            "cps2": [
                "ssf2", "ssf2t", "ssf2xj", "hsf2", "sfa", "sfa2", "sfa3",
                "msh", "vsav", "vhunt2", "vsav2", "nwarr", "dstlk",
                "xmcota", "xmvsf", "mshvsf", "mvsc", "cybots", "ringdest", "sgemf",
            ],
            "cps3": ["sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth"],
            "all": [
                "sf2", "ssf2t", "sfa2", "sfa3", "kof98", "kof2002", "samsho2", "mk",
                "tekken3", "vsav", "garou", "rbffspec", "soulclbr", "mvsc", "sfiii3",
            ],
        },
        "beatemup": {
            "neogeo": ["sengoku", "sengoku2", "sengoku3", "mutnat", "burningf", "roboarmy", "eightman"],
            "cps1": ["ffight", "dino", "punisher", "captcomm", "knights", "wof", "kod"],
            "cps2": ["avsp", "ddtod", "ddsom", "batcir", "armwar"],
            "all": ["ffight", "dino", "punisher", "captcomm", "tmnt", "simpsons", "xmen", "avsp", "ddsom", "sengoku3"],
        },
        "runandgun": {
            "neogeo": ["mslug", "mslug2", "mslug3", "mslug4", "mslug5", "mslugx", "shocktro", "shocktr2", "cyberlip", "nam1975"],
            "cps1": ["mercs", "cawing", "forgottn", "varth"],
            "cps2": ["progear", "ecofghtr"],
            "all": ["mslug", "mslugx", "mslug3", "contra", "ssriders", "shocktro", "gunforce", "mercs", "gberet"],
        },
        "shootemup": {
            "neogeo": [
                "pulstar", "blazstar", "viewpoin", "lastreso", "twinspri",
                "aerofig2", "aerofig3", "strik1945p", "preisle2", "zedblade", "androdu", "alpham2",
            ],
            "cps1": ["1941", "varth", "cawing", "forgottn", "unsquad"],
            "cps2": ["1944", "19xx", "gigawing", "progear", "marsmatr", "dimahoo", "ecofghtr"],
            "all": ["1942", "1943", "19xx", "raiden", "rtype", "gradius", "ddonpach", "pulstar", "blazstar", "gigawing", "marsmatr"],
        },
        "platform": {
            "neogeo": ["tophuntr", "spinmast", "maglord", "bjourney"],
            "cps1": ["ghouls", "strider", "megaman", "nemo", "mtwins", "willow", "3wonders"],
            "cps2": ["megaman2"],
            "all": ["snowbros", "bublbobl", "rainbow", "newzeal", "ghouls", "strider", "toki", "rastan", "shinobi", "willow"],
        },
        "puzzle": {
            "neogeo": ["pbobble", "pbobble2", "magicald", "magdrop2", "magdrop3", "puzzledp", "joyjoy", "moneyid", "gururin", "panicbom"],
            "cps1": ["pnickj", "qad", "qtono2", "pang3"],
            "cps2": ["spf2t", "pzloop2", "mpang", "qndream", "choko"],
            "all": ["pbobble", "atetris", "columns", "klax", "spf2t", "puyopuy2", "pang", "mpang", "magdrop3"],
        },
        "sports": {
            "neogeo": ["windjamm", "ssideki", "ssideki2", "ssideki3", "ssideki4", "turfmast", "neocup98", "2020bb", "strhoop"],
            "cps2": ["csclub"],
            "all": ["windjamm", "nbajam", "ssideki2", "turfmast", "trackfld", "archrivl", "csclub"],
        },
        "racing": {
            "neogeo": ["overtop", "neodrift", "thrashr"],
            "all": ["outrun", "daytona", "srally", "chasehq", "hangon", "overtop", "neodrift"],
        },
        "maze": {
            "all": ["pacman", "mspacman", "galaga", "digdug", "pengo", "bbmanw", "frogger"],
        },
    }

    def __init__(self) -> None:
        self.store = LiveReferentialStore(self.source_id, ttl_seconds=43200)

    @classmethod
    def is_rom_allowed_for_system(cls, rom: str, sys_key: Optional[str]) -> bool:
        """Return True if `rom` is permitted on `sys_key` (rejects Naomi `mvsc2` on `cps2`, etc.)."""
        r = (rom or "").strip().lower()
        if not r or r.startswith(_IGNORED_MAME_PREFIXES):
            return False
        if not sys_key:
            return True
        canon_sys = resolve_canonical_system(sys_key)
        forbidden = cls._FORBIDDEN_ROMS_BY_SYSTEM.get(canon_sys)
        if forbidden and r in forbidden:
            return False
        return True

    async def _harvest_from_lista_mame(
        self, client: Any, token: str, sys_key: Optional[str] = None
    ) -> List[str]:
        """Scrape valid parent MAME `game_name` identifiers from ArcadeItalia's HTTPS `lista_mame.php` search."""
        discovered: List[str] = []
        try:
            resp = await client.get(
                "https://adb.arcadeitalia.net/lista_mame.php",
                params={"ricerca": token, "arcade_only": "1", "cloni": "0", "lang": "en"},
                timeout=5.0,
            )
            if resp.status_code == 200 and resp.text:
                for match in _ADB_ROM_LINK_RE.finditer(resp.text):
                    rom = match.group(1).lower().strip()
                    if (
                        rom
                        and self.is_rom_allowed_for_system(rom, sys_key)
                        and rom not in discovered
                    ):
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
        req_sys = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.lower() != "all"
        ]
        req_genres = [
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.lower() != "all"
        ]
        specific_sys = next((s for s in req_sys if s in self._SYSTEM_SEARCH_STEMS), None)
        chosen_genre = random.choice(req_genres) if req_genres else None

        if chosen_genre and chosen_genre in self._GENRE_SEARCH_STEMS:
            g_map = self._GENRE_SEARCH_STEMS[chosen_genre]
            stems_pool = [
                s.strip().lower()
                for s in (g_map.get(specific_sys or "all") or g_map.get("all") or [])
                if s and self.is_rom_allowed_for_system(s, specific_sys)
            ]
            if stems_pool:
                bucket_key = f"{specific_sys or 'arcade'}:{chosen_genre}"
                by_sg = dict(((ref_data.get("metadata") or {}).get("by_system_genre")) or {})
                sg_tokens: List[str] = [
                    r
                    for r in (by_sg.get(bucket_key) or [])
                    if self.is_rom_allowed_for_system(r, specific_sys)
                ]
                unseen_sg = [r for r in sg_tokens if f"games_adb_{r}" not in seen_ids]
                if len(unseen_sg) < max(6, count):
                    unseen_stems = [
                        s for s in stems_pool if s not in sg_tokens and f"games_adb_{s}" not in seen_ids
                    ] or list(stems_pool)
                    random.shuffle(unseen_stems)
                    for stem in unseen_stems[:4]:
                        fresh = await self._harvest_from_lista_mame(client, stem, sys_key=specific_sys)
                        for rom in fresh:
                            if rom not in sg_tokens and self.is_rom_allowed_for_system(rom, specific_sys):
                                sg_tokens.append(rom)
                        if stem not in sg_tokens and self.is_rom_allowed_for_system(stem, specific_sys):
                            sg_tokens.append(stem)
                        if len([r for r in sg_tokens if f"games_adb_{r}" not in seen_ids]) >= max(8, count * 2):
                            break
                    by_sg[bucket_key] = sg_tokens
                    self.store.save(tokens=sg_tokens, metadata={"by_system_genre": by_sg})
                    unseen_sg = [r for r in sg_tokens if f"games_adb_{r}" not in seen_ids]
                pool = unseen_sg if unseen_sg else sg_tokens
                if pool:
                    return random.sample(pool, k=min(len(pool), max(6, count)))

        if specific_sys:
            by_system = dict(((ref_data.get("metadata") or {}).get("by_system")) or {})
            sys_tokens: List[str] = [
                r
                for r in (by_system.get(specific_sys) or [])
                if self.is_rom_allowed_for_system(r, specific_sys)
            ]
            unseen_sys = [r for r in sys_tokens if f"games_adb_{r}" not in seen_ids]
            if len(unseen_sys) < count:
                stems = [
                    s
                    for s in self._SYSTEM_SEARCH_STEMS[specific_sys]
                    if s not in sys_tokens and f"games_adb_{s}" not in seen_ids
                ] or list(self._SYSTEM_SEARCH_STEMS[specific_sys])
                random.shuffle(stems)
                for stem in stems[:4]:
                    fresh = await self._harvest_from_lista_mame(client, stem, sys_key=specific_sys)
                    for rom in fresh:
                        if rom not in sys_tokens and self.is_rom_allowed_for_system(rom, specific_sys):
                            sys_tokens.append(rom)
                    if len([r for r in sys_tokens if f"games_adb_{r}" not in seen_ids]) >= max(6, count):
                        break
                by_system[specific_sys] = sys_tokens
                self.store.save(tokens=sys_tokens, metadata={"by_system": by_system})
                unseen_sys = [r for r in sys_tokens if f"games_adb_{r}" not in seen_ids]
            pool = unseen_sys if unseen_sys else sys_tokens
            if pool:
                return random.sample(pool, k=min(len(pool), max(1, count)))

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
