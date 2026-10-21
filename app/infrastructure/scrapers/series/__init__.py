"""
TV Series domain scraper adapters:
- Primary: `TmdbSeriesAdapter` (`api.themoviedb.org/3/discover/tv`)
- Fallback: `TvMazeAdapter` (`api.tvmaze.com/shows`)
"""

from app.infrastructure.scrapers.series.tmdb_series_adapter import TmdbSeriesAdapter
from app.infrastructure.scrapers.series.tvmaze_adapter import TvMazeAdapter

__all__ = ["TmdbSeriesAdapter", "TvMazeAdapter"]
