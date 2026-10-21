"""
Infrastructure Scrapers package (Ports & Adapters implementation of `IScraperSource`).
"""

from app.infrastructure.scrapers.registry import ScraperRegistry, scraper_registry

__all__ = ["ScraperRegistry", "scraper_registry"]
