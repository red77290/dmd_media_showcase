"""
Domain Ports (Interfaces) for the Hexagonal / DDD Architecture.
"""

from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.domain.ports.scraper_source import IScraperSource
from app.domain.ports.seen_repository import ISeenHistoryRepository

__all__ = [
    "IFrameStreamerPort",
    "IMediaTranscoderPort",
    "IScraperSource",
    "ISeenHistoryRepository",
]
