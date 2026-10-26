"""
Domain Ports (Interfaces) for the Hexagonal Architecture (`app/domain/ports`).
"""

from app.domain.ports.card_renderer import ICardPreviewRendererPort
from app.domain.ports.control_gateway import IMqttControlGatewayPort
from app.domain.ports.default_ports import PortProviderRegistry, default_port_registry
from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.domain.ports.profile_repository import IDmdProfileRepository
from app.domain.ports.referential_strategy import IScraperReferentialStrategy
from app.domain.ports.scraper_registry_port import IScraperRegistryPort
from app.domain.ports.scraper_source import IScraperSource
from app.domain.ports.seen_repository import ISeenHistoryRepository

__all__ = [
    "ICardPreviewRendererPort",
    "IDmdProfileRepository",
    "IFrameStreamerPort",
    "IMediaTranscoderPort",
    "IMqttControlGatewayPort",
    "IScraperReferentialStrategy",
    "IScraperRegistryPort",
    "IScraperSource",
    "ISeenHistoryRepository",
    "PortProviderRegistry",
    "default_port_registry",
]
