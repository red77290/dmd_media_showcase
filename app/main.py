"""
FastAPI Bootstrap & Composition Entrypoint for the DMD Media Showcase Server.

Exposes:
- Centralized Web Admin Dashboard (`/`, `/admin`) & Modular Static Assets (`/static/*`)
- Interactive OpenAPI / Swagger UI (`/docs`, `/swagger`, `/redoc`) & AsyncAPI 2.6 (`/asyncapi.yaml`)
- Embedded MQTT 3.1.1 Control Plane (`:1883` with `retain=True` + `LWT`) + Real-Time `ffmpeg -re` UDP streamer
- Consume-Once HTTP Polling & Session/Batch Rotation Routers (`app/infrastructure/http/routers/`)
"""

from __future__ import annotations

import sys as _boot_sys

if len(_boot_sys.argv) >= 2 and _boot_sys.argv[1] == "--internal-ytdlp":
    from app.portable_env import enforce_hermetic_environment as _enforce_ytdlp_env

    _enforce_ytdlp_env()
    import yt_dlp as _yt_dlp_mod

    _boot_sys.argv = ["yt-dlp", *_boot_sys.argv[2:]]
    raise SystemExit(_yt_dlp_mod.main())

from app.portable_env import enforce_hermetic_environment

# Lock down portable environment before any media/subprocess imports
enforce_hermetic_environment()

from contextlib import asynccontextmanager
import logging
import sys
from typing import Any, AsyncIterator, Dict, Tuple

import dmd_gif_converter
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.application.consume_once_orchestrator import consume_once_orchestrator
from app.application.source_chain_service import source_chain_service
from app.application.update_checker import release_update_checker
from app.config import settings
from app.infrastructure.http.routers import (
    clients_and_queue,
    consume_once,
    discovery,
    rotation_batch,
    studio,
    system_admin,
)
from app.infrastructure.http.runtime_reloader import apply_live_log_level
from app.infrastructure.http.security_middleware import optional_api_key_security_middleware
from app.infrastructure.mdns_responder import showcase_mdns_responder
from app.infrastructure.mqtt.showcase_mqtt_gateway import showcase_mqtt_gateway
from app.infrastructure.streaming.ffmpeg_udp_streamer import udp_clock_streamer
from app.infrastructure.persistence import (
    dmd_profile_store,
    on_demand_cache_store,
    seen_repository,
    server_config_store,
)
from app.infrastructure.scrapers.http_utils import create_async_http_client
from app.infrastructure.scrapers.registry import scraper_registry
from app.infrastructure.web.dashboard_loader import STATIC_WEB_DIR
from app.models import DomainType
from app.scheduler.worker_pool import batch_manager
from app.security.log_redactor import install_secret_log_redaction

__all__ = [
    "ALL_SHOWCASE_DOMAINS",
    "OPENAPI_TAGS",
    "_session_client_meta",
    "app",
    "batch_manager",
    "consume_once_orchestrator",
    "create_async_http_client",
    "dmd_profile_store",
    "on_demand_cache_store",
    "release_update_checker",
    "run",
    "scraper_registry",
    "seen_repository",
    "server_config_store",
    "showcase_mdns_responder",
    "showcase_mqtt_gateway",
    "source_chain_service",
]

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
install_secret_log_redaction()
logger = logging.getLogger("dmd_showcase.http")

ALL_SHOWCASE_DOMAINS: Tuple[DomainType, ...] = ("games", "movies", "series", "anime")
_session_client_meta: Dict[Tuple[str, str], Tuple[str, float]] = {}

OPENAPI_TAGS = [
    {
        "name": "System & Admin",
        "description": "Server health, centralized API credentials & source policies (`server_config.json`), and `seen_history.json` management.",
    },
    {
        "name": "Discovery",
        "description": "Live categories, 66 retro/arcade systems, genres, decades, and configurable multi-source metadata & video scraper chains.",
    },
    {
        "name": "Consume-Once Protocol",
        "description": "Stateless Consume-Once endpoints (`/config`, `/next`, `/stream/{item_id}.rgb565.stream`) with automatic disk purge after playback.",
    },
    {
        "name": "DMD Rotation Protocol",
        "description": "Proactive session/batch polling endpoints (`/session`, `/batch`, `/random`) for HTTP polling clients.",
    },
    {
        "name": "DMD Profiles & Passive Mode",
        "description": "Persistent DMD profiles (`dmd_profiles.json`), server-managed playlists (Modes 2A & 2B), `hello` announcements, and live playback controls.",
    },
    {
        "name": "On-Demand & Voice Assistant",
        "description": "Multi-candidate localized search (`/search`) and per-DMD On-Demand priority queues (`/clients/{dmd_id}/queue`, `/clients/{dmd_id}/queue/async`, `/on-demand`) for WebUI, FrontendSync, and voice assistants.",
    },
    {
        "name": "Card Studio & Simulator",
        "description": "Interactive Mode 2B Card Customizer, Full Sequence Builder, HUB75 LED Simulator (`DMDS` binary streams), and zero-retranscode promotion (`preview_id`) into active DMD queues.",
    },
    {
        "name": "Assets & Streaming",
        "description": "Binary asset streaming for mini-jackets (`.rgb565`, `.png`), panoramas, and video trailers.",
    },
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    server_config_store.apply_to_settings()
    apply_live_log_level(settings.log_level)
    logger.info(
        "[STARTUP] Starting DMD Media Showcase Server v%s (dmd_gif_converter v%s, max_workers=%d)",
        __version__,
        getattr(dmd_gif_converter, "__version__", "8.0.0"),
        settings.effective_max_workers,
    )
    batch_manager.start()
    await udp_clock_streamer.start_listener(host=settings.host, port=settings.udp_stream_port)
    await showcase_mqtt_gateway.start()
    await consume_once_orchestrator.start_server_managed_profiles()
    if settings.mdns_enabled:
        showcase_mdns_responder.hostname = settings.mdns_hostname
        await showcase_mdns_responder.start(port=settings.port)
    try:
        yield
    finally:
        await consume_once_orchestrator.stop_server_managed_profiles()
        await showcase_mdns_responder.stop()
        await showcase_mqtt_gateway.stop()
        await udp_clock_streamer.stop_listener()
        await batch_manager.stop()


app = FastAPI(
    title="DMD Media Showcase Server API",
    summary="DMD Media Showcase Server (MQTT Retain + UDP FFmpeg -re & HTTP Consume-Once) for HUB75 LED Matrices",
    description=(
        "Self-hosted multilingual media & video trailer streaming server "
        "for **ArcadeMatrix (ESP32 / ESP32-S3)** and **ArcadeMatrix_RPi** LED matrices.\n\n"
        "### Architecture Highlights\n"
        "- **Configurable Multi-Source Scraper Chains (`source_policies`)**: Ordered Primary/Secondary/Fallback chains per domain (`IGDB v4`, `ScreenScraper`, `ArcadeItalia`, `Libretro/Wikidata`, `TMDB`, `iTunes`, `TheTVDB v4`, `TVMaze`, `Jikan/AnimeThemes`, `AniList`) with automatic cross-source asset enrichment (`enrich_missing` -> composite `source_used` such as `igdb+arcadeitalia`).\n"
        "- **Strict Consume-Once (`seen_history.json`)**: Zero title repetition and immediate post-stream disk purge.\n"
        "- **Dual Transport**: Real-time MQTT (`retain=True` + `LWT`) + monotonic UDP `DMDF` streaming "
        "(`10`-byte Little-Endian `<4sHBBH` header + `<= 1408B` RGB565LE chunks — full AsyncAPI 2.6 spec at "
        "[`/asyncapi.yaml`](/asyncapi.yaml)), plus HTTP `DMDS` Streaming (`16`-byte `<4sBBHHHHH` header)."
    ),
    version=__version__,
    openapi_tags=OPENAPI_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_guard_middleware(request: Request, call_next) -> Response:
    return await optional_api_key_security_middleware(request, call_next)


app.mount("/static", StaticFiles(directory=str(STATIC_WEB_DIR)), name="static")

app.include_router(system_admin.router)
app.include_router(discovery.router)
app.include_router(clients_and_queue.router)
app.include_router(studio.router)
app.include_router(consume_once.router)
app.include_router(rotation_batch.router)


def run() -> None:
    """CLI entrypoint (`dmd-media-showcase`) supporting both HTTP (Home-Lab) and HTTPS (TLS / Reverse Proxy)."""
    import uvicorn

    ssl_kwargs: Dict[str, Any] = {}
    if settings.ssl_certfile and settings.ssl_keyfile:
        ssl_kwargs["ssl_certfile"] = settings.ssl_certfile
        ssl_kwargs["ssl_keyfile"] = settings.ssl_keyfile

    target_app: Any = app if getattr(sys, "frozen", False) else "app.main:app"
    uvicorn.run(
        target_app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=settings.trust_proxy_headers,
        forwarded_allow_ips="*",
        ws_per_message_deflate=False,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    run()
