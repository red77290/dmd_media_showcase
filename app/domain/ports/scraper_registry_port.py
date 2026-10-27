"""
Domain Port: `IScraperRegistryPort` for resolving `[Primary, Fallback]` scraper adapter chains.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from app.domain.entities import DomainType
from app.domain.ports.scraper_source import IScraperSource


class IScraperRegistryPort(ABC):
    """Hexagonal port providing ordered scraper source chains and consolidated domain filters."""

    @abstractmethod
    def register(self, source: IScraperSource, prepend: bool = False) -> None:
        """Register an `IScraperSource` implementation for its domain."""

    @abstractmethod
    def get_chain(self, domain: DomainType) -> List[IScraperSource]:
        """Return the ordered `[Primary, Fallback, ...]` adapter list for `domain`."""

    @abstractmethod
    def describe_active_chains(self) -> Dict[str, str]:
        """Return a summary string of active sources per domain."""

    @abstractmethod
    def describe_chains_detailed(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return structured status metadata (`source_id`, `role`, `configured`) per domain."""

    @abstractmethod
    def available_filters(self, domain: DomainType, lang: str = "fr") -> Dict[str, Any]:
        """Return the consolidated, localized referential specification for `domain`."""

    def get_domain_policy(self, domain: DomainType) -> Dict[str, Any]:
        """Return the active source ordering, disabled set, and `aggregation_mode` for `domain`."""
        chain = self.get_chain(domain)
        return {
            "ordered_sources": [s.source_id for s in chain],
            "disabled_sources": [],
            "aggregation_mode": "enrich_missing",
        }
