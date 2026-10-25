"""
Domain Port: `IScraperSource` interface implemented by all infrastructure scraper adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate


class IScraperSource(ABC):
    """
    Domain port representing a single dedicated scraper source for a media domain
    (e.g. `ScreenScraperAdapter`, `ArcadeItaliaAdapter`, `TmdbMovieAdapter`, `ItunesMovieAdapter`,
    `TmdbSeriesAdapter`, `TvMazeAdapter`, `JikanAnimeThemesAdapter`, `AniListAdapter`).
    """

    source_id: str
    domain: DomainType
    role: str  # "primary" or "fallback"

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this source is ready to be queried (e.g. API key present or keyless)."""

    @abstractmethod
    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        """Return the supported systems, genres, and decades for this source."""

    @abstractmethod
    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        """
        Fetch up to `count` random candidates from the live remote catalog using uniform
        random pagination, strictly excluding any candidate ID present in `seen_ids`.
        """

    @abstractmethod
    async def search_by_query(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[MediaCandidate]:
        """Search and return the best matching candidate for an on-demand query."""

    async def search_candidates(
        self,
        query: str,
        criteria: FilterCriteria,
        limit: int = 8,
    ) -> List[MediaCandidate]:
        """Search and return up to `limit` matching candidates honoring `criteria`."""
        cand = await self.search_by_query(query=query, lang=criteria.lang)
        return [cand] if cand is not None else []
