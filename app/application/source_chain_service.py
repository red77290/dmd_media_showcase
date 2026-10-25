"""
Application Service: `DomainSourceChainService`.

Orchestrates the `[Primary, Fallback]` `IScraperSource` adapters for each domain (`games`,
`movies`, `series`, `anime`) while enforcing strict `ISeenHistoryRepository` exclusion so
no title is ever repeated.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.seen_repository import ISeenHistoryRepository
from app.domain.taxonomy import (
    ReferentialConsolidator,
    build_game_fingerprint,
    is_arcade_system,
    is_disallowed_non_fighting_title,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.persistence.json_seen_repository import seen_repository
from app.infrastructure.scrapers.registry import ScraperRegistry, scraper_registry

logger = logging.getLogger("dmd_showcase.application.source_chain")


class DomainSourceChainService:
    """
    Executes the 2-source `[Primary, Fallback]` chain for a domain and filters out
    all previously consumed item IDs stored in `ISeenHistoryRepository`.
    """

    def __init__(
        self,
        registry: Optional[ScraperRegistry] = None,
        history_repo: Optional[ISeenHistoryRepository] = None,
    ) -> None:
        self.registry = registry or scraper_registry
        self.history_repo = history_repo or seen_repository

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
            if cand.id.startswith("games_adb_") and not any(is_arcade_system(s) for s in req_systems):
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
        chain: List,
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

        # Only reset domain seen_history when a probe pass without persisted_seen proves
        # that the catalog is genuinely available and exhausted (never wipe history on
        # transient HTTP timeouts, rate limits, or empty filters!).
        if not results and persisted_seen:
            probe_seen: Set[str] = set(extra_exclude_ids or set())
            if extra_exclude_ids and hasattr(self.history_repo, "get_seen_fingerprints"):
                for ex_id in extra_exclude_ids:
                    probe_seen.update(
                        self.history_repo.get_seen_fingerprints(domain, include_item_ids={ex_id})
                        - self.history_repo.get_seen_fingerprints(domain)
                    )
            probe_results = await self._collect_from_chain(
                chain=chain,
                criteria=criteria,
                working_seen=probe_seen,
                count=count,
            )
            if probe_results:
                logger.info(
                    "[SOURCE CHAIN] Catalog exhausted for domain '%s' (%d seen), resetting seen_history",
                    domain,
                    len(persisted_seen),
                )
                self.history_repo.reset(domain)
                for cand in probe_results:
                    self._stage_candidate_fp(domain, cand)
                results = probe_results

        return results[:count]

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
            cand = await source.search_by_query(query=query, lang=lang)
            if cand is not None:
                return cand
        return None

    def available_filters(self, domain: DomainType, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return self.registry.available_filters(domain=domain, lang=lang)


source_chain_service = DomainSourceChainService()
