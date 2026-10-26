"""
Composition Root (`app/container.py`).

Wires concrete Infrastructure Adapters to Domain Ports (`default_port_registry`) using
lazy per-port factories so `app/domain/` and `app/application/` remain 100% pure of
`app.infrastructure.*` imports without circular initialization cycles.
"""

from __future__ import annotations

from typing import Any

from app.domain.ports.default_ports import default_port_registry


def _resolve_mqtt_gateway() -> Any:
    from app.infrastructure.mqtt.showcase_mqtt_gateway import showcase_mqtt_gateway

    return showcase_mqtt_gateway


def _resolve_profile_repository() -> Any:
    from app.infrastructure.persistence.dmd_profile_store import dmd_profile_store

    return dmd_profile_store


def _resolve_seen_repository() -> Any:
    from app.infrastructure.persistence.json_seen_repository import seen_repository

    return seen_repository


def _resolve_card_preview_renderer() -> Any:
    from app.infrastructure.rendering.card_layout_engine import pil_card_preview_renderer

    return pil_card_preview_renderer


def _resolve_scraper_registry() -> Any:
    from app.infrastructure.scrapers.registry import scraper_registry

    return scraper_registry


def _resolve_frame_streamer() -> Any:
    from app.infrastructure.streaming.ffmpeg_udp_streamer import udp_clock_streamer

    return udp_clock_streamer


def _resolve_media_transcoder() -> Any:
    from app.infrastructure.transcoder.dmd_converter_adapter import dmd_transcoder_adapter

    return dmd_transcoder_adapter


def _resolve_http_client_factory() -> Any:
    from app.infrastructure.scrapers.http_utils import create_async_http_client

    return create_async_http_client


def register_default_ports() -> None:
    """Bind all default Infrastructure singletons into `default_port_registry`."""
    default_port_registry.register("mqtt_gateway", _resolve_mqtt_gateway)
    default_port_registry.register("profile_repository", _resolve_profile_repository)
    default_port_registry.register("seen_repository", _resolve_seen_repository)
    default_port_registry.register("card_preview_renderer", _resolve_card_preview_renderer)
    default_port_registry.register("scraper_registry", _resolve_scraper_registry)
    default_port_registry.register("frame_streamer", _resolve_frame_streamer)
    default_port_registry.register("media_transcoder", _resolve_media_transcoder)
    default_port_registry.register("http_client_factory", _resolve_http_client_factory)


register_default_ports()
