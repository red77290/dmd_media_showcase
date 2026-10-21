"""
Games domain scraper adapters:
- Primary: `ScreenScraperAdapter` (`api.screenscraper.fr`)
- Fallback: `ArcadeItaliaAdapter` (`adb.arcadeitalia.net`)
"""

from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter
from app.infrastructure.scrapers.games.screenscraper_adapter import ScreenScraperAdapter

__all__ = ["ArcadeItaliaAdapter", "ScreenScraperAdapter"]
