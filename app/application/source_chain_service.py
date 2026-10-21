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

        results: List[MediaCandidate] = []
        for source in chain:
            if not source.is_configured():
                continue
            needed = count - len(results)
            if needed <= 0:
                break
            try:
                batch = await source.fetch_random_unseen(
                    criteria=criteria,
                    seen_ids=working_seen,
                    count=needed,
                )
                for cand in batch:
                    if cand.id not in persisted_seen:
                        results.append(cand)
                        working_seen.add(cand.id)
                        if len(results) >= count:
                            break
            except Exception as exc:
                logger.warning(
                    "[SOURCE CHAIN] Source '%s' (%s) failed for domain '%s': %s",
                    source.source_id,
                    source.role,
                    domain,
                    exc,
                )

        # If catalog is exhausted for this filter, reset domain seen history and retry once
        if not results and persisted_seen:
            logger.info(
                "[SOURCE CHAIN] Catalog exhausted for domain '%s' (%d seen), resetting seen_history",
                domain,
                len(persisted_seen),
            )
            self.history_repo.reset(domain)
            working_seen = set(extra_exclude_ids or set())
            for source in chain:
                if not source.is_configured():
                    continue
                needed = count - len(results)
                if needed <= 0:
                    break
                batch = await source.fetch_random_unseen(
                    criteria=criteria,
                    seen_ids=working_seen,
                    count=needed,
                )
                results.extend(batch[:needed])

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
