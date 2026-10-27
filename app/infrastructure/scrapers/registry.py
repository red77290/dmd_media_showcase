"""
Configurable Multi-Source `ScraperRegistry` managing the ordered `IScraperSource`
chain for each media domain (`games`, `movies`, `series`, `anime`), honoring live
`source_policies` (`ordered_sources`, `disabled_sources`, `aggregation_mode`) from
`server_config.json`, and consolidating domain referentials via `ReferentialConsolidator`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.domain.entities import DomainType
from app.domain.ports.scraper_registry_port import IScraperRegistryPort
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import ReferentialConsolidator
from app.infrastructure.persistence.server_config_store import (
    DEFAULT_SOURCE_POLICIES,
    normalize_source_policies,
    server_config_store,
)
from app.infrastructure.scrapers.anime import AniListAdapter, JikanAnimeThemesAdapter
from app.infrastructure.scrapers.games import (
    ArcadeItaliaAdapter,
    IgdbGamesAdapter,
    LibretroWikidataAdapter,
    ScreenScraperAdapter,
)
from app.infrastructure.scrapers.movies import ItunesMovieAdapter, TmdbMovieAdapter
from app.infrastructure.scrapers.referential_store import (
    anilist_referential_strategy,
    jikan_referential_strategy,
    tvmaze_referential_strategy,
)
from app.infrastructure.scrapers.series import TmdbSeriesAdapter, TvMazeAdapter
from app.infrastructure.scrapers.tvdb_adapter import TvdbSeriesAdapter


class ScraperRegistry(IScraperRegistryPort):
    """
    Registry of domain scraper adapters (`IScraperSource`).
    Dynamically orders and filters each domain's adapter chain according to
    `server_config_store` `source_policies` while supporting runtime registration.
    """

    def __init__(self) -> None:
        self._chains: Dict[DomainType, List[IScraperSource]] = {
            "games": [
                IgdbGamesAdapter(),
                ScreenScraperAdapter(),
                ArcadeItaliaAdapter(),
                LibretroWikidataAdapter(),
            ],
            "movies": [TmdbMovieAdapter(), ItunesMovieAdapter()],
            "series": [TmdbSeriesAdapter(), TvdbSeriesAdapter(), TvMazeAdapter()],
            "anime": [JikanAnimeThemesAdapter(), AniListAdapter()],
        }
        self._prepended_ids: Dict[DomainType, List[str]] = {}

    def register(self, source: IScraperSource, prepend: bool = False) -> None:
        """Register an additional `IScraperSource` implementation for its domain."""
        chain = self._chains.setdefault(source.domain, [])
        chain = [s for s in chain if s.source_id != source.source_id]
        prep_map = getattr(self, "_prepended_ids", None)
        if prepend or source.role == "primary":
            chain.insert(0, source)
            if isinstance(prep_map, dict) and prepend:
                lst = prep_map.setdefault(source.domain, [])
                if source.source_id not in lst:
                    lst.insert(0, source.source_id)
        else:
            chain.append(source)
        self._chains[source.domain] = chain

    def get_domain_policy(self, domain: DomainType) -> Dict[str, Any]:
        """Return normalized `{ordered_sources, disabled_sources, aggregation_mode}` for `domain`."""
        cfg = server_config_store.get_all()
        policies = normalize_source_policies(cfg.get("source_policies"))
        dom_pol = policies.get(domain) or DEFAULT_SOURCE_POLICIES.get(domain) or {
            "ordered_sources": [],
            "disabled_sources": [],
            "aggregation_mode": "enrich_missing",
        }
        return {
            "ordered_sources": list(dom_pol.get("ordered_sources") or []),
            "disabled_sources": list(dom_pol.get("disabled_sources") or []),
            "aggregation_mode": str(dom_pol.get("aggregation_mode") or "enrich_missing"),
        }

    def _ordered_domain_sources(
        self, domain: DomainType
    ) -> Tuple[List[IScraperSource], Dict[str, Any]]:
        raw = list(self._chains.get(domain, []))
        policy = self.get_domain_policy(domain)
        order_list: List[str] = policy["ordered_sources"]
        order_index = {sid: idx for idx, sid in enumerate(order_list)}
        prepended = list((getattr(self, "_prepended_ids", None) or {}).get(domain, []))
        prep_index = {sid: idx for idx, sid in enumerate(prepended)}

        def _sort_key(item: Tuple[int, IScraperSource]) -> Tuple[int, int, int]:
            orig_idx, src = item
            if src.source_id in prep_index:
                return (0, prep_index[src.source_id], orig_idx)
            if src.source_id in order_index:
                return (1, order_index[src.source_id], orig_idx)
            return (2, orig_idx, orig_idx)

        ordered = [src for _, src in sorted(enumerate(raw), key=_sort_key)]
        return ordered, policy

    def get_chain(self, domain: DomainType) -> List[IScraperSource]:
        """Return the ordered active (non-disabled) `IScraperSource` list for `domain`."""
        ordered, policy = self._ordered_domain_sources(domain)
        disabled = set(policy.get("disabled_sources") or [])
        return [s for s in ordered if s.source_id not in disabled]

    def describe_active_chains(self) -> Dict[str, str]:
        """Return a summary of active configured chains for `/api/v1/health`."""
        summary: Dict[str, str] = {}
        for dom in ("games", "movies", "series", "anime"):
            chain = self.get_chain(dom)
            active = [s.source_id for s in chain if s.is_configured()]
            summary[dom] = " -> ".join(active) if active else "none"
        return summary

    def describe_chains_detailed(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return structured multi-source chain metadata per domain for Health & Admin UI."""
        detailed: Dict[str, List[Dict[str, Any]]] = {}
        for dom in ("games", "movies", "series", "anime"):
            ordered, policy = self._ordered_domain_sources(dom)
            disabled = set(policy.get("disabled_sources") or [])
            agg_mode = str(policy.get("aggregation_mode") or "enrich_missing")
            entries: List[Dict[str, Any]] = []
            for idx, src in enumerate(ordered):
                configured = bool(src.is_configured())
                in_cooldown = bool(src.is_in_cooldown())
                health = "cooldown" if in_cooldown else ("unconfigured" if not configured else "ok")
                entries.append(
                    {
                        "source_id": src.source_id,
                        "display_name": src.display_name or src.source_id,
                        "role": "primary" if idx == 0 else "fallback",
                        "priority": idx + 1,
                        "enabled": src.source_id not in disabled,
                        "configured": configured,
                        "aggregation_mode": agg_mode,
                        "health": health,
                    }
                )
            detailed[dom] = entries
        return detailed

    def available_filters(self, domain: DomainType, lang: str = "fr") -> Dict[str, Any]:
        """
        Return the consolidated, localized referential for `domain` (`systems`, `genres`, `decades`,
        `sources`) merging canonical taxonomy and live-harvested scraper referentials.
        """
        detailed_dom = self.describe_chains_detailed().get(domain, [])
        sources = [
            {
                "id": e["source_id"],
                "label": (
                    f"{e['display_name']} "
                    f"({e['role']}, {'ready' if e['configured'] else 'unconfigured'})"
                ),
                "display_name": e["display_name"],
                "role": e["role"],
                "priority": e["priority"],
                "enabled": e["enabled"],
                "configured": e["configured"],
                "health": e["health"],
            }
            for e in detailed_dom
            if e.get("enabled", True)
        ]
        extra_genres: List[Tuple[str, str]] = []
        if domain == "anime":
            jikan_data = jikan_referential_strategy.store.load()
            for slug, gmeta in ((jikan_data.get("metadata") or {}).get("genres") or {}).items():
                if isinstance(gmeta, dict) and gmeta.get("label"):
                    extra_genres.append((str(slug), str(gmeta["label"])))
            anilist_data = anilist_referential_strategy.store.load()
            for gname in anilist_data.get("tokens") or []:
                if gname:
                    extra_genres.append((str(gname), str(gname)))
        elif domain == "series":
            tvm_data = tvmaze_referential_strategy.store.load()
            for gname in tvm_data.get("tokens") or []:
                if gname:
                    extra_genres.append((str(gname), str(gname)))

        return ReferentialConsolidator.build_domain_referential(
            domain=domain,
            lang=lang,
            sources_info=sources,
            extra_discovered_genres=extra_genres,
        )


scraper_registry = ScraperRegistry()
