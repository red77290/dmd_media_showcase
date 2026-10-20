"""
Multilingual media providers for Games, Movies, TV Series, and Anime.
"""

from app.providers.base import BaseMediaProvider, RawMediaCandidate
from app.providers.games import GamesProvider
from app.providers.movies import MoviesProvider
from app.providers.series import SeriesProvider
from app.providers.anime import AnimeProvider

__all__ = [
    "BaseMediaProvider",
    "RawMediaCandidate",
    "GamesProvider",
    "MoviesProvider",
    "SeriesProvider",
    "AnimeProvider",
]
