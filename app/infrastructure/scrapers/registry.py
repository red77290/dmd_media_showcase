"""
Extensible DDD `ScraperRegistry` managing the `[Primary, Fallback]` `IScraperSource`
chain for each media domain (`games`, `movies`, `series`, `anime`).
"""

from __future__ import annotations

from typing import Dict, List

from app.domain.entities import DomainType
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.anime import AniListAdapter, JikanAnimeThemesAdapter
from app.infrastructure.scrapers.games import ArcadeItaliaAdapter, ScreenScraperAdapter
from app.infrastructure.scrapers.movies import ItunesMovieAdapter, TmdbMovieAdapter
from app.infrastructure.scrapers.series import TmdbSeriesAdapter, TvMazeAdapter


class ScraperRegistry:
    """
    Registry of domain scraper adapters (`IScraperSource`).
    Guarantees each domain has its Primary and Fallback source in priority order,
    while allowing custom `IScraperSource` implementations to be registered at runtime.
    """

    def __init__(self) -> None:
        self._chains: Dict[DomainType, List[IScraperSource]] = {
            "games": [ScreenScraperAdapter(), ArcadeItaliaAdapter()],
            "movies": [TmdbMovieAdapter(), ItunesMovieAdapter()],
            "series": [TmdbSeriesAdapter(), TvMazeAdapter()],
            "anime": [JikanAnimeThemesAdapter(), AniListAdapter()],
        }

    def register(self, source: IScraperSource, prepend: bool = False) -> None:
        """Register an additional `IScraperSource` implementation for its domain."""
        chain = self._chains.setdefault(source.domain, [])
        chain = [s for s in chain if s.source_id != source.source_id]
        if prepend or source.role == "primary":
            chain.insert(0, source)
        else:
            chain.append(source)
        self._chains[source.domain] = chain

    def get_chain(self, domain: DomainType) -> List[IScraperSource]:
        """Return the ordered `[primary, fallback, ...]` sources for `domain`."""
        return list(self._chains.get(domain, []))

    def describe_active_chains(self) -> Dict[str, str]:
        """Return a summary of configured `[primary -> fallback]` chains for `/api/v1/health`."""
        summary: Dict[str, str] = {}
        for dom, chain in self._chains.items():
            active = [s.source_id for s in chain if s.is_configured()]
            summary[dom] = " -> ".join(active) if active else "none"
        return summary

    def available_filters(self, domain: DomainType, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        """Return the filter catalog for `domain` merged with the registered sources list."""
        chain = self.get_chain(domain)
        base = chain[0].supported_filters(lang=lang) if chain else {}
        sources = [
            {
                "id": s.source_id,
                "label": f"{s.source_id} ({s.role}, {'ready' if s.is_configured() else 'unconfigured'})",
            }
            for s in chain
        ]
        return {"sources": sources, **base}


scraper_registry = ScraperRegistry()
