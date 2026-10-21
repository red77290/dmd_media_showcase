"""
Movies domain scraper adapters:
- Primary: `TmdbMovieAdapter` (`api.themoviedb.org/3/discover/movie`)
- Fallback: `ItunesMovieAdapter` (`itunes.apple.com/search?media=movie`)
"""

from app.infrastructure.scrapers.movies.itunes_movie_adapter import ItunesMovieAdapter
from app.infrastructure.scrapers.movies.tmdb_movie_adapter import TmdbMovieAdapter

__all__ = ["ItunesMovieAdapter", "TmdbMovieAdapter"]
