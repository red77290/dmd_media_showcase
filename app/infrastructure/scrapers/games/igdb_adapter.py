"""
Primary Games Scraper Adapter: IGDB v4 (`https://api.igdb.com/v4/games`) via Twitch OAuth2.

Implements `IScraperSource` with:
- In-memory Twitch `client_credentials` OAuth2 token caching (`id.twitch.tv/oauth2/token`).
- 60-second circuit-breaker cooldown on upstream auth/rate-limit/5xx errors.
- Full Apicalypse search and random discovery across all 64 retro & arcade platforms,
  16 canonical game genres, and release decades.
- Player shorthand expansion (`sf2x`, `kof98`, `3s`, `sotn`) and alternative title matching.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import (
    CANONICAL_DECADES,
    CANONICAL_GAME_GENRE_PRIORITY,
    GAME_GENRE_CATALOG,
    RETRO_SYSTEM_CATALOG,
    ReferentialConsolidator,
    build_game_fingerprint,
    is_disallowed_non_fighting_title,
    is_unofficial_game_release,
    localize_label,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.persistence.server_config_store import (
    sanitize_twitch_credential,
    server_config_store,
)
from app.infrastructure.scrapers.games.arcade_aliases import (
    expand_retro_query_titles,
    resolve_arcade_query_roms,
)
from app.infrastructure.scrapers.games.arcadeitalia_referential import (
    ArcadeItaliaReferentialStrategy,
)
from app.infrastructure.scrapers.games.screenscraper_tokens import (
    SYSTEM_SPECIFIC_STEMS,
)
from app.infrastructure.scrapers.http_utils import (
    clean_synopsis_text,
    create_async_http_client,
    fetch_wikipedia_localized_synopsis,
    format_exception_detail,
)
from app.infrastructure.scrapers.translation_service import ensure_candidate_localized

logger = logging.getLogger("dmd_showcase.scrapers.games.igdb")

_SYS_RNG = random.SystemRandom()

_IGDB_TOKEN_CACHE: Dict[str, Any] = {
    "client_id": "",
    "client_secret": "",
    "access_token": "",
    "expires_at": 0.0,
}

# Strict arcade sub-boards whose IGDB platform ID is only generic `52` (Arcade)
_GENERIC_ARCADE_SUBBOARDS = frozenset(
    {"cps1", "cps2", "cps3", "naomi", "atomiswave", "model2", "model3", "zn", "pgm"}
)
_GENERIC_ARCADE_CATCHALLS = ("arcade", "mame", "fbneo")

# Build reverse lookup: IGDB platform ID -> canonical system keys (generic `52` maps strictly to `"arcade"`)
_IGDB_ID_TO_SYSTEMS: Dict[int, List[str]] = {52: ["arcade"]}
for _sys_k, _smeta in RETRO_SYSTEM_CATALOG.items():
    if _sys_k in _GENERIC_ARCADE_SUBBOARDS or _sys_k in _GENERIC_ARCADE_CATCHALLS:
        continue
    _ids = list(_smeta.get("igdb_ids") or ([] if not _smeta.get("igdb_id") else [_smeta["igdb_id"]]))
    for _pid in _ids:
        if int(_pid) == 52:
            continue
        _bucket = _IGDB_ID_TO_SYSTEMS.setdefault(int(_pid), [])
        if _sys_k not in _bucket:
            _bucket.append(_sys_k)

_IGDB_GENRE_ID_TO_KEYS: Dict[int, List[str]] = {}
for _g_key in CANONICAL_GAME_GENRE_PRIORITY:
    _gmeta = GAME_GENRE_CATALOG.get(_g_key) or {}
    for _gid in _gmeta.get("igdb_genre_ids") or []:
        _g_bucket = _IGDB_GENRE_ID_TO_KEYS.setdefault(int(_gid), [])
        if _g_key not in _g_bucket:
            _g_bucket.append(_g_key)

_APICALYPSE_FIELDS = (
    "fields name, summary, storyline, first_release_date, total_rating, rating, "
    "platforms, genres, cover.image_id, screenshots.image_id, artworks.image_id, "
    "videos.video_id, alternative_names.name;"
)


def _decade_epoch_bounds(decades: List[str]) -> Optional[Tuple[int, int]]:
    min_yr: Optional[int] = None
    max_yr: Optional[int] = None
    for raw in decades or []:
        d = (raw or "").strip().lower()
        if not d or d == "all":
            continue
        if d in CANONICAL_DECADES:
            lo, hi = CANONICAL_DECADES[d]["bounds"]
        elif len(d) >= 4 and d[:4].isdigit():
            lo = int(d[:4])
            hi = lo + 9
        else:
            continue
        min_yr = lo if min_yr is None else min(min_yr, lo)
        max_yr = hi if max_yr is None else max(max_yr, hi)
    if min_yr is None or max_yr is None:
        return None
    start_ts = int(dt.datetime(min_yr, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    end_ts = int(dt.datetime(max_yr, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp())
    return start_ts, end_ts


class IgdbGamesAdapter(IScraperSource):
    """
    Primary `IScraperSource` for `games` querying IGDB API v4 (`api.igdb.com/v4/games`).
    """

    source_id: str = "igdb"
    display_name: str = "IGDB (Twitch / Amazon)"
    domain: DomainType = "games"
    role: str = "primary"

    _COOLDOWN_SECONDS: float = 60.0
    _cooldown_until_monotonic: float = 0.0

    @classmethod
    def reset_circuit_breaker(cls) -> None:
        cls._cooldown_until_monotonic = 0.0
        _IGDB_TOKEN_CACHE["access_token"] = ""
        _IGDB_TOKEN_CACHE["expires_at"] = 0.0

    @classmethod
    def _record_cooldown(cls, reason: str) -> None:
        cls._cooldown_until_monotonic = time.monotonic() + cls._COOLDOWN_SECONDS
        logger.warning(
            "[IGDB] Entering %.0fs cooldown (%s); failing over to secondary game scrapers.",
            cls._COOLDOWN_SECONDS,
            reason,
        )

    def is_in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until_monotonic

    def _credentials(self) -> Tuple[str, str]:
        cfg = server_config_store.get_all()
        cid = sanitize_twitch_credential(cfg.get("igdb_client_id"))
        csecret = sanitize_twitch_credential(cfg.get("igdb_client_secret"))
        return cid, csecret

    def is_configured(self) -> bool:
        cid, csecret = self._credentials()
        return bool(cid and csecret)

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        ref = ReferentialConsolidator.build_domain_referential("games", lang=lang)
        return {
            "systems": [{"id": s["id"], "label": s["label"]} for s in ref["systems"]],
            "genres": [{"id": g["id"], "label": g["label"]} for g in ref["genres"]],
            "decades": [{"id": d["id"], "label": d["label"]} for d in ref["decades"]],
        }

    async def _get_access_token(self, client: Any) -> Optional[str]:
        cid, csecret = self._credentials()
        if not cid or not csecret:
            return None
        now = time.monotonic()
        if (
            _IGDB_TOKEN_CACHE["access_token"]
            and _IGDB_TOKEN_CACHE["client_id"] == cid
            and _IGDB_TOKEN_CACHE["client_secret"] == csecret
            and now < float(_IGDB_TOKEN_CACHE["expires_at"])
        ):
            return str(_IGDB_TOKEN_CACHE["access_token"])

        try:
            resp = await client.post(
                "https://id.twitch.tv/oauth2/token",
                params={"client_id": cid, "client_secret": csecret, "grant_type": "client_credentials"},
                timeout=5.0,
            )
            if resp.status_code != 200:
                self._record_cooldown(f"Twitch OAuth2 HTTP {resp.status_code}")
                return None
            payload = resp.json() if resp.content else {}
            token = str(payload.get("access_token") or "").strip()
            expires_in = int(payload.get("expires_in") or 3600)
            if not token:
                self._record_cooldown("Twitch OAuth2 missing access_token")
                return None
            _IGDB_TOKEN_CACHE.update(
                {
                    "client_id": cid,
                    "client_secret": csecret,
                    "access_token": token,
                    "expires_at": now + max(60.0, float(expires_in) - 60.0),
                }
            )
            return token
        except Exception as exc:
            self._record_cooldown(f"Twitch OAuth2 error: {type(exc).__name__}")
            return None

    async def _query_igdb_games(self, client: Any, apicalypse_body: str) -> List[Dict[str, Any]]:
        cid, _ = self._credentials()
        token = await self._get_access_token(client)
        if not cid or not token:
            return []
        headers = {
            "Client-ID": cid,
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
            "Accept": "application/json",
        }
        try:
            resp = await client.post(
                "https://api.igdb.com/v4/games",
                content=apicalypse_body.encode("utf-8"),
                headers=headers,
                timeout=6.0,
            )
            if resp.status_code == 401:
                _IGDB_TOKEN_CACHE["access_token"] = ""
                _IGDB_TOKEN_CACHE["expires_at"] = 0.0
                self._record_cooldown("IGDB HTTP 401 unauthorized")
                return []
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                self._record_cooldown(f"IGDB HTTP {resp.status_code}")
                return []
            if resp.status_code != 200:
                logger.warning("[IGDB] Unexpected HTTP %d from /v4/games", resp.status_code)
                return []
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            self._record_cooldown(f"IGDB request failed: {type(exc).__name__}")
            return []

    @staticmethod
    def _build_where_clauses(
        req_systems: List[str],
        req_genres: List[str],
        req_decades: List[str],
        require_cover: bool = False,
        for_discovery: bool = False,
    ) -> List[str]:
        clauses: List[str] = []
        if require_cover:
            clauses.append("cover != null")
        if for_discovery:
            clauses.extend(("first_release_date != null", "version_parent = null", "parent_game = null"))
        if req_systems:
            has_generic_arcade = any(s in _GENERIC_ARCADE_CATCHALLS for s in req_systems)
            has_dedicated_plat = any(
                s not in _GENERIC_ARCADE_SUBBOARDS and s not in _GENERIC_ARCADE_CATCHALLS
                for s in req_systems
            )
            plat_ids: List[int] = []
            for sys_k in req_systems:
                smeta = RETRO_SYSTEM_CATALOG.get(sys_k) or {}
                for pid in smeta.get("igdb_ids") or (
                    [smeta["igdb_id"]] if smeta.get("igdb_id") else []
                ):
                    pid_int = int(pid)
                    # During random offset discovery, do not pollute dedicated consoles/Neo-Geo with
                    # generic Arcade `52` unless generic Arcade was explicitly requested or no dedicated IDs exist.
                    if for_discovery and pid_int == 52 and not has_generic_arcade and has_dedicated_plat:
                        continue
                    if pid_int not in plat_ids:
                        plat_ids.append(pid_int)
            if plat_ids:
                joined = ",".join(str(p) for p in plat_ids)
                clauses.append(f"platforms = ({joined})")
        if req_genres:
            g_ids: List[int] = []
            for g_k in req_genres:
                gmeta = GAME_GENRE_CATALOG.get(g_k) or {}
                for gid in gmeta.get("igdb_genre_ids") or []:
                    if int(gid) not in g_ids:
                        g_ids.append(int(gid))
            if g_ids:
                joined_g = ",".join(str(g) for g in g_ids)
                clauses.append(f"genres = ({joined_g})")
        epoch_bounds = _decade_epoch_bounds(req_decades)
        if epoch_bounds is not None:
            lo_ts, hi_ts = epoch_bounds
            clauses.append(f"first_release_date >= {lo_ts} & first_release_date <= {hi_ts}")
        return clauses

    @staticmethod
    def _extract_rom_candidates(item: Dict[str, Any], raw_query: str = "") -> List[str]:
        title = str(item.get("name") or "").strip()
        rom_cands = resolve_arcade_query_roms(raw_query) or resolve_arcade_query_roms(title)
        for alt in item.get("alternative_names") or []:
            if isinstance(alt, dict) and alt.get("name") and not rom_cands:
                rom_cands = resolve_arcade_query_roms(str(alt["name"]))
        return rom_cands

    @classmethod
    def _resolve_system_for_item(
        cls,
        item: Dict[str, Any],
        req_systems: List[str],
        rom_cands: Optional[List[str]] = None,
        allow_empty_platforms: bool = False,
    ) -> str:
        item_plats = [int(p) for p in (item.get("platforms") or []) if str(p).isdigit()]
        plats_set = set(item_plats)
        roms = rom_cands if rom_cands is not None else cls._extract_rom_candidates(item)

        if req_systems:
            if not item_plats:
                if allow_empty_platforms:
                    first_sys = req_systems[0]
                    if first_sys not in _GENERIC_ARCADE_SUBBOARDS:
                        return first_sys
                    if any(
                        ArcadeItaliaReferentialStrategy.is_rom_authoritative_for_system(rc, first_sys)
                        for rc in roms
                    ):
                        return first_sys
                return ""

            # Pass 1: Specific dedicated IGDB platform IDs (`pid != 52`) take strict precedence
            # so multi-platform console+PlayChoice-10 games (e.g. Kid Icarus `[18, 52]`) resolve to `nes`.
            for sys_k in req_systems:
                if sys_k in _GENERIC_ARCADE_SUBBOARDS or sys_k in _GENERIC_ARCADE_CATCHALLS:
                    continue
                smeta = RETRO_SYSTEM_CATALOG.get(sys_k) or {}
                raw_pids = smeta.get("igdb_ids") or ([smeta["igdb_id"]] if smeta.get("igdb_id") else [])
                specific_pids = {int(p) for p in raw_pids if int(p) != 52}
                if specific_pids & plats_set:
                    return sys_k

            # Pass 2: Strict arcade sub-boards (`cps1`, `cps2`, `cps3`, `naomi`, ...) require board ROM verification
            if 52 in plats_set and roms:
                for sys_k in req_systems:
                    if sys_k in _GENERIC_ARCADE_SUBBOARDS and any(
                        ArcadeItaliaReferentialStrategy.is_rom_authoritative_for_system(rc, sys_k) for rc in roms
                    ):
                        return sys_k

            # Pass 3: Generic catch-all Arcade (`arcade`, `mame`, `fbneo`) if explicitly requested
            if 52 in plats_set:
                for sys_k in req_systems:
                    if sys_k in _GENERIC_ARCADE_CATCHALLS:
                        return sys_k
            return ""

        # Unfiltered (`req_systems == []`): upgrade `52` to authoritative arcade board if known, else dedicated ID
        if 52 in plats_set and roms:
            for rc in roms:
                board_sys = ArcadeItaliaReferentialStrategy.resolve_authoritative_system_for_rom(rc)
                if board_sys:
                    return board_sys
        for pid in item_plats:
            if pid == 52:
                continue
            mapped = _IGDB_ID_TO_SYSTEMS.get(pid)
            if mapped:
                return mapped[0]
        if 52 in plats_set or allow_empty_platforms:
            return "arcade"
        return ""

    @staticmethod
    def _resolve_genre_for_item(
        item: Dict[str, Any],
        req_genres: List[str],
        title: str = "",
    ) -> str:
        raw_Keys: List[str] = []
        for gid in item.get("genres") or []:
            if str(gid).isdigit():
                for gk in _IGDB_GENRE_ID_TO_KEYS.get(int(gid), []):
                    if gk not in raw_Keys:
                        raw_Keys.append(gk)
        priority_rank = {gk: idx for idx, gk in enumerate(CANONICAL_GAME_GENRE_PRIORITY)}
        mapped_keys = sorted(raw_Keys, key=lambda gk: priority_rank.get(gk, 999))

        non_fighting = is_disallowed_non_fighting_title(title) if title else False
        if non_fighting and "fighting" in mapped_keys and len(mapped_keys) > 1:
            mapped_keys = [gk for gk in mapped_keys if gk != "fighting"]

        if req_genres:
            req_set = set(req_genres)
            if mapped_keys:
                for gk in mapped_keys:
                    if gk == "fighting" and non_fighting:
                        continue
                    if gk in req_set:
                        return gk
                return ""
            if req_genres == ["fighting"] and non_fighting:
                return ""
            return req_genres[0]

        if mapped_keys:
            return mapped_keys[0]
        return "action"

    async def _build_candidate(
        self,
        client: Any,
        item: Dict[str, Any],
        req_systems: List[str],
        req_genres: List[str],
        lang: str,
        raw_query: str = "",
        fetch_wiki: bool = True,
    ) -> Optional[MediaCandidate]:
        igdb_gid = item.get("id")
        title = str(item.get("name") or "").strip()
        if not igdb_gid or not title:
            return None

        igdb_summary = clean_synopsis_text(
            str(item.get("summary") or item.get("storyline") or ""), max_chars=320
        )
        if is_unofficial_game_release(title, synopsis=igdb_summary):
            return None

        rom_cands = self._extract_rom_candidates(item, raw_query=raw_query)
        sys_key = self._resolve_system_for_item(
            item,
            req_systems,
            rom_cands=rom_cands,
            allow_empty_platforms=bool(raw_query),
        )
        if not sys_key:
            return None
        smeta = RETRO_SYSTEM_CATALOG.get(sys_key) or RETRO_SYSTEM_CATALOG["arcade"]
        is_arcade = bool(smeta.get("is_arcade"))

        # Validate hardware board ROM compatibility when strict arcade sub-board is resolved
        matched_rom = ""
        if is_arcade:
            for rc in rom_cands:
                if ArcadeItaliaReferentialStrategy.is_rom_allowed_for_system(rc, sys_key):
                    matched_rom = rc
                    break
            if (
                req_systems
                and sys_key in ("cps1", "cps2", "cps3", "neogeo")
                and rom_cands
                and not matched_rom
            ):
                return None

        genre_key = self._resolve_genre_for_item(item, req_genres, title=title)
        if not genre_key:
            return None

        cover_obj = item.get("cover") if isinstance(item.get("cover"), dict) else {}
        cover_img_id = str(cover_obj.get("image_id") or "").strip()
        screenshots = [
            str(s.get("image_id") or "").strip()
            for s in (item.get("screenshots") or [])
            if isinstance(s, dict) and s.get("image_id")
        ]
        artworks = [
            str(a.get("image_id") or "").strip()
            for a in (item.get("artworks") or [])
            if isinstance(a, dict) and a.get("image_id")
        ]

        poster_url = (
            f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover_img_id}.jpg"
            if cover_img_id
            else (
                f"https://images.igdb.com/igdb/image/upload/t_screenshot_med/{screenshots[0]}.jpg"
                if screenshots
                else None
            )
        )
        if not poster_url:
            return None

        backdrop_img = artworks[0] if artworks else (screenshots[0] if screenshots else "")
        backdrop_url = (
            f"https://images.igdb.com/igdb/image/upload/t_720p/{backdrop_img}.jpg"
            if backdrop_img
            else None
        )

        youtube_url: Optional[str] = None
        for vid in item.get("videos") or []:
            if isinstance(vid, dict) and vid.get("video_id"):
                youtube_url = f"https://www.youtube.com/watch?v={vid['video_id']}"
                break

        ts = item.get("first_release_date")
        if isinstance(ts, (int, float)) and ts > 0:
            year_str = str(dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).year)
        else:
            default_dec = (smeta.get("decades") or ["1990s"])[0]
            year_str = str(default_dec)[:4] if str(default_dec)[:4].isdigit() else "1994"

        raw_rating = item.get("total_rating") or item.get("rating")
        rating_str = (
            f"{max(1.0, min(9.9, float(raw_rating) / 10.0)):.1f}"
            if isinstance(raw_rating, (int, float)) and raw_rating > 0
            else "8.8"
        )

        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        gmeta = GAME_GENRE_CATALOG.get(genre_key) or GAME_GENRE_CATALOG["action"]
        genre_label = localize_label(gmeta["labels"], short_lang, "Arcade")
        sys_label = localize_label(smeta["labels"], short_lang, smeta["badge"])

        synopsis = igdb_summary
        synopsis_localized = "1" if (short_lang == "en" and bool(igdb_summary)) else "0"

        if short_lang != "en" and fetch_wiki and client is not None:
            try:
                wiki_syn, _ = await asyncio.wait_for(
                    fetch_wikipedia_localized_synopsis(
                        client,
                        title,
                        lang=short_lang,
                        domain_hint="video game",
                    ),
                    timeout=4.5,
                )
                if wiki_syn:
                    synopsis = wiki_syn
                    synopsis_localized = "1"
            except Exception as exc:
                logger.warning(
                    "[IGDB] Wikipedia synopsis lookup timed out/failed for '%s': %s",
                    title,
                    format_exception_detail(exc),
                )

        if not synopsis:
            prep = {"fr": "sur", "es": "en", "de": "auf", "it": "su", "pt": "no"}.get(short_lang, "on")
            synopsis = f"{title} ({year_str}) — {genre_label} {prep} {sys_label}."

        extra_meta: Dict[str, Any] = {
            "system": sys_key,
            "genre": genre_key,
            "igdb_id": str(igdb_gid),
            "synopsis_localized": synopsis_localized,
        }
        if matched_rom:
            extra_meta["mame_rom"] = matched_rom

        cand = MediaCandidate(
            id=f"games_igdb_{sys_key}_{igdb_gid}",
            domain="games",
            title=title,
            year=year_str,
            badge=smeta["badge"],
            badge_color=smeta["color"],
            subtitle=f"{genre_label} • {smeta['badge']}"[:28],
            rating=rating_str,
            synopsis=synopsis,
            lang=short_lang,
            poster_url=poster_url,
            backdrop_url=backdrop_url,
            jacket_url=poster_url,
            marquee_url=None,
            video_url=None,
            youtube_url=youtube_url,
            source_id=self.source_id,
            theme_palette=tuple(smeta["palette"]),
            extra_meta=extra_meta,
        )
        for shot_id in screenshots[:4]:
            cand.add_unique_snapshot(
                f"https://images.igdb.com/igdb/image/upload/t_screenshot_med/{shot_id}.jpg"
            )
        if short_lang != "en" and synopsis_localized == "0" and igdb_summary:
            await ensure_candidate_localized(cand, short_lang, client, raw_source_lang="en")
        return cand

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        if not self.is_configured() or self.is_in_cooldown():
            return []

        req_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.strip().lower() != "all" and resolve_canonical_system(s) in RETRO_SYSTEM_CATALOG
        ]
        req_genres = [
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.strip().lower() != "all" and resolve_canonical_genre("games", g) in GAME_GENRE_CATALOG
        ]
        req_decades = [
            d.strip().lower() for d in (criteria.decades or []) if d and d.strip().lower() != "all"
        ]

        only_generic_subboards = bool(req_systems) and all(
            s in _GENERIC_ARCADE_SUBBOARDS for s in req_systems
        )
        if only_generic_subboards:
            stem_pool = [st for s_k in req_systems for st in (SYSTEM_SPECIFIC_STEMS.get(s_k) or [])]
            if stem_pool:
                searched = await self.search_candidates(
                    query=_SYS_RNG.choice(stem_pool), criteria=criteria, limit=max(4, count * 3)
                )
                out: List[MediaCandidate] = []
                for cand in searched:
                    fp = build_game_fingerprint(str((cand.extra_meta or {}).get("system") or ""), cand.title)
                    if cand.id in seen_ids or (fp and fp in seen_ids):
                        continue
                    seen_ids.update({cand.id} | ({fp} if fp else set()))
                    out.append(cand)
                    if len(out) >= count:
                        return out
                if out:
                    return out

        clauses = self._build_where_clauses(
            req_systems, req_genres, req_decades, require_cover=True, for_discovery=True
        )
        where_stmt = f"where {' & '.join(clauses)};" if clauses else "where cover != null;"
        max_offset = 80 if (req_systems or req_genres or req_decades) else 350
        rand_offset = _SYS_RNG.randint(0, max_offset)
        batch_limit = max(15, count * 6)

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=7.0) as client:
            items = await self._query_igdb_games(
                client,
                f"{_APICALYPSE_FIELDS} {where_stmt} limit {batch_limit}; offset {rand_offset};",
            )
            if not items and rand_offset > 0 and not self.is_in_cooldown():
                items = await self._query_igdb_games(
                    client,
                    f"{_APICALYPSE_FIELDS} {where_stmt} limit {batch_limit}; offset 0;",
                )
            if not items:
                return []

            _SYS_RNG.shuffle(items)
            if criteria.include_video:
                items.sort(key=lambda it: 0 if it.get("videos") else 1)
            for item in items:
                if len(results) >= count:
                    break
                igdb_gid = item.get("id")
                sys_probe = self._resolve_system_for_item(item, req_systems)
                if not sys_probe:
                    continue
                cand_id_probe = f"games_igdb_{sys_probe}_{igdb_gid}"
                title_probe = str(item.get("name") or "").strip()
                fp_probe = build_game_fingerprint(sys_probe, title_probe)
                if cand_id_probe in seen_ids or (fp_probe and fp_probe in seen_ids):
                    continue
                cand = await self._build_candidate(
                    client,
                    item=item,
                    req_systems=req_systems,
                    req_genres=req_genres,
                    lang=criteria.lang,
                    fetch_wiki=True,
                )
                if not cand or cand.id in seen_ids:
                    continue
                cand_sys = str((cand.extra_meta or {}).get("system") or sys_probe)
                fp = build_game_fingerprint(cand_sys, cand.title)
                if fp and fp in seen_ids:
                    seen_ids.add(cand.id)
                    continue
                seen_ids.add(cand.id)
                if fp:
                    seen_ids.add(fp)
                results.append(cand)

        return results[:count]

    async def search_candidates(
        self,
        query: str,
        criteria: FilterCriteria,
        limit: int = 8,
    ) -> List[MediaCandidate]:
        q_clean = (query or "").strip()
        if not self.is_configured() or self.is_in_cooldown() or not q_clean or limit <= 0:
            return []

        target_limit = max(1, int(limit))
        req_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.strip().lower() != "all" and resolve_canonical_system(s) in RETRO_SYSTEM_CATALOG
        ]
        req_genres = [
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.strip().lower() != "all" and resolve_canonical_genre("games", g) in GAME_GENRE_CATALOG
        ]
        req_decades = [
            d.strip().lower() for d in (criteria.decades or []) if d and d.strip().lower() != "all"
        ]

        clauses = self._build_where_clauses(req_systems, req_genres, req_decades, require_cover=False)
        where_part = f" where {' & '.join(clauses)};" if clauses else ""
        fetch_lim = max(8, target_limit * 2)

        def _esc(raw: str) -> str:
            return re.sub(r'["\\]', " ", raw).strip()

        expanded_titles = expand_retro_query_titles(q_clean)
        # If query is a compact shorthand alias like `sf2x` or `3s`, prioritize canonical title search first,
        # otherwise try raw query first then expanded titles, then alternative_names wildcard.
        query_plan: List[str] = []
        if expanded_titles and len(q_clean) <= 6 and " " not in q_clean:
            query_plan.extend(expanded_titles[:2])
            query_plan.append(q_clean)
        else:
            query_plan.append(q_clean)
            for ext in expanded_titles[:2]:
                if ext.lower() != q_clean.lower():
                    query_plan.append(ext)

        results: List[MediaCandidate] = []
        seen_keys: Set[str] = set()

        async with create_async_http_client(timeout=7.0) as client:
            raw_items: List[Dict[str, Any]] = []
            for q_term in query_plan:
                if self.is_in_cooldown():
                    break
                esc_q = _esc(q_term)
                if not esc_q:
                    continue
                body = f'{_APICALYPSE_FIELDS} search "{esc_q}";{where_part} limit {fetch_lim};'
                hits = await self._query_igdb_games(client, body)
                if hits:
                    raw_items.extend(hits)
                    break

            if not raw_items and not self.is_in_cooldown():
                esc_raw = _esc(q_clean)
                if esc_raw:
                    alt_clauses = [f'alternative_names.name ~ *"{esc_raw}"*'] + clauses
                    alt_body = (
                        f"{_APICALYPSE_FIELDS} where {' & '.join(alt_clauses)}; limit {fetch_lim};"
                    )
                    raw_items.extend(await self._query_igdb_games(client, alt_body))

            for idx, item in enumerate(raw_items):
                if len(results) >= target_limit:
                    break
                cand = await self._build_candidate(
                    client,
                    item=item,
                    req_systems=req_systems,
                    req_genres=req_genres,
                    lang=criteria.lang,
                    raw_query=q_clean,
                    fetch_wiki=(idx < min(target_limit, 3)),
                )
                if not cand:
                    continue
                cand_sys = str((cand.extra_meta or {}).get("system") or "arcade")
                fp = build_game_fingerprint(cand_sys, cand.title)
                if cand.id in seen_keys or (fp and fp in seen_keys):
                    continue
                seen_keys.add(cand.id)
                if fp:
                    seen_keys.add(fp)
                results.append(cand)

        return results[:target_limit]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        criteria = FilterCriteria(domain="games", systems=[], genres=[], decades=[], lang=lang)
        cands = await self.search_candidates(query=query, criteria=criteria, limit=1)
        return cands[0] if cands else None
