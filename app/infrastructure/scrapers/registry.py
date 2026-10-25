"""
Extensible DDD `ScraperRegistry` managing the `[Primary, Fallback]` `IScraperSource`
chain for each media domain (`games`, `movies`, `series`, `anime`) and consolidating
their live referentials via `ReferentialConsolidator`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.domain.entities import DomainType
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import ReferentialConsolidator
from app.infrastructure.scrapers.anime import AniListAdapter, JikanAnimeThemesAdapter
from app.infrastructure.scrapers.games import ArcadeItaliaAdapter, ScreenScraperAdapter
from app.infrastructure.scrapers.movies import ItunesMovieAdapter, TmdbMovieAdapter
from app.infrastructure.scrapers.referential_store import (
    anilist_referential_strategy,
    jikan_referential_strategy,
    tvmaze_referential_strategy,
)
from app.infrastructure.scrapers.series import TmdbSeriesAdapter, TvMazeAdapter
from app.infrastructure.scrapers.tvdb_adapter import TvdbSeriesAdapter


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
            "series": [TmdbSeriesAdapter(), TvdbSeriesAdapter(), TvMazeAdapter()],
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
        for dom in ("games", "movies", "series", "anime"):
            chain = self._chains.get(dom, [])
            active = [s.source_id for s in chain if s.is_configured()]
            summary[dom] = " -> ".join(active) if active else "none"
        return summary

    def describe_chains_detailed(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return structured `[primary, fallback, ...]` adapter metadata per domain for the Admin UI."""
        detailed: Dict[str, List[Dict[str, Any]]] = {}
        for dom in ("games", "movies", "series", "anime"):
            chain = self._chains.get(dom, [])
            detailed[dom] = [
                {
                    "source_id": s.source_id,
                    "role": s.role,
                    "configured": bool(s.is_configured()),
                }
                for s in chain
            ]
        return detailed

    def available_filters(self, domain: DomainType, lang: str = "fr") -> Dict[str, Any]:
        """
        Return the consolidated, localized referential for `domain` (`systems`, `genres`, `decades`,
        `sources`) merging canonical taxonomy and live-harvested scraper referentials.
        """
        chain = self.get_chain(domain)
        sources = [
            {
                "id": s.source_id,
                "label": f"{s.source_id} ({s.role}, {'ready' if s.is_configured() else 'unconfigured'})",
            }
            for s in chain
        ]
        extra_genres: List[Tuple[str, str]] = []
        if domain == "anime":
            jikan_data = jikan_referential_strategy.store.load()
            for slug, gmeta in ((jikan_data.get("metadata") or {}).get("genres") or {}).items():
                if isinstance(gmeta, dict) and gmeta.get("label"):
                    extra_genres.append((str(slug), str(gmeta["label"])))
            anilist_data = anilist_referential_strategy.store.load()
            for gname in (anilist_data.get("tokens") or []):
                if gname:
                    extra_genres.append((str(gname), str(gname)))
        elif domain == "series":
            tvm_data = tvmaze_referential_strategy.store.load()
            for gname in (tvm_data.get("tokens") or []):
                if gname:
                    extra_genres.append((str(gname), str(gname)))

        return ReferentialConsolidator.build_domain_referential(
            domain=domain,
            lang=lang,
            sources_info=sources,
            extra_discovered_genres=extra_genres,
        )


scraper_registry = ScraperRegistry()
