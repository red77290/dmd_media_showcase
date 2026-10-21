"""
Anime domain scraper adapters:
- Primary: `JikanAnimeThemesAdapter` (`api.jikan.moe/v4` + `api.animethemes.moe`)
- Fallback: `AniListAdapter` (`graphql.anilist.co` + `api.animethemes.moe`)
"""

from app.infrastructure.scrapers.anime.anilist_adapter import AniListAdapter
from app.infrastructure.scrapers.anime.jikan_animethemes_adapter import (
    JikanAnimeThemesAdapter,
    enrich_with_animethemes_webm,
)

__all__ = ["AniListAdapter", "JikanAnimeThemesAdapter", "enrich_with_animethemes_webm"]
