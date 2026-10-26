"""
Canonical Multi-Domain Taxonomy Package (`app/domain/taxonomy`).

Provides source-agnostic catalogs, generic `SynonymCatalogResolver`, game fingerprinting,
and `ReferentialConsolidator` across Games, Movies, TV Series, and Anime.
"""

from app.domain.taxonomy.cinema_genres import (
    ANIME_GENRE_CATALOG,
    MOVIE_GENRE_CATALOG,
    SERIES_GENRE_CATALOG,
)
from app.domain.taxonomy.common import (
    CANONICAL_DECADES,
    localize_label,
    normalize_category_slug,
)
from app.domain.taxonomy.consolidator import ReferentialConsolidator
from app.domain.taxonomy.game_genres import (
    GAME_GENRE_CATALOG,
    is_disallowed_non_fighting_title,
)
from app.domain.taxonomy.resolver import (
    GENRE_RESOLVERS_BY_DOMAIN,
    SYSTEM_RESOLVER,
    SynonymCatalogResolver,
    build_game_fingerprint,
    is_arcade_system,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.domain.taxonomy.systems import RETRO_SYSTEM_CATALOG

__all__ = [
    "ANIME_GENRE_CATALOG",
    "CANONICAL_DECADES",
    "GAME_GENRE_CATALOG",
    "GENRE_RESOLVERS_BY_DOMAIN",
    "MOVIE_GENRE_CATALOG",
    "RETRO_SYSTEM_CATALOG",
    "ReferentialConsolidator",
    "SERIES_GENRE_CATALOG",
    "SYSTEM_RESOLVER",
    "SynonymCatalogResolver",
    "build_game_fingerprint",
    "is_arcade_system",
    "is_disallowed_non_fighting_title",
    "localize_label",
    "normalize_category_slug",
    "resolve_canonical_genre",
    "resolve_canonical_system",
]
