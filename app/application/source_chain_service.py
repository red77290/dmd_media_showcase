"""
Application Service: `DomainSourceChainService`.

Orchestrates the `[Primary, Fallback]` `IScraperSource` adapters for each domain (`games`,
`movies`, `series`, `anime`) while enforcing strict `ISeenHistoryRepository` exclusion so
no title is ever repeated.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Set

from app.domain.aggregates.artifact_cache import BoundedTtlCache
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports import (
    IScraperRegistryPort,
    IScraperSource,
    ISeenHistoryRepository,
    default_port_registry,
)
from app.domain.taxonomy import (
    ReferentialConsolidator,
    build_game_fingerprint,
    is_arcade_system,
    is_disallowed_non_fighting_title,
    resolve_canonical_genre,
    resolve_canonical_system,
)

logger = logging.getLogger("dmd_showcase.application.source_chain")


class DomainSourceChainService:
    """
    Executes the 2-source `[Primary, Fallback]` chain for a domain and filters out
    all previously consumed item IDs stored in `ISeenHistoryRepository`.
    """

    _STAGED_MAX_ENTRIES: int = 256
    _STAGED_TTL_S: float = 1800.0

    def __init__(
        self,
        registry: Optional[IScraperRegistryPort] = None,
        history_repo: Optional[ISeenHistoryRepository] = None,
    ) -> None:
        self.registry: IScraperRegistryPort = (
            registry if registry is not None else default_port_registry.resolve("scraper_registry")
        )
        self.history_repo: ISeenHistoryRepository = (
            history_repo
            if history_repo is not None
            else default_port_registry.resolve("seen_repository")
        )
        self._staged_cache: BoundedTtlCache[str, MediaCandidate] = BoundedTtlCache(
            ttl_seconds=self._STAGED_TTL_S,
            max_entries=self._STAGED_MAX_ENTRIES,
        )
        self._stage_lock = threading.RLock()

    def stage_candidate(self, cand: MediaCandidate) -> None:
        """Stage a discovered `MediaCandidate` in the bounded TTL cache for instant queue resolution."""
        if cand is None or not cand.id:
            return
        with self._stage_lock:
            self._staged_cache.put(cand.id, cand)
        self._stage_candidate_fp(cand.domain, cand)

    def get_staged_candidate(self, candidate_id: str) -> Optional[MediaCandidate]:
        """Retrieve a previously staged `MediaCandidate` by ID if still within its TTL."""
        cid = (candidate_id or "").strip()
        if not cid:
            return None
        with self._stage_lock:
            return self._staged_cache.get(cid)

    @staticmethod
    def _candidate_matches_criteria(cand: MediaCandidate, criteria: FilterCriteria) -> bool:
        """
        Defense-in-depth validation ensuring no adapter ever leaks a wrong-system game
        (e.g. Arcade/SNES/PS1 when `systems=['megadrive']`) or a Platformer / Beat 'em up
        when `genres=['fighting']` (`vs_fighting`) is requested.
        """
        if criteria.domain != "games":
            return True

        meta = cand.extra_meta or {}
        req_systems = {
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.strip().lower() != "all"
        }
        if req_systems:
            if cand.id.startswith("games_adb_") and not any(
                is_arcade_system(s) for s in req_systems
            ):
                return False
            raw_cand_sys = str(meta.get("system") or "").strip()
            if raw_cand_sys:
                cand_sys = resolve_canonical_system(raw_cand_sys)
                if cand_sys not in req_systems:
                    if not (
                        cand_sys in ("cps1", "cps2", "cps3", "neogeo")
                        and (req_systems & {"arcade", "mame", "fbneo"})
                    ):
                        return False

        req_genres = {
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.strip().lower() != "all"
        }
        if req_genres:
            cand_genres: Set[str] = set()
            raw_list = meta.get("genres")
            if isinstance(raw_list, list):
                for g_item in raw_list:
                    if g_item:
                        cand_genres.add(resolve_canonical_genre("games", str(g_item)))
            raw_single = str(meta.get("genre") or "").strip()
            if raw_single:
                cand_genres.add(resolve_canonical_genre("games", raw_single))

            if cand_genres:
                if not (cand_genres & req_genres):
                    return False
                if "fighting" in req_genres:
                    if "platform" not in req_genres and "platform" in cand_genres:
                        return False
                    if "beatemup" not in req_genres and "beatemup" in cand_genres:
                        return False

            if (
                "fighting" in req_genres
                and "platform" not in req_genres
                and "beatemup" not in req_genres
                and is_disallowed_non_fighting_title(cand.title)
            ):
                return False

        return True

    def _stage_candidate_fp(self, domain: DomainType, cand: MediaCandidate) -> Optional[str]:
        if domain != "games":
            return None
        cand_sys = str((cand.extra_meta or {}).get("system") or "")
        fp = build_game_fingerprint(cand_sys, cand.title)
        if fp and hasattr(self.history_repo, "stage_candidate_fingerprint"):
            self.history_repo.stage_candidate_fingerprint(domain, cand.id, {fp})
        return fp or None

    async def _collect_from_chain(
        self,
        chain: List[IScraperSource],
        criteria: FilterCriteria,
        working_seen: Set[str],
        count: int,
    ) -> List[MediaCandidate]:
        domain = criteria.domain
        collected: List[MediaCandidate] = []
        for source in chain:
            if not source.is_configured():
                continue
            if (
                not ReferentialConsolidator.source_supports_criteria(source.source_id, criteria)
                and source.source_id != "arcadeitalia"
            ):
                continue
            needed = count - len(collected)
            if needed <= 0:
                break
            try:
                batch = await source.fetch_random_unseen(
                    criteria=criteria,
                    seen_ids=set(working_seen),
                    count=needed,
                )
                for cand in batch:
                    if cand.id in working_seen:
                        continue
                    if not self._candidate_matches_criteria(cand, criteria):
                        working_seen.add(cand.id)
                        continue
                    fp = (
                        build_game_fingerprint(
                            str((cand.extra_meta or {}).get("system") or ""),
                            cand.title,
                        )
                        if domain == "games"
                        else ""
                    )
                    if fp and fp in working_seen:
                        working_seen.add(cand.id)
                        continue
                    self._stage_candidate_fp(domain, cand)
                    collected.append(cand)
                    working_seen.add(cand.id)
                    if fp:
                        working_seen.add(fp)
                    if len(collected) >= count:
                        break
            except Exception as exc:
                logger.warning(
                    "[SOURCE CHAIN] Source '%s' (%s) failed for domain '%s': %s",
                    source.source_id,
                    source.role,
                    domain,
                    exc,
                )
        return collected

    async def acquire_unseen_candidates(
        self,
        criteria: FilterCriteria,
        count: int = 1,
        extra_exclude_ids: Optional[Set[str]] = None,
    ) -> List[MediaCandidate]:
        """
        Acquire `count` unseen candidates for `criteria.domain` by querying the Primary
        `IScraperSource` first, and falling back to the Fallback `IScraperSource` if needed.
        """
        domain = criteria.domain
        chain = self.registry.get_chain(domain)
        persisted_seen = self.history_repo.get_seen_ids(domain)
        working_seen = set(persisted_seen)
        if extra_exclude_ids:
            working_seen.update(extra_exclude_ids)
        if hasattr(self.history_repo, "get_seen_fingerprints"):
            working_seen.update(
                self.history_repo.get_seen_fingerprints(
                    domain, include_item_ids=extra_exclude_ids
                )
            )

        results = await self._collect_from_chain(
            chain=chain,
            criteria=criteria,
            working_seen=working_seen,
            count=count,
        )

        # Guard against false catalog-exhaustion resets when stochastic single-page or
        # single-system sampling happens to land on already-seen items:
        # 1. Retry once with `persisted_seen` still enforced so random samplers get a second draw.
        # 2. If still empty, probe `max(count, 3)` items without `persisted_seen`:
        #    - If ANY returned candidate is genuinely unseen, return those unseen items WITHOUT
        #      resetting `seen_history`.
        #    - ONLY reset `seen_history` when the probe returns candidates and 100% of them
        #      are already present in `persisted_seen` / `persisted_seen_fps`.
        if not results and persisted_seen:
            results = await self._collect_from_chain(
                chain=chain,
                criteria=criteria,
                working_seen=set(working_seen),
                count=count,
            )

        if not results and persisted_seen:
            persisted_fps: Set[str] = (
                self.history_repo.get_seen_fingerprints(domain)
                if hasattr(self.history_repo, "get_seen_fingerprints")
                else set()
            )
            probe_seen: Set[str] = set(extra_exclude_ids or set())
            if extra_exclude_ids and hasattr(self.history_repo, "get_seen_fingerprints"):
                for ex_id in extra_exclude_ids:
                    probe_seen.update(
                        self.history_repo.get_seen_fingerprints(domain, include_item_ids={ex_id})
                        - persisted_fps
                    )
            probe_results = await self._collect_from_chain(
                chain=chain,
                criteria=criteria,
                working_seen=probe_seen,
                count=max(count, 3),
            )
            if probe_results:
                genuinely_unseen: List[MediaCandidate] = []
                for cand in probe_results:
                    cand_fp = (
                        build_game_fingerprint(
                            str((cand.extra_meta or {}).get("system") or ""),
                            cand.title,
                        )
                        if domain == "games"
                        else ""
                    )
                    if cand.id not in persisted_seen and not (cand_fp and cand_fp in persisted_fps):
                        genuinely_unseen.append(cand)

                if genuinely_unseen:
                    for cand in genuinely_unseen[:count]:
                        self._stage_candidate_fp(domain, cand)
                    results = genuinely_unseen[:count]
                else:
                    logger.info(
                        "[SOURCE CHAIN] Catalog exhausted for domain '%s' (%d seen), resetting seen_history",
                        domain,
                        len(persisted_seen),
                    )
                    self.history_repo.reset(domain)
                    for cand in probe_results[:count]:
                        self._stage_candidate_fp(domain, cand)
                    results = probe_results[:count]

        final_results = results[:count]
        for cand in final_results:
            self.stage_candidate(cand)
        return final_results

    async def search_candidates(
        self,
        criteria: FilterCriteria,
        query: str = "",
        limit: int = 8,
    ) -> List[MediaCandidate]:
        """
        Search or sample up to `limit` candidates honoring `criteria`, staging all returned
        candidates without marking them consumed in `ISeenHistoryRepository`.
        """
        clamped_limit = max(1, min(20, int(limit)))
        q_clean = (query or "").strip()
        if not q_clean:
            unseen = await self.acquire_unseen_candidates(criteria, count=clamped_limit)
            for cand in unseen:
                self.stage_candidate(cand)
            return unseen[:clamped_limit]

        domain = criteria.domain
        has_systems = any(s and s.strip().lower() != "all" for s in (criteria.systems or []))
        has_genres = any(g and g.strip().lower() != "all" for g in (criteria.genres or []))
        enforce_games_criteria = has_systems or has_genres

        collected: List[MediaCandidate] = []
        seen_keys: Set[str] = set()

        for source in self.registry.get_chain(domain):
            if not source.is_configured():
                continue
            if (
                domain == "games"
                and has_systems
                and source.source_id != "arcadeitalia"
                and not ReferentialConsolidator.source_supports_criteria(
                    source.source_id, criteria
                )
            ):
                continue
            needed = clamped_limit - len(collected)
            if needed <= 0:
                break
            try:
                batch = await source.search_candidates(
                    query=q_clean,
                    criteria=criteria,
                    limit=needed,
                )
                for cand in batch or []:
                    if cand is None or not cand.id or cand.id in seen_keys:
                        continue
                    if enforce_games_criteria and not self._candidate_matches_criteria(
                        cand, criteria
                    ):
                        continue
                    fp = (
                        build_game_fingerprint(
                            str((cand.extra_meta or {}).get("system") or ""),
                            cand.title,
                        )
                        if domain == "games"
                        else ""
                    )
                    if fp and fp in seen_keys:
                        continue
                    seen_keys.add(cand.id)
                    if fp:
                        seen_keys.add(fp)
                    self.stage_candidate(cand)
                    collected.append(cand)
                    if len(collected) >= clamped_limit:
                        break
            except Exception as exc:
                logger.warning(
                    "[SOURCE CHAIN] Search on source '%s' (%s) failed for domain '%s' (query='%s'): %s",
                    source.source_id,
                    source.role,
                    domain,
                    q_clean,
                    exc,
                )

        return collected[:clamped_limit]

    async def search_on_demand(
        self,
        domain: DomainType,
        query: str,
        lang: str = "fr",
    ) -> Optional[MediaCandidate]:
        """Search across `[Primary, Fallback]` sources for an explicit on-demand query."""
        for source in self.registry.get_chain(domain):
            if not source.is_configured():
                continue
            try:
                cand = await source.search_by_query(query=query, lang=lang)
                if cand is not None:
                    self.stage_candidate(cand)
                    return cand
            except Exception as exc:
                logger.warning(
                    "[SOURCE CHAIN] On-demand search on '%s' failed for '%s': %s",
                    source.source_id,
                    query,
                    exc,
                )
        return None

    def available_filters(
        self, domain: DomainType, lang: str = "fr"
    ) -> Dict[str, List[Dict[str, str]]]:
        return self.registry.available_filters(domain=domain, lang=lang)


source_chain_service = DomainSourceChainService()
