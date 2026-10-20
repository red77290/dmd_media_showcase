"""
FastAPI entrypoint for the DMD Media Showcase Server.

Exposes the 4 media domain APIs (`/api/v1/games`, `/api/v1/movies`, `/api/v1/series`,
`/api/v1/anime`), the proactive session & double-batch polling endpoints, the future-ready
on-demand endpoint, and the zero-overhead binary asset streamer.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

import dmd_gif_converter
from dmd_gif_converter import truncate_gif_bytes, truncate_rgb565_stream

from app import __version__
from app.config import settings
from app.models import (
    BatchStatusResponse,
    DomainType,
    HealthResponse,
    MediaShowcaseItem,
    OnDemandRequest,
    SessionRegisterRequest,
    SessionRegisterResponse,
)
from app.scheduler.worker_pool import batch_manager

# Configure human-readable structured logging to stdout for Docker container visibility
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("dmd_showcase.http")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "[STARTUP] Starting DMD Media Showcase Server v%s (dmd_gif_converter v%s, max_workers=%d)",
        __version__,
        getattr(dmd_gif_converter, "__version__", "8.0.0"),
        settings.effective_max_workers,
    )
    batch_manager.start()
    try:
        yield
    finally:
        await batch_manager.stop()


app = FastAPI(
    title="DMD Media Showcase Server",
    description=(
        "Self-hosted, stateless multilingual media & video trailer streaming server "
        "for ArcadeMatrix (ESP32 / ESP32-S3) and ArcadeMatrix_RPi LED matrices."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


DOMAIN_FILTERS: Dict[DomainType, Dict[str, List[str]]] = {
    "games": {
        "sources": ["auto", "screenscraper", "arcadeitalia", "libretro"],
        "systems": ["mame", "neogeo", "cps", "fbneo", "snes", "megadrive", "psx", "nes", "gba", "pcengine"],
        "genres": ["action", "fighting", "shootemup", "platform", "rpg", "racing", "puzzle"],
        "decades": ["1980s", "1990s", "2000s", "2010s"],
    },
    "movies": {
        "sources": ["auto", "tmdb", "wikipedia"],
        "systems": [],
        "genres": ["scifi", "action", "animation", "horror", "thriller", "adventure", "fantasy"],
        "decades": ["1970s", "1980s", "1990s", "2000s", "2010s", "2020s"],
    },
    "series": {
        "sources": ["auto", "tvmaze", "tmdb", "tvdb"],
        "systems": [],
        "genres": ["scifi", "drama", "crime", "fantasy", "mystery", "action", "comedy"],
        "decades": ["1990s", "2000s", "2010s", "2020s"],
    },
    "anime": {
        "sources": ["auto", "animethemes", "jikan", "tmdb"],
        "systems": [],
        "genres": ["shonen", "seinen", "mecha", "cyberpunk", "fantasy", "action", "adventure"],
        "decades": ["1980s", "1990s", "2000s", "2010s", "2020s"],
    },
}


@app.get("/health", response_model=HealthResponse, include_in_schema=False)
@app.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
async def get_health() -> HealthResponse:
    """Return server health, worker pool saturation, and configured scraper providers."""
    batch_manager.start()
    providers_cfg = {
        "games": (
            "screenscraper+arcadeitalia+libretro"
            if (settings.screenscraper_user or settings.screenscraper_dev_id)
            else "arcadeitalia+libretro+curated_catalog"
        ),
        "movies": "tmdb+wikipedia+curated_catalog" if settings.tmdb_api_key else "wikipedia+curated_catalog",
        "series": (
            "tmdb+tvmaze+catalog"
            if settings.tmdb_api_key
            else ("tvdb+tvmaze+catalog" if settings.tvdb_api_key else "tvmaze+curated_catalog")
        ),
        "anime": "animethemes+jikan+curated_catalog",
    }
    return HealthResponse(
        status="ok",
        version=__version__,
        converter_version=getattr(dmd_gif_converter, "__version__", "8.0.0"),
        active_workers=batch_manager.active_workers,
        max_workers=settings.effective_max_workers,
        queue_depth=batch_manager.queue_depth,
        active_sessions=len(batch_manager.sessions),
        completed_jobs_total=batch_manager.completed_jobs_total,
        avg_job_duration_s=batch_manager.avg_job_duration_s,
        providers_configured=providers_cfg,
    )


@app.get("/api/v1/{domain}/filters", tags=["Discovery"])
async def get_domain_filters(domain: DomainType) -> Dict[str, object]:
    """Return available filter values for the specified media domain."""
    return {
        "domain": domain,
        "languages": ["fr", "en", "es", "de", "it"],
        "video_modes": ["auto_action", "center_crop", "pixel_art", "anime", "cinema", "panorama"],
        "video_types": ["any", "opening", "ending", "pv", "trailer"],
        **DOMAIN_FILTERS[domain],
    }


@app.post(
    "/api/v1/{domain}/session",
    response_model=SessionRegisterResponse,
    tags=["DMD Rotation Protocol"],
)
async def register_domain_session(
    domain: DomainType,
    req: SessionRegisterRequest,
    request: Request,
) -> SessionRegisterResponse:
    """
    Step 1 of the DMD Rotation Protocol:
    Sent proactively by the DMD engine on boot or when WebUI settings are saved.
    Registers the session and immediately enqueues `count` items into the bounded
    worker pool so `CurrentBatch` is warmed up before the engine's rotation turn.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "[HTTP IN] --> POST /api/v1/%s/session from %s | client_id=%s, count=%d, lang=%s, %dx%d, "
        "primary_source=%s, image_count=%d, include_video=%s, credentials=%s",
        domain,
        client_ip,
        req.client_id,
        req.count,
        req.lang,
        req.width,
        req.height,
        req.primary_source,
        req.image_count,
        req.include_video,
        bool(req.scraper_user or req.scraper_api_key),
    )
    return await batch_manager.register_session(domain, req)


@app.get(
    "/api/v1/{domain}/batch",
    response_model=BatchStatusResponse,
    tags=["DMD Rotation Protocol"],
)
async def get_domain_batch(
    domain: DomainType,
    request: Request,
    session_id: str = Query(..., description="Session ID returned by POST /api/v1/{domain}/session"),
) -> BatchStatusResponse:
    """
    Step 2 of the DMD Rotation Protocol ("Are you ready?"):
    Ultra-fast (< 2 ms) LAN status check called by the DMD right before its rotation turn.
    - If `status == "ready"`: returns the `N` pre-rendered items and rotates/schedules `NextBatch`.
    - If `status == "warming"`: allows the DMD to cleanly skip its rotation turn (if other
      engines are active) or display static fallback cards while video jobs finish.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "[HTTP IN] --> GET /api/v1/%s/batch?session_id=%s from %s",
        domain,
        session_id,
        client_ip,
    )
    res = await batch_manager.get_batch_status(domain, session_id)
    if res is None:
        logger.warning(
            "[HTTP OUT] <-- 404 Unknown session_id '%s' for domain '%s'",
            session_id,
            domain,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Unknown or expired session_id '{session_id}' for domain '{domain}'.",
        )
    return res


@app.get(
    "/api/v1/{domain}/random",
    response_model=BatchStatusResponse,
    tags=["DMD Rotation Protocol"],
)
async def get_domain_random(
    domain: DomainType,
    request: Request,
    client_id: str = Query(default="default_dmd", description="Stable client identifier"),
    count: int = Query(default=3, ge=1, le=25),
    lang: str = Query(default="fr"),
    width: int = Query(default=128, ge=32, le=512),
    height: int = Query(default=32, ge=16, le=256),
    image_count: int = Query(default=2, ge=0, le=5),
    primary_source: str = Query(default="auto"),
    include_video: bool = Query(default=True),
    video_max_duration_s: float = Query(default=6.0, ge=1.5, le=30.0),
    video_fps: int = Query(default=15, ge=6, le=30),
    systems: Optional[str] = Query(default=None, description="Comma-separated systems"),
    genres: Optional[str] = Query(default=None, description="Comma-separated genres"),
) -> BatchStatusResponse:
    """
    Convenience single-call endpoint that automatically creates or polls a session
    associated with `client_id`.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "[HTTP IN] --> GET /api/v1/%s/random from %s (client_id=%s, count=%d, primary_source=%s, image_count=%d)",
        domain,
        client_ip,
        client_id,
        count,
        primary_source,
        image_count,
    )
    req = SessionRegisterRequest(
        client_id=client_id,
        count=count,
        lang=lang,
        width=width,
        height=height,
        image_count=image_count,
        primary_source=primary_source,
        include_video=include_video,
        video_max_duration_s=video_max_duration_s,
        video_fps=video_fps,
        systems=[s.strip() for s in systems.split(",") if s.strip()] if systems else ["mame", "neogeo", "snes"],
        genres=[g.strip() for g in genres.split(",") if g.strip()] if genres else [],
    )
    ses_id = batch_manager._make_session_id(domain, req)
    if ses_id in batch_manager.sessions:
        existing = await batch_manager.get_batch_status(domain, ses_id)
        if existing is not None:
            return existing
    reg = await batch_manager.register_session(domain, req)
    status = await batch_manager.get_batch_status(domain, reg.session_id)
    assert status is not None
    return status


@app.post(
    "/api/v1/{domain}/on-demand",
    response_model=MediaShowcaseItem,
    tags=["On-Demand & Voice Assistant"],
)
async def trigger_on_demand_media(
    domain: DomainType,
    req: OnDemandRequest,
    request: Request,
) -> MediaShowcaseItem:
    """
    Future-ready endpoint for on-demand requests (Google Home, Alexa, Home Assistant,
    or custom webhooks). Enqueues the requested media item with `HIGH_ON_DEMAND`
    priority ahead of background rotation batches.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "[HTTP IN] --> POST /api/v1/%s/on-demand from %s | query='%s', target_session=%s",
        domain,
        client_ip,
        req.query,
        req.target_session_id,
    )
    req.domain = domain
    try:
        return await batch_manager.prepare_on_demand(req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _iter_aligned_chunks(data: bytes, chunk_size: int = 8192):
    for offset in range(0, len(data), chunk_size):
        yield data[offset : offset + chunk_size]


@app.get("/api/v1/assets/{item_key}/{filename}", tags=["Assets & Streaming"])
async def stream_media_asset(
    item_key: str,
    filename: str,
    request: Request,
    max_duration_s: Optional[float] = Query(
        default=None,
        ge=0.5,
        le=30.0,
        description="Optional on-the-fly duration cap in seconds (zero re-encoding).",
    ),
) -> Response:
    """
    Serve or stream pre-rendered DMD assets (`.png`, `.rgb565`, `.gif`, `.rgb565.stream`).
    Supports $O(1)$ dynamic truncation via `?max_duration_s=` without re-running FFmpeg.
    """
    client_ip = request.client.host if request.client else "unknown"
    asset_path: Optional[Path] = batch_manager.resolve_asset_path(item_key, filename)
    if asset_path is None:
        logger.warning(
            "[STREAM OUT] <-- 404 Asset '%s' for item '%s' not found (client=%s)",
            filename,
            item_key,
            client_ip,
        )
        raise HTTPException(
            status_code=404,
            detail=f"Asset '{filename}' for item '{item_key}' is not ready or has expired.",
        )

    headers = {
        "X-DMD-Media-Ready": "1",
        "Cache-Control": f"public, max-age={settings.batch_ttl_seconds}",
    }

    if filename.endswith(".rgb565.stream"):
        if max_duration_s is not None:
            raw_bytes = truncate_rgb565_stream(asset_path, max_duration_s)
        else:
            raw_bytes = asset_path.read_bytes()
        headers["Content-Length"] = str(len(raw_bytes))
        logger.info(
            "[STREAM OUT] <-- Serving stream '%s/%s' (%.1f KB, max_duration_s=%s) to %s",
            item_key,
            filename,
            len(raw_bytes) / 1024.0,
            max_duration_s,
            client_ip,
        )
        return StreamingResponse(
            _iter_aligned_chunks(raw_bytes, chunk_size=8192),
            media_type="application/octet-stream",
            headers=headers,
        )

    if filename.endswith(".gif"):
        if max_duration_s is not None:
            raw_bytes = truncate_gif_bytes(asset_path, max_duration_s)
        else:
            raw_bytes = asset_path.read_bytes()
        headers["Content-Length"] = str(len(raw_bytes))
        logger.info(
            "[STREAM OUT] <-- Serving GIF '%s/%s' (%.1f KB, max_duration_s=%s) to %s",
            item_key,
            filename,
            len(raw_bytes) / 1024.0,
            max_duration_s,
            client_ip,
        )
        return Response(
            content=raw_bytes,
            media_type="image/gif",
            headers=headers,
        )

    logger.info(
        "[STREAM OUT] <-- Serving static asset '%s/%s' (%.1f KB) to %s",
        item_key,
        filename,
        asset_path.stat().st_size / 1024.0,
        client_ip,
    )
    if filename.endswith(".png"):
        return FileResponse(asset_path, media_type="image/png", headers=headers)

    return FileResponse(asset_path, media_type="application/octet-stream", headers=headers)


def run() -> None:
    """CLI entrypoint (`dmd-media-showcase`)."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
