"""
Games domain scraper adapters:
- Primary: `ScreenScraperAdapter` (`api.screenscraper.fr`)
- Fallback: `ArcadeItaliaAdapter` (`https://adb.arcadeitalia.net` for Arcade/MAME + `LibretroWikidataAdapter` for Consoles/Computers)
"""

from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter
from app.infrastructure.scrapers.games.libretro_wikidata_adapter import LibretroWikidataAdapter
from app.infrastructure.scrapers.games.screenscraper_adapter import ScreenScraperAdapter

__all__ = ["ArcadeItaliaAdapter", "LibretroWikidataAdapter", "ScreenScraperAdapter"]
