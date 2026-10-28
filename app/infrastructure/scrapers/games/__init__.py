"""
Games domain scraper adapters:
- Primary: `IgdbGamesAdapter` (`api.igdb.com/v4`) & `ScreenScraperAdapter` (`api.screenscraper.fr`)
- Fallback: `ArcadeItaliaAdapter` (`adb.arcadeitalia.net`) & `LibretroWikidataAdapter`
"""

from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter
from app.infrastructure.scrapers.games.igdb_adapter import IgdbGamesAdapter
from app.infrastructure.scrapers.games.libretro_wikidata_adapter import LibretroWikidataAdapter
from app.infrastructure.scrapers.games.screenscraper_adapter import ScreenScraperAdapter

__all__ = [
    "ArcadeItaliaAdapter",
    "IgdbGamesAdapter",
    "LibretroWikidataAdapter",
    "ScreenScraperAdapter",
]
