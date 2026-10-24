"""
FastAPI entrypoint for the DMD Media Showcase Server.

Exposes:
- Centralized Web Admin Dashboard (`/`, `/admin`) & Interactive Swagger UI (`/docs`, `/swagger`, `/redoc`)
- Embedded MQTT 3.1.1 Control Plane (`:1883` with `retain=True` + `LWT`) + Real-Time `ffmpeg -re` UDP streamer
- Consume-Once HTTP Polling endpoints (`/api/v1/{domain}/config`, `/api/v1/{domain}/next`, `/api/v1/{domain}/stream/{item_id}.rgb565.stream`)
- Backward-compatible session/batch polling endpoints (`/session`, `/batch`, `/random`, `/on-demand`)
"""

from __future__ import annotations

from app.portable_env import enforce_hermetic_environment

# Lock down portable environment before any media/subprocess imports
enforce_hermetic_environment()

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import dmd_gif_converter
from dmd_gif_converter import truncate_gif_bytes, truncate_rgb565_stream
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from app import __version__
from app.application.consume_once_orchestrator import consume_once_orchestrator
from app.asyncapi_spec import ASYNCAPI_YAML
from app.config import settings
from app.domain.entities import PanelGeometry
from app.infrastructure.mdns_responder import showcase_mdns_responder
from app.infrastructure.mqtt.showcase_mqtt_gateway import showcase_mqtt_gateway
from app.infrastructure.persistence import seen_repository, server_config_store
from app.infrastructure.scrapers.http_utils import create_async_http_client
from app.infrastructure.scrapers.referential_store import (
    anilist_referential_strategy,
    arcadeitalia_referential_strategy,
    jikan_referential_strategy,
    libretro_wikidata_referential_strategy,
    tvmaze_referential_strategy,
)
from app.infrastructure.scrapers.registry import scraper_registry
from app.infrastructure.web_admin import ADMIN_DASHBOARD_HTML
from app.models import (
    AdminServerConfigPayload,
    BatchStatusResponse,
    DomainType,
    HealthResponse,
    MediaShowcaseItem,
    OnDemandRequest,
    SessionRegisterRequest,
    SessionRegisterResponse,
)
from app.scheduler.worker_pool import batch_manager

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("dmd_showcase.http")

OPENAPI_TAGS = [
    {
        "name": "System & Admin",
        "description": "Server health, centralized API credentials (`server_config.json`), and `seen_history.json` management.",
    },
    {
        "name": "Discovery",
        "description": "Live categories, systems, genres, decades, and `[Primary, Fallback]` DDD scraper sources.",
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
        "name": "On-Demand & Voice Assistant",
        "description": "Priority queue endpoint (`POST /on-demand`) for voice assistants and webhooks.",
    },
    {
        "name": "Assets & Streaming",
        "description": "Binary asset streaming for mini-jackets (`.rgb565`, `.png`), panoramas, and video trailers.",
    },
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "[STARTUP] Starting DMD Media Showcase Server v%s (dmd_gif_converter v%s, max_workers=%d)",
        __version__,
        getattr(dmd_gif_converter, "__version__", "8.0.0"),
        settings.effective_max_workers,
    )
    batch_manager.start()
    await showcase_mqtt_gateway.start()
    if settings.mdns_enabled:
        showcase_mdns_responder.hostname = settings.mdns_hostname
        await showcase_mdns_responder.start(port=settings.port)
    try:
        yield
    finally:
        await showcase_mdns_responder.stop()
        await showcase_mqtt_gateway.stop()
        await batch_manager.stop()


app = FastAPI(
    title="DMD Media Showcase Server API",
    summary="DDD Media Showcase Server (MQTT Retain + UDP FFmpeg -re & HTTP Consume-Once) for HUB75 DMDs",
    description=(
        "Self-hosted multilingual media & video trailer streaming server "
        "for **ArcadeMatrix (ESP32 / ESP32-S3)** and **ArcadeMatrix_RPi** LED matrices.\n\n"
        "### Architecture Highlights\n"
        "- **DDD Ports & Adapters (`IScraperSource`)**: 2 dedicated sources per domain (`Primary` + `Fallback`).\n"
        "- **Strict Consume-Once (`seen_history.json`)**: Zero title repetition and immediate post-stream disk purge.\n"
        "- **Dual Transport**: Real-time MQTT (`retain=True` + `LWT`) + `ffmpeg -re` UDP `DMDF` streaming "
        "(`10`-byte Little-Endian `<4sHBBH` header + `<= 1024B` RGB565LE chunks — full AsyncAPI 2.6 spec at "
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
async def optional_api_key_security_middleware(request: Request, call_next):
    """
    Hybrid Home-Lab HTTP (insecure, zero-config for ESP32/RPi) & Scalable Online HTTPS/Token Guard:
    - When `showcase_api_key` is empty (default Home-Lab mode): all HTTP/HTTPS requests are allowed.
    - When `showcase_api_key` is configured (Online/Cloud deployment): `/api/v1/*` requests
      (except `/api/v1/health`) must supply a matching `X-Showcase-Key` header,
      `Authorization: Bearer <key>` header, or `?api_key=<key>` query parameter.
    """
    path = request.url.path
    if path.startswith("/api/v1/") and path not in ("/api/v1/health", "/api/v1/asyncapi.yaml"):
        expected_key = str(
            server_config_store.get_all().get("showcase_api_key") or settings.showcase_api_key or ""
        ).strip()
        if expected_key:
            hdr_key = (request.headers.get("x-showcase-key") or "").strip()
            auth_hdr = (request.headers.get("authorization") or "").strip()
            bearer_key = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else ""
            q_key = (request.query_params.get("api_key") or "").strip()
            if expected_key not in (hdr_key, bearer_key, q_key):
                return Response(
                    content='{"detail":"Unauthorized: valid X-Showcase-Key or Bearer token required."}',
                    status_code=401,
                    media_type="application/json",
                )
    return await call_next(request)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def web_admin_dashboard() -> HTMLResponse:
    """Serve the centralized Web Administration Dashboard (`http://dmdmediashowcase.local:8890/`)."""
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML)


@app.get("/swagger", include_in_schema=False)
async def swagger_alias() -> HTMLResponse:
    """Alias route serving the interactive Swagger UI at `/swagger`."""
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - Swagger UI",
    )


@app.get("/asyncapi.yaml", include_in_schema=False)
@app.get(
    "/api/v1/asyncapi.yaml",
    tags=["System & Admin"],
    summary="Get AsyncAPI 2.6 Specification (MQTT 3.1.1 & UDP DMDF Streaming)",
    responses={
        200: {
            "content": {"application/yaml": {"schema": {"type": "string"}}},
            "description": "AsyncAPI 2.6.0 YAML specification for MQTT `:1883` topics and UDP `DMDF` `<4sHBBH` frame datagrams.",
        }
    },
)
async def get_asyncapi_spec() -> Response:
    """
    Serve the authoritative AsyncAPI 2.6.0 specification describing:
    - MQTT 3.1.1 retained & event channels (`status`, `referential`, `config`, `ready`, `events`, `stream_state`)
    - Real-time UDP `DMDF` binary frame chunk datagrams (`10`-byte Little-Endian `<4sHBBH` header + `<= 1024B` RGB565LE)
    """
    return Response(content=ASYNCAPI_YAML, media_type="application/yaml; charset=utf-8")


@app.get("/health", response_model=HealthResponse, include_in_schema=False)
@app.get("/api/v1/health", response_model=HealthResponse, tags=["System & Admin"])
async def get_health() -> HealthResponse:
    """Return server health, worker pool saturation, MQTT broker mode, and DDD scraper chains."""
    batch_manager.start()
    srv_cfg = server_config_store.get_all()
    mqtt_port = (
        showcase_mqtt_gateway.embedded_broker.port
        if showcase_mqtt_gateway.embedded_broker
        else int(srv_cfg.get("mqtt_port") or 1883)
    )
    return HealthResponse(
        status="ok",
        version=__version__,
        converter_version=getattr(dmd_gif_converter, "__version__", "8.0.0"),
        active_workers=batch_manager.active_workers,
        max_workers=settings.effective_max_workers,
        queue_depth=batch_manager.queue_depth,
        active_sessions=len(batch_manager.sessions) + len(consume_once_orchestrator.client_configs),
        completed_jobs_total=batch_manager.completed_jobs_total,
        avg_job_duration_s=batch_manager.avg_job_duration_s,
        providers_configured=scraper_registry.describe_active_chains(),
        mqtt_mode=str(srv_cfg.get("mqtt_mode") or "embedded"),
        mqtt_port=mqtt_port,
        seen_history_counts=seen_repository.stats(),
    )


@app.get("/api/v1/admin/config", tags=["System & Admin"])
async def get_admin_config() -> Dict[str, Any]:
    """
    Return sanitized server configuration (`server_config.json`), encrypted vault status,
    active scraper chains, connected DMD clients, and `seen_history.json` counts.
    Developer API keys are never exposed in plaintext.
    """
    return {
        "config": server_config_store.get_public_admin_config(),
        "vault_status": server_config_store.get_vault_status(),
        "providers": scraper_registry.describe_active_chains(),
        "seen_history": seen_repository.stats(),
        "clients": consume_once_orchestrator.describe_connected_clients(),
    }


@app.post("/api/v1/admin/config", tags=["System & Admin"])
async def update_admin_config(payload: AdminServerConfigPayload) -> Dict[str, Any]:
    """Update centralized user scraper credentials and MQTT settings in `./data/server_config.json`."""
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    updated = server_config_store.update(updates)
    return {
        "status": "saved",
        "config": updated,
        "vault_status": server_config_store.get_vault_status(),
        "providers": scraper_registry.describe_active_chains(),
    }


@app.post("/api/v1/admin/history/reset", tags=["System & Admin"])
async def reset_seen_history(
    domain: Optional[DomainType] = Query(default=None, description="Optional domain to reset (or all if omitted)"),
) -> Dict[str, Any]:
    """Reset `seen_history.json` for a single domain or all domains."""
    cleared = seen_repository.reset(domain)
    return {
        "status": "reset",
        "domain": domain or "all",
        "cleared_count": cleared,
        "seen_history": seen_repository.stats(),
    }


@app.post("/api/v1/admin/referentials/sync", tags=["System & Admin"])
async def sync_live_referentials() -> Dict[str, Any]:
    """Force real-time synchronization of live upstream scraper referentials (`./data/referentials/*.json`)."""
    strategies = (
        arcadeitalia_referential_strategy,
        libretro_wikidata_referential_strategy,
        jikan_referential_strategy,
        anilist_referential_strategy,
        tvmaze_referential_strategy,
    )
    summary: Dict[str, int] = {}
    async with create_async_http_client(timeout=6.0) as client:
        for strat in strategies:
            ref = await strat.sync_referential(client, force=True)
            summary[strat.source_id] = len(ref.get("tokens") or [])
    default_lang = str(server_config_store.get_all().get("default_lang") or "fr")
    await showcase_mqtt_gateway.publish_catalog_referentials(lang=default_lang)
    return {"status": "synced", "referential_counts": summary}


@app.get("/api/v1/{domain}/filters", tags=["Discovery"])
@app.get("/api/v1/{domain}/referential", tags=["Discovery"])
async def get_domain_filters(
    domain: DomainType,
    lang: str = Query(default="fr", description="Preferred UI language (`fr`, `en`, `es`, `de`, `it`, `ja`)"),
) -> Dict[str, object]:
    """Return consolidated localized filter categories, relational system capabilities, genres, decades, and DDD sources."""
    filters = scraper_registry.available_filters(domain=domain, lang=lang)
    return {
        "domain": domain,
        "languages": ["fr", "en", "es", "de", "it", "ja"],
        "text_scroll_speeds": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "slow", "normal", "fast", "turbo"],
        "video_modes": ["auto_action", "panorama", "center_crop"],
        "color_boost_modes": ["auto", "pixel_art", "anime", "cinema"],
        "video_types": ["any", "opening", "ending", "pv", "trailer"],
        **filters,
    }


@app.post("/api/v1/{domain}/config", tags=["Consume-Once Protocol"])
async def register_consume_once_config(
    domain: DomainType,
    req: SessionRegisterRequest,
) -> Dict[str, Any]:
    """
    HTTP equivalent of publishing `dmd_showcase/clients/{dmd_id}/config` (`retain=True`).
    Registers the DMD client's panel geometry and filter criteria and triggers background
    preparation of 1 unseen `PreparedShowcasePackage`.
    """
    dmd_id = req.client_id or f"http_{domain}_{req.width}x{req.height}"
    geometry = PanelGeometry(
        width=req.width,
        height=req.height,
        poster_width=req.poster_width,
        poster_height=req.poster_height,
    )
    criteria = consume_once_orchestrator._parse_domain_criteria(
        domain,
        req.model_dump(),
        top_lang=req.lang,
    )
    await consume_once_orchestrator.register_client_domain(dmd_id, domain, geometry, criteria)
    ready_pkg = consume_once_orchestrator.ready_packages.get((dmd_id, domain))
    return {
        "dmd_id": dmd_id,
        "domain": domain,
        "ready": ready_pkg is not None,
        "status": "ready" if ready_pkg is not None else "preparing",
    }


@app.get("/api/v1/{domain}/next", tags=["Consume-Once Protocol"])
async def claim_next_consume_once_item(
    domain: DomainType,
    background_tasks: BackgroundTasks,
    dmd_id: str = Query(default="default_dmd", description="DMD client identifier"),
    client_id: Optional[str] = Query(default=None, description="Alias for dmd_id"),
    width: int = Query(default=128, ge=32, le=512),
    height: int = Query(default=32, ge=16, le=256),
    lang: str = Query(default="fr"),
) -> Dict[str, Any]:
    """
    Atomically claim the next prepared unseen item for `(dmd_id, domain)`:
    - If not ready yet: returns `{"ready": false, "status": "preparing"}` immediately (`0 ms` block).
    - If ready: marks the item consumed in `seen_history.json`, invalidates the ready slot,
      and returns the JSON Card + `stream_url` (`/api/v1/{domain}/stream/{item_id}.rgb565.stream`).
    """
    effective_id = (client_id or "").strip() or dmd_id
    key = (effective_id, domain)
    if key not in consume_once_orchestrator.client_configs:
        geometry = PanelGeometry(width=width, height=height)
        criteria = consume_once_orchestrator._parse_domain_criteria(domain, {"lang": lang}, top_lang=lang)
        await consume_once_orchestrator.register_client_domain(effective_id, domain, geometry, criteria)

    pkg = await consume_once_orchestrator.claim_next_package(effective_id, domain)
    if pkg is None:
        return {
            "ready": False,
            "status": "preparing",
            "dmd_id": effective_id,
            "domain": domain,
            "item": None,
        }

    stream_url = ""
    if pkg.card.stream_frames > 0 and pkg.unified_stream_path and pkg.unified_stream_path.exists():
        stream_url = f"/api/v1/{domain}/stream/{pkg.item_id}.rgb565.stream"
    else:
        background_tasks.add_task(consume_once_orchestrator.purge_consumed_package, pkg.item_id)

    card_dict = pkg.card.to_dict()
    return {
        "ready": True,
        "status": "ready",
        "dmd_id": effective_id,
        "domain": domain,
        "item_id": pkg.item_id,
        "card": card_dict,
        "item": card_dict,
        "stream_url": stream_url,
    }


@app.get("/api/v1/{domain}/poster/{item_id}.rgb565", tags=["Consume-Once Protocol"])
async def stream_consume_once_poster(
    domain: DomainType,
    item_id: str,
) -> Response:
    """Serve the binary RGB565 mini-jacket for `item_id` over HTTP/HTTPS."""
    pkg = consume_once_orchestrator.active_packages.get(item_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail=f"Poster for '{item_id}' not found or already purged.")
    poster_fname = str(pkg.files_map.get("poster_rgb565") or "")
    poster_path = (pkg.item_dir / poster_fname) if poster_fname else None
    if poster_path is None or not poster_path.exists():
        raise HTTPException(status_code=404, detail=f"Poster file for '{item_id}' is unavailable.")
    raw = poster_path.read_bytes()
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(raw)),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        },
    )


@app.get("/api/v1/{domain}/stream/{item_id}.rgb565.stream", tags=["Consume-Once Protocol"])
async def stream_consume_once_sequence(
    domain: DomainType,
    item_id: str,
    background_tasks: BackgroundTasks,
    purge: bool = Query(
        default=True,
        description="Purge package from disk immediately after HTTP response (pass purge=false when preloading in mqtt_udp mode)",
    ),
) -> Response:
    """
    Stream the unified `DMDS` binary sequence (`Jacket Panorama -> Marquee Panorama -> Snapshots -> Video`)
    for `item_id` and immediately purge the consumed files from disk once the HTTP response completes
    (unless `purge=false` is passed by an `mqtt_udp` client that purges on `stop_stream`).

    ### Binary HTTP `DMDS` Header (16 Bytes, Little-Endian `<4sBBHHHHH`)
    - `0..3` (`char[4]`): `"DMDS"` (`0x44 0x4D 0x44 0x53`)
    - `4` (`uint8`): `version` (`1`)
    - `5` (`uint8`): `fps` (`8..60`)
    - `6..7` (`uint16le`): `width` (panel width in pixels, e.g. `128`)
    - `8..9` (`uint16le`): `height` (panel height in pixels, e.g. `32`)
    - `10..11` (`uint16le`): `total_frames` (`1..65535`)
    - `12..13` (`uint16le`): `frame_dur_ms` (`round(1000 / fps)`)
    - `14..15` (`uint16le`): `reserved` (`0`)
    - `16..EOF`: Contiguous `total_frames * width * height * 2` bytes of Little-Endian RGB565 pixels.

    For real-time UDP `DMDF` datagram streaming (`10`-byte `<4sHBBH` header), see `/asyncapi.yaml`.
    """
    pkg = consume_once_orchestrator.active_packages.get(item_id)
    if pkg is None or not pkg.unified_stream_path or not pkg.unified_stream_path.exists():
        raise HTTPException(status_code=404, detail=f"Stream '{item_id}' not found or already consumed and purged.")

    raw_bytes = pkg.unified_stream_path.read_bytes()
    if purge:
        background_tasks.add_task(consume_once_orchestrator.purge_consumed_package, item_id)
    return Response(
        content=raw_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(raw_bytes)),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-DMD-Consume-Once": "1",
        },
        background=background_tasks if purge else None,
    )


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
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "[HTTP IN] --> POST /api/v1/%s/session from %s | client_id=%s, count=%d, lang=%s, %dx%d",
        domain,
        client_ip,
        req.client_id,
        req.count,
        req.lang,
        req.width,
        req.height,
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
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "[HTTP IN] --> GET /api/v1/%s/batch?session_id=%s from %s",
        domain,
        session_id,
        client_ip,
    )
    res = await batch_manager.get_batch_status(domain, session_id)
    if res is None:
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
    count: int = Query(default=1, ge=1, le=25),
    lang: str = Query(default="fr"),
    width: int = Query(default=128, ge=32, le=512),
    height: int = Query(default=32, ge=16, le=256),
    jacket_panorama: bool = Query(default=True),
    marquee_panorama: bool = Query(default=False),
    snapshot_panorama_count: int = Query(default=0, ge=0, le=5),
    image_count: Optional[int] = Query(default=None, ge=0, le=5),
    primary_source: str = Query(default="auto"),
    include_video: bool = Query(default=True),
    video_max_duration_s: float = Query(default=6.0, ge=1.5, le=60.0),
    video_fps: int = Query(default=15, ge=6, le=30),
    systems: Optional[str] = Query(default=None),
    genres: Optional[str] = Query(default=None),
    decade: Optional[str] = Query(default=None),
) -> BatchStatusResponse:
    eff_snaps = snapshot_panorama_count if snapshot_panorama_count > 0 else (image_count or 0)
    req = SessionRegisterRequest(
        client_id=client_id,
        count=count,
        lang=lang,
        width=width,
        height=height,
        jacket_panorama=jacket_panorama,
        marquee_panorama=marquee_panorama,
        snapshot_panorama_count=eff_snaps,
        image_count=eff_snaps,
        primary_source=primary_source,
        include_video=include_video,
        video_max_duration_s=video_max_duration_s,
        video_fps=video_fps,
        systems=[s.strip() for s in systems.split(",") if s.strip()] if systems else ["arcade", "neogeo", "snes", "megadrive"],
        genres=[g.strip() for g in genres.split(",") if g.strip()] if genres else [],
        decade=decade,
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
    max_duration_s: Optional[float] = Query(default=None, ge=0.5, le=60.0),
) -> Response:
    asset_path: Optional[Path] = batch_manager.resolve_asset_path(item_key, filename)
    if asset_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Asset '{filename}' for item '{item_key}' is not ready or has expired.",
        )

    headers = {
        "X-DMD-Media-Ready": "1",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    }

    if filename.endswith(".rgb565.stream"):
        raw_bytes = (
            truncate_rgb565_stream(asset_path, max_duration_s)
            if max_duration_s is not None
            else asset_path.read_bytes()
        )
        headers["Content-Length"] = str(len(raw_bytes))
        return StreamingResponse(
            _iter_aligned_chunks(raw_bytes, chunk_size=8192),
            media_type="application/octet-stream",
            headers=headers,
        )

    if filename.endswith(".gif"):
        raw_bytes = (
            truncate_gif_bytes(asset_path, max_duration_s)
            if max_duration_s is not None
            else asset_path.read_bytes()
        )
        headers["Content-Length"] = str(len(raw_bytes))
        return Response(content=raw_bytes, media_type="image/gif", headers=headers)

    if filename.endswith(".png"):
        return FileResponse(asset_path, media_type="image/png", headers=headers)

    return FileResponse(asset_path, media_type="application/octet-stream", headers=headers)


def run() -> None:
    """CLI entrypoint (`dmd-media-showcase`) supporting both HTTP (Home-Lab) and HTTPS (TLS / Reverse Proxy)."""
    import uvicorn

    ssl_kwargs: Dict[str, Any] = {}
    if settings.ssl_certfile and settings.ssl_keyfile:
        ssl_kwargs["ssl_certfile"] = settings.ssl_certfile
        ssl_kwargs["ssl_keyfile"] = settings.ssl_keyfile

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=settings.trust_proxy_headers,
        forwarded_allow_ips="*",
        **ssl_kwargs,
    )


if __name__ == "__main__":
    run()
