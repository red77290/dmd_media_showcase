"""
Primary Games Scraper Adapter: ScreenScraper API v2 (`api.screenscraper.fr`).
Implements `IScraperSource` with uniform random discovery across all retro systems,
delegating XML/JSON parsing to `screenscraper_xml_parser` and token sampling to `screenscraper_tokens`.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import (
    build_game_fingerprint,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.persistence.server_config_store import server_config_store
from app.infrastructure.scrapers.catalog_metadata import GAME_GENRES, SYSTEM_METADATA
from app.infrastructure.scrapers.games.arcade_aliases import expand_retro_query_titles
from app.infrastructure.scrapers.games.screenscraper_tokens import (
    STRICT_HARDWARE_SS_IDS,
    pick_discovery_token,
)
from app.infrastructure.scrapers.games.screenscraper_xml_parser import (
    detect_screenscraper_api_error,
    extract_jeux_from_response,
    extract_node_text,
    is_screenscraper_in_cooldown,
    parse_jeu_infos_xml,
    parse_screenscraper_game_node,
    record_screenscraper_cooldown,
    reset_screenscraper_cooldown,
)
from app.infrastructure.scrapers.http_utils import (
    create_async_http_client,
    format_exception_detail,
)

logger = logging.getLogger("dmd_showcase.scrapers.games.screenscraper")

_SYS_RNG = random.SystemRandom()
_WARNED_MISSING_DEVID = False


class ScreenScraperAdapter(IScraperSource):
    """
    `IScraperSource` for `games` using ScreenScraper API v2 (`jeuRecherche.php` & `jeuInfos.php`).
    """

    source_id: str = "screenscraper"
    display_name: str = "ScreenScraper"
    domain: DomainType = "games"
    role: str = "primary"

    parse_jeu_infos_xml = staticmethod(parse_jeu_infos_xml)
    reset_circuit_breaker = staticmethod(reset_screenscraper_cooldown)

    def __init__(self) -> None:
        self._recent_tokens: Dict[Tuple[str, str], Deque[str]] = {}

    def is_in_cooldown(self) -> bool:
        return is_screenscraper_in_cooldown()

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
                {"id": "2010s", "label": "2010s"},
            ],
        }

    def _parse_game_node(
        self, jeu: Dict[str, Any], sys_key: str, lang: str, criteria: Optional[FilterCriteria] = None
    ) -> Optional[MediaCandidate]:
        return parse_screenscraper_game_node(
            jeu=jeu, sys_key=sys_key, lang=lang, criteria=criteria, source_id=self.source_id
        )

    def _pick_discovery_token(
        self, sys_key: str, req_genres: List[str], used_tokens: Set[Tuple[str, str]]
    ) -> str:
        return pick_discovery_token(
            sys_key=sys_key, req_genres=req_genres, used_tokens=used_tokens, recent_tokens=self._recent_tokens
        )

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        if not self.is_configured() or self.is_in_cooldown():
            return []

        creds = self._credentials()
        valid_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.strip().lower() != "all" and resolve_canonical_system(s) in SYSTEM_METADATA
        ] or list(SYSTEM_METADATA.keys())
        req_genres = [
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.strip().lower() != "all"
        ]

        results: List[MediaCandidate] = []
        overflow_candidates: List[Tuple[MediaCandidate, Set[str]]] = []
        staged_keys: Set[str] = set()
        used_tokens: Set[Tuple[str, str]] = set()
        max_attempts = max(10 if req_genres else 6, count * 5)

        async with create_async_http_client(timeout=6.0) as client:
            for _ in range(max_attempts):
                if len(results) >= count or self.is_in_cooldown():
                    break
                sys_key = _SYS_RNG.choice(valid_systems)
                sys_meta = SYSTEM_METADATA[sys_key]
                token = self._pick_discovery_token(sys_key, req_genres, used_tokens)

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
                    api_err = detect_screenscraper_api_error(resp)
                    if api_err:
                        record_screenscraper_cooldown(api_err)
                        break
                    if resp.status_code != 200:
                        continue
                    jeux = extract_jeux_from_response(resp)
                    _SYS_RNG.shuffle(jeux)
                    jeux.sort(
                        key=lambda j: 0
                        if extract_node_text(j.get("cloneof")) in ("", "0")
                        else 1
                    )
                    token_primary_added = False
                    for jeu in jeux:
                        raw_gid = str(jeu.get("id") or "").strip()
                        if raw_gid and (
                            f"games_ss_{raw_gid}" in seen_ids
                            or f"games_ss_{raw_gid}" in staged_keys
                        ):
                            continue
                        clone_parent = extract_node_text(jeu.get("cloneof"))
                        if clone_parent not in ("", "0") and (
                            f"games_ss_{clone_parent}" in seen_ids
                            or f"games_ss_{clone_parent}" in staged_keys
                        ):
                            continue

                        cand = self._parse_game_node(
                            jeu, sys_key, criteria.lang, criteria=criteria
                        )
                        if not cand:
                            if raw_gid:
                                seen_ids.add(f"games_ss_{raw_gid}")
                            continue

                        cand_sys = str((cand.extra_meta or {}).get("system") or sys_key)
                        title_slug = re.sub(r"[^a-z0-9]+", "_", cand.title.lower()).strip("_")
                        title_key = f"games_ss_title_{title_slug}"
                        fp_key = build_game_fingerprint(cand_sys, cand.title)

                        dedup_keys = {cand.id, title_key}
                        if fp_key:
                            dedup_keys.add(fp_key)
                        if clone_parent not in ("", "0"):
                            dedup_keys.add(f"games_ss_{clone_parent}")

                        if dedup_keys & seen_ids or dedup_keys & staged_keys:
                            seen_ids.add(cand.id)
                            continue

                        if not token_primary_added:
                            seen_ids.update(dedup_keys)
                            results.append(cand)
                            token_primary_added = True
                            if len(results) >= count:
                                break
                        else:
                            staged_keys.update(dedup_keys)
                            overflow_candidates.append((cand, dedup_keys))
                except Exception as exc:
                    logger.warning(
                        "[ScreenScraper] Discovery request failed: %s",
                        format_exception_detail(exc),
                    )
                    break

        if len(results) < count and overflow_candidates:
            _SYS_RNG.shuffle(overflow_candidates)
            for cand, dedup_keys in overflow_candidates:
                if len(results) >= count:
                    break
                if dedup_keys & seen_ids:
                    continue
                seen_ids.update(dedup_keys)
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
        creds = self._credentials()
        valid_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.strip().lower() != "all" and resolve_canonical_system(s) in SYSTEM_METADATA
        ]

        results: List[MediaCandidate] = []
        seen_keys: Set[str] = set()

        def _base_params(q_text: str, system_id: Optional[str] = None) -> Dict[str, str]:
            p: Dict[str, str] = {
                "softname": "dmd-media-showcase",
                "output": "json",
                "recherche": q_text,
            }
            if system_id:
                p["systemeid"] = system_id
            if creds["devid"]:
                p["devid"] = creds["devid"]
                p["devpassword"] = creds["devpass"]
            if creds["user"]:
                p["ssid"] = creds["user"]
                p["sspassword"] = creds["pass"]
            return p

        def _resolve_hint_sys(jeu: Dict[str, Any], default_sys: str) -> str:
            if default_sys:
                return default_sys
            if not valid_systems:
                return ""
            sys_node = jeu.get("systeme")
            if isinstance(sys_node, dict):
                sid = str(sys_node.get("id") or "").strip()
                stxt = str(sys_node.get("text") or sys_node.get("nom") or "").strip().lower()
            else:
                sid = ""
                stxt = str(sys_node or "").strip().lower()
            for strict_sys in ("cps1", "cps2", "cps3", "neogeo"):
                if strict_sys in valid_systems:
                    tokens = STRICT_HARDWARE_SS_IDS[strict_sys]
                    if sid == tokens[0] or (
                        sid in ("", "75") and any(t in stxt for t in tokens[1:])
                    ):
                        return strict_sys
            return ""

        def _ingest_jeux(jeux: List[Dict[str, Any]], default_sys: str) -> None:
            ordered = sorted(
                jeux,
                key=lambda j: 0 if extract_node_text(j.get("cloneof")) in ("", "0") else 1,
            )
            for jeu in ordered:
                if len(results) >= target_limit:
                    break
                raw_gid = str(jeu.get("id") or "").strip()
                if raw_gid and f"games_ss_{raw_gid}" in seen_keys:
                    continue
                clone_parent = extract_node_text(jeu.get("cloneof"))
                if clone_parent not in ("", "0") and f"games_ss_{clone_parent}" in seen_keys:
                    continue

                hint_sys = _resolve_hint_sys(jeu, default_sys)
                cand = self._parse_game_node(jeu, hint_sys, criteria.lang, criteria=criteria)
                if not cand:
                    continue

                cand_sys = str((cand.extra_meta or {}).get("system") or hint_sys or "arcade")
                fp_key = build_game_fingerprint(cand_sys, cand.title)
                dedup_keys = {cand.id}
                if fp_key:
                    dedup_keys.add(fp_key)
                if clone_parent not in ("", "0"):
                    dedup_keys.add(f"games_ss_{clone_parent}")

                if dedup_keys & seen_keys:
                    continue
                seen_keys.update(dedup_keys)
                results.append(cand)

        async def _run_query_pass(client: Any, q_text: str) -> bool:
            if len(q_text.strip()) < 3:
                return True
            if len(valid_systems) == 1:
                only_sys = valid_systems[0]
                ss_id = str(SYSTEM_METADATA[only_sys]["ss_id"])
                resp = await client.get(
                    "https://api.screenscraper.fr/api2/jeuRecherche.php",
                    params=_base_params(q_text, system_id=ss_id),
                )
                err = detect_screenscraper_api_error(resp)
                if err:
                    record_screenscraper_cooldown(err)
                    return False
                if resp.status_code == 200:
                    _ingest_jeux(extract_jeux_from_response(resp), only_sys)
                return True

            resp = await client.get(
                "https://api.screenscraper.fr/api2/jeuRecherche.php",
                params=_base_params(q_text),
            )
            err = detect_screenscraper_api_error(resp)
            if err:
                record_screenscraper_cooldown(err)
                return False
            if resp.status_code == 200:
                _ingest_jeux(extract_jeux_from_response(resp), "")

            if valid_systems and len(results) < target_limit:
                queried_ss_ids: Set[str] = set()
                for sys_key in valid_systems:
                    if len(results) >= target_limit or len(queried_ss_ids) >= 3:
                        break
                    ss_id = str(SYSTEM_METADATA[sys_key]["ss_id"])
                    if ss_id in queried_ss_ids:
                        continue
                    queried_ss_ids.add(ss_id)
                    sub_resp = await client.get(
                        "https://api.screenscraper.fr/api2/jeuRecherche.php",
                        params=_base_params(q_text, system_id=ss_id),
                    )
                    sub_err = detect_screenscraper_api_error(sub_resp)
                    if sub_err:
                        record_screenscraper_cooldown(sub_err)
                        return False
                    if sub_resp.status_code == 200:
                        _ingest_jeux(extract_jeux_from_response(sub_resp), sys_key)
            return True

        try:
            async with create_async_http_client(timeout=6.0) as client:
                ok = await _run_query_pass(client, q_clean)
                if ok and not results:
                    for expanded in expand_retro_query_titles(q_clean)[:2]:
                        if expanded.lower() != q_clean.lower():
                            if not await _run_query_pass(client, expanded) or results:
                                break
        except Exception as exc:
            logger.warning(
                "[ScreenScraper] Search error for '%s': %s",
                q_clean,
                format_exception_detail(exc),
            )

        return results[:target_limit]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        criteria = FilterCriteria(domain="games", systems=[], genres=[], decades=[], lang=lang)
        cands = await self.search_candidates(query=query, criteria=criteria, limit=1)
        return cands[0] if cands else None
