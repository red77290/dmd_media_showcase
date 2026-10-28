"""
Multi-Scraper Referential Consolidator & Capability Router (`app/domain/taxonomy/consolidator.py`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria
from app.domain.taxonomy.cinema_genres import (
    ANIME_GENRE_CATALOG,
    MOVIE_GENRE_CATALOG,
    SERIES_GENRE_CATALOG,
)
from app.domain.taxonomy.common import CANONICAL_DECADES, localize_label
from app.domain.taxonomy.game_genres import GAME_GENRE_CATALOG
from app.domain.taxonomy.resolver import (
    is_arcade_system,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.domain.taxonomy.systems import RETRO_SYSTEM_CATALOG, _ALL_CONSOLE_GENRES


class ReferentialConsolidator:
    """
    Consolidates live referentials from all registered `IScraperSource` adapters in a domain
    into a single source-agnostic, localized specification for `/api/v1/{domain}/filters`.
    """

    @staticmethod
    def build_domain_referential(
        domain: DomainType,
        lang: str = "fr",
        sources_info: Optional[List[Dict[str, str]]] = None,
        extra_discovered_genres: Optional[List[Tuple[str, str]]] = None,
        extra_discovered_systems: Optional[List[Tuple[str, str]]] = None,
    ) -> Dict[str, Any]:
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        decades_list = [
            {"id": did, "label": localize_label(dmeta["labels"], short_lang, did)}
            for did, dmeta in CANONICAL_DECADES.items()
        ]

        if domain == "games":
            systems_out: List[Dict[str, Any]] = []
            seen_sys: Set[str] = set()
            for sys_id, smeta in RETRO_SYSTEM_CATALOG.items():
                seen_sys.add(sys_id)
                supported_sources = (
                    ["igdb", "screenscraper", "arcadeitalia", "libretro_wikidata"]
                    if smeta.get("is_arcade")
                    else ["igdb", "screenscraper", "libretro_wikidata"]
                )
                sys_genres = list(smeta.get("genres", _ALL_CONSOLE_GENRES))
                sys_decades = list(smeta.get("decades", ["1980s", "1990s"]))
                systems_out.append(
                    {
                        "id": sys_id,
                        "label": localize_label(smeta["labels"], short_lang, sys_id),
                        "badge": smeta["badge"],
                        "is_arcade": bool(smeta.get("is_arcade")),
                        "genres": sys_genres,
                        "decades": sys_decades,
                        "supported_genres": list(sys_genres),
                        "supported_decades": list(sys_decades),
                        "sources": supported_sources,
                    }
                )
            for raw_id, raw_label in (extra_discovered_systems or []):
                canon_sys = resolve_canonical_system(raw_id)
                if canon_sys and canon_sys not in seen_sys:
                    seen_sys.add(canon_sys)
                    dyn_genres = list(_ALL_CONSOLE_GENRES)
                    dyn_decades = ["1980s", "1990s", "2000s"]
                    systems_out.append(
                        {
                            "id": canon_sys,
                            "label": raw_label or canon_sys.upper(),
                            "badge": canon_sys[:6].upper(),
                            "is_arcade": False,
                            "genres": dyn_genres,
                            "decades": dyn_decades,
                            "supported_genres": list(dyn_genres),
                            "supported_decades": list(dyn_decades),
                            "sources": ["igdb", "screenscraper", "libretro_wikidata"],
                        }
                    )

            genres_out: List[Dict[str, Any]] = []
            seen_genres: Set[str] = set()
            for gid, gmeta in GAME_GENRE_CATALOG.items():
                seen_genres.add(gid)
                genres_out.append(
                    {
                        "id": gid,
                        "label": localize_label(gmeta["labels"], short_lang, gid),
                    }
                )
            for raw_gid, raw_glabel in (extra_discovered_genres or []):
                canon_g = resolve_canonical_genre("games", raw_gid)
                if canon_g and canon_g not in seen_genres:
                    seen_genres.add(canon_g)
                    genres_out.append({"id": canon_g, "label": raw_glabel or canon_g.title()})

            return {
                "domain": "games",
                "lang": short_lang,
                "sources": sources_info or [],
                "systems": systems_out,
                "genres": genres_out,
                "decades": decades_list[:5],
            }

        genre_cat = (
            MOVIE_GENRE_CATALOG
            if domain == "movies"
            else (SERIES_GENRE_CATALOG if domain == "series" else ANIME_GENRE_CATALOG)
        )
        genres_list: List[Dict[str, Any]] = []
        seen_dom_genres: Set[str] = set()
        for gid, gmeta in genre_cat.items():
            seen_dom_genres.add(gid)
            genres_list.append(
                {
                    "id": gid,
                    "label": localize_label(gmeta["labels"], short_lang, gid),
                }
            )
        for raw_gid, raw_glabel in (extra_discovered_genres or []):
            canon_g = resolve_canonical_genre(domain, raw_gid)
            if canon_g and canon_g not in seen_dom_genres:
                seen_dom_genres.add(canon_g)
                genres_list.append({"id": canon_g, "label": raw_glabel or canon_g.title()})

        result: Dict[str, Any] = {
            "domain": domain,
            "lang": short_lang,
            "sources": sources_info or [],
            "genres": genres_list,
            "decades": decades_list,
        }
        if domain == "anime":
            video_type_labels = {
                "any": {
                    "fr": "Opening, Ending ou Trailer",
                    "en": "Opening, Ending or Trailer",
                    "es": "Opening, Ending o Tráiler",
                },
                "opening": {
                    "fr": "Opening (OP sans crédits)",
                    "en": "Opening (Creditless OP)",
                    "es": "Opening (OP sin créditos)",
                },
                "ending": {
                    "fr": "Ending (ED sans crédits)",
                    "en": "Ending (Creditless ED)",
                    "es": "Ending (ED sin créditos)",
                },
                "trailer": {
                    "fr": "Bande-annonce (PV / Trailer)",
                    "en": "Official PV / Trailer",
                    "es": "Tráiler Oficial (PV)",
                },
            }
            result["video_types"] = [
                {"id": vt_id, "label": localize_label(vt_lbls, short_lang, vt_id)}
                for vt_id, vt_lbls in video_type_labels.items()
            ]
        return result

    @staticmethod
    def source_supports_criteria(source_id: str, criteria: FilterCriteria) -> bool:
        """Capability-aware routing preventing ArcadeItalia calls when only home consoles are requested."""
        if criteria.domain == "games" and source_id == "arcadeitalia":
            req_systems = [
                resolve_canonical_system(s)
                for s in (criteria.systems or [])
                if s and s.lower() != "all"
            ]
            if req_systems and not any(is_arcade_system(s) for s in req_systems):
                return False
        return True
