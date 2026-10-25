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
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Set, Tuple

import dmd_gif_converter
from dmd_gif_converter import truncate_gif_bytes, truncate_rgb565_stream
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from app import __version__
from app.application.consume_once_orchestrator import (
    canonical_dmd_board_key,
    consume_once_orchestrator,
    strip_mqtt_gen_suffix,
)
from app.application.source_chain_service import source_chain_service
from app.asyncapi_spec import ASYNCAPI_YAML
from app.config import settings
from app.domain.entities import MediaCandidate, PanelGeometry
from app.infrastructure.mdns_responder import showcase_mdns_responder
from app.infrastructure.mqtt.showcase_mqtt_gateway import showcase_mqtt_gateway
from app.infrastructure.persistence import dmd_profile_store, seen_repository, server_config_store
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
    ClientControlRequest,
    ClientHelloPayload,
    DmdProfilePayload,
    DomainType,
    HealthResponse,
    MediaShowcaseItem,
    OnDemandRequest,
    QueueAddRequest,
    SearchCandidateModel,
    SearchCandidatesResponse,
    SearchRequestPayload,
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
        "name": "DMD Profiles & Passive Mode",
        "description": "Persistent DMD profiles (`dmd_profiles.json`), server-managed playlists (Modes 2A & 2B), `hello` announcements, and live playback controls.",
    },
    {
        "name": "On-Demand & Voice Assistant",
        "description": "Multi-candidate localized search (`/search`) and per-DMD On-Demand priority queues (`/clients/{dmd_id}/queue`, `/on-demand`) for WebUI, FrontendSync, and voice assistants.",
    },
    {
        "name": "Assets & Streaming",
        "description": "Binary asset streaming for mini-jackets (`.rgb565`, `.png`), panoramas, and video trailers.",
    },
]


def _apply_live_log_level(level_name: str) -> str:
    resolved_name = str(level_name or "INFO").strip().upper()
    lvl = getattr(logging, resolved_name, logging.INFO)
    logging.getLogger().setLevel(lvl)
    for name, lg in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(lg, logging.Logger) and name.startswith("dmd_showcase"):
            lg.setLevel(lvl)
    return logging.getLevelName(lvl)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    server_config_store.apply_to_settings()
    _apply_live_log_level(settings.log_level)
    logger.info(
        "[STARTUP] Starting DMD Media Showcase Server v%s (dmd_gif_converter v%s, max_workers=%d)",
        __version__,
        getattr(dmd_gif_converter, "__version__", "8.0.0"),
        settings.effective_max_workers,
    )
    batch_manager.start()
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


ALL_SHOWCASE_DOMAINS: Tuple[DomainType, ...] = ("games", "movies", "series", "anime")
_session_client_meta: Dict[Tuple[str, str], Tuple[str, float]] = {}


def _normalized_seen_stats() -> Dict[str, int]:
    """Always return all 4 canonical Showcase domains even when `seen_history.json` is empty."""
    raw = seen_repository.stats() or {}
    return {dom: int(raw.get(dom, 0)) for dom in ALL_SHOWCASE_DOMAINS}


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
    co = consume_once_orchestrator
    board_keys: Set[str] = set(co.distinct_board_keys())

    mqtt_tcp_sessions = 0
    if showcase_mqtt_gateway.embedded_broker is not None:
        for cid, mqtt_sess in list(showcase_mqtt_gateway.embedded_broker.clients.items()):
            if cid == "dmd_media_showcase_server":
                continue
            mqtt_tcp_sessions += 1
            base_id = strip_mqtt_gen_suffix(cid)
            best_ip = co.client_udp_ips.get(base_id) or mqtt_sess.peer_ip
            board_keys.add(canonical_dmd_board_key(base_id, best_ip))
    elif showcase_mqtt_gateway.external_client is not None:
        mqtt_tcp_sessions = 1

    non_tester_sessions = [
        s
        for s in batch_manager.sessions.values()
        if (s.request.client_id or s.session_id) != "webui_tester"
    ]
    slot_keys: Set[Tuple[str, str]] = {(dmd_id, dom) for (dmd_id, dom) in co.client_configs}
    for s in non_tester_sessions:
        cid = s.request.client_id or s.session_id
        slot_keys.add((cid, s.domain))
        meta = _session_client_meta.get((cid, s.domain))
        sess_ip = meta[0] if meta else ""
        board_keys.add(canonical_dmd_board_key(cid, sess_ip))

    distinct_dmds = len(board_keys)
    total_completed = batch_manager.completed_jobs_total + co.completed_jobs_total
    total_duration = batch_manager._total_job_duration_s + co.total_job_duration_s
    combined_avg_s = round(total_duration / total_completed, 2) if total_completed > 0 else 0.0
    active_udp = (
        co.streamer.active_streams_count
        if hasattr(co.streamer, "active_streams_count")
        else 0
    )
    ready_slots = co.ready_slots_count + sum(
        1 for s in non_tester_sessions if s.current_batch.is_ready
    )
    standby_slots = co.standby_slots_count + sum(
        1 for s in non_tester_sessions if s.next_batch is not None and s.next_batch.is_ready
    )
    legacy_playing = sum(
        1 for s in non_tester_sessions if s.current_batch.served_count > 0
    )
    active_broadcasts = max(active_udp, co.active_broadcasts_count + legacy_playing)

    return HealthResponse(
        status="ok",
        version=__version__,
        converter_version=getattr(dmd_gif_converter, "__version__", "8.0.0"),
        active_workers=min(
            settings.effective_max_workers,
            batch_manager.active_workers + co.active_workers,
        ),
        max_workers=settings.effective_max_workers,
        queue_depth=batch_manager.queue_depth + co.queue_depth,
        active_sessions=max(distinct_dmds, len(slot_keys)),
        completed_jobs_total=total_completed,
        avg_job_duration_s=combined_avg_s,
        providers_configured=scraper_registry.describe_active_chains(),
        providers_detailed=scraper_registry.describe_chains_detailed(),
        mqtt_mode=str(srv_cfg.get("mqtt_mode") or "embedded"),
        mqtt_port=mqtt_port,
        mqtt_connected_clients=distinct_dmds,
        mqtt_tcp_sessions=mqtt_tcp_sessions,
        registered_dmd_slots=len(slot_keys),
        ready_slots=ready_slots,
        standby_slots=standby_slots,
        active_udp_streams=active_udp,
        active_broadcasts=active_broadcasts,
        video_max_duration_limit_s=settings.video_max_duration_limit_s,
        video_max_fps_limit=settings.video_max_fps_limit,
        use_process_pool=settings.use_process_pool,
        seen_history_counts=_normalized_seen_stats(),
    )


@app.get("/api/v1/admin/config", tags=["System & Admin"])
async def get_admin_config() -> Dict[str, Any]:
    """
    Return sanitized server configuration (`server_config.json`), encrypted vault status,
    active scraper chains, connected DMD clients, and `seen_history.json` counts.
    Developer API keys are never exposed in plaintext.
    """
    now_ts = time.time()
    clients = consume_once_orchestrator.describe_connected_clients()
    existing_keys = {(c["dmd_id"], c["domain"]) for c in clients}
    for sess in batch_manager.sessions.values():
        cid = sess.request.client_id or sess.session_id
        if cid == "webui_tester" or (cid, sess.domain) in existing_keys:
            continue
        cur_items = sess.current_batch.items
        nxt_items = sess.next_batch.items if sess.next_batch else []
        meta = _session_client_meta.get((cid, sess.domain))
        sess_ip = meta[0] if meta and meta[0] not in ("unknown", "testclient") else ""
        last_seen_ago = int(max(0.0, now_ts - meta[1])) if meta else None
        cur_title = cur_items[0].title if cur_items else None
        is_served = sess.current_batch.served_count > 0 and cur_title is not None
        clients.append(
            {
                "dmd_id": cid,
                "domain": sess.domain,
                "transport": "HTTP",
                "client_ip": sess_ip,
                "width": sess.request.width,
                "height": sess.request.height,
                "lang": sess.request.lang,
                "video_mode": sess.request.video_mode,
                "color_boost": str(sess.request.color_boost),
                "video_max_duration_s": min(
                    float(sess.request.video_max_duration_s),
                    float(settings.video_max_duration_limit_s),
                ),
                "video_fps": min(
                    int(sess.request.video_fps),
                    int(settings.video_max_fps_limit),
                ),
                "systems": list(sess.request.systems),
                "genres": list(sess.request.genres),
                "decades": list(sess.request.decades),
                "active_title": cur_title,
                "playing_title": cur_title if is_served else None,
                "last_played_title": cur_title,
                "is_playing": is_served,
                "udp_streaming": False,
                "ready_title": cur_title if sess.current_batch.is_ready else None,
                "standby_title": nxt_items[0].title if nxt_items else None,
                "preparing": not sess.current_batch.is_ready or sess.next_batch_creating,
                "last_seen_ago_s": last_seen_ago,
            }
        )
    return {
        "config": server_config_store.get_public_admin_config(),
        "vault_status": server_config_store.get_vault_status(),
        "providers": scraper_registry.describe_chains_detailed(),
        "providers_summary": scraper_registry.describe_active_chains(),
        "seen_history": _normalized_seen_stats(),
        "clients": clients,
        "dmd_profiles": dmd_profile_store.list_profiles(),
    }


async def _apply_runtime_config_live(
    prev_cfg: Dict[str, Any],
    new_cfg: Dict[str, Any],
    touched_keys: Set[str],
) -> Dict[str, Any]:
    """
    Hot-apply runtime configuration changes immediately across all active services
    (worker pool, Consume-Once concurrency & slot limits, logger levels, MQTT broker, and mDNS)
    with zero server restart required.
    """
    applied_log_level = _apply_live_log_level(settings.log_level)

    if "cache_dir" in touched_keys:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)

    worker_changed = bool(
        touched_keys & {"max_concurrent_jobs", "use_process_pool", "cache_dir"}
        or prev_cfg.get("max_concurrent_jobs") != new_cfg.get("max_concurrent_jobs")
        or prev_cfg.get("use_process_pool") != new_cfg.get("use_process_pool")
    )
    if worker_changed and (batch_manager._running or batch_manager._worker_tasks):
        effective_workers = await batch_manager.reconfigure_workers()
    else:
        effective_workers = settings.effective_max_workers
    consume_once_orchestrator.reconfigure_concurrency(effective_workers)

    invalidated_slots = 0
    if touched_keys & {"video_max_duration_limit_s", "video_max_fps_limit"}:
        invalidated_slots = await consume_once_orchestrator.apply_runtime_limits()

    expired_sessions = 0
    if "session_ttl_seconds" in touched_keys:
        expired_sessions = batch_manager.cleanup_expired_sessions()

    mqtt_reloaded = False
    mqtt_changed = any(
        prev_cfg.get(k) != new_cfg.get(k)
        for k in ("mqtt_mode", "mqtt_port", "mqtt_external_url")
    )
    if mqtt_changed and showcase_mqtt_gateway._running:
        await showcase_mqtt_gateway.stop()
        await showcase_mqtt_gateway.start()
        mqtt_reloaded = True
    elif (
        prev_cfg.get("default_lang") != new_cfg.get("default_lang")
        and showcase_mqtt_gateway._running
    ):
        await showcase_mqtt_gateway.publish_catalog_referentials(
            lang=str(new_cfg.get("default_lang") or "fr")
        )

    mdns_reloaded = False
    mdns_changed = any(
        prev_cfg.get(k) != new_cfg.get(k) for k in ("mdns_enabled", "mdns_hostname")
    )
    if mdns_changed and (
        showcase_mdns_responder._transport is not None or showcase_mqtt_gateway._running
    ):
        await showcase_mdns_responder.stop()
        if settings.mdns_enabled:
            showcase_mdns_responder.hostname = settings.mdns_hostname
            await showcase_mdns_responder.start(port=settings.port)
        mdns_reloaded = True

    return {
        "effective_max_workers": effective_workers,
        "use_process_pool": settings.use_process_pool,
        "video_max_duration_limit_s": settings.video_max_duration_limit_s,
        "video_max_fps_limit": settings.video_max_fps_limit,
        "log_level": applied_log_level,
        "invalidated_slots": invalidated_slots,
        "expired_sessions": expired_sessions,
        "mqtt_reloaded": mqtt_reloaded,
        "mdns_reloaded": mdns_reloaded,
    }


@app.post("/api/v1/admin/config", tags=["System & Admin"])
async def update_admin_config(payload: AdminServerConfigPayload) -> Dict[str, Any]:
    """
    Update and immediately hot-apply centralized runtime settings, worker pool concurrency,
    video duration/FPS ceilings, MQTT/mDNS settings, and scraper credentials in `./data/server_config.json`.
    """
    prev_cfg = server_config_store.get_all()
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    updated = server_config_store.update(updates)
    new_cfg = server_config_store.get_all()
    applied_live = await _apply_runtime_config_live(prev_cfg, new_cfg, set(updates.keys()))
    return {
        "status": "saved",
        "config": updated,
        "applied_live": applied_live,
        "vault_status": server_config_store.get_vault_status(),
        "providers": scraper_registry.describe_chains_detailed(),
        "providers_summary": scraper_registry.describe_active_chains(),
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
        "seen_history": _normalized_seen_stats(),
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
    lang: str = Query(default="fr", description="Preferred UI/content language (`fr`, `en`, `es`, `de`, `it`, `pt`, `ja`)"),
) -> Dict[str, object]:
    """Return consolidated localized filter categories, relational system capabilities, genres, decades, and DDD sources."""
    filters = scraper_registry.available_filters(domain=domain, lang=lang)
    return {
        "domain": domain,
        "languages": ["fr", "en", "es", "de", "it", "pt", "ja"],
        "text_scroll_speeds": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "slow", "normal", "fast", "turbo"],
        "video_modes": ["auto_action", "panorama", "center_crop"],
        "color_boost_modes": ["auto", "pixel_art", "anime", "cinema"],
        "video_types": ["any", "opening", "ending", "pv", "trailer"],
        **filters,
    }


def _candidate_to_search_model(cand: MediaCandidate, fallback_lang: str) -> SearchCandidateModel:
    return SearchCandidateModel(
        candidate_id=cand.id,
        id=cand.id,
        domain=cand.domain,
        title=cand.title,
        year=cand.year,
        badge=cand.badge,
        badge_color=cand.badge_color,
        subtitle=cand.subtitle,
        rating=cand.rating,
        synopsis=cand.synopsis,
        lang=cand.lang or fallback_lang,
        source_id=cand.source_id,
        poster_url=cand.resolved_jacket_url(),
        has_video=bool(cand.video_url or cand.youtube_url),
    )


async def _execute_domain_search(
    domain: DomainType,
    query: str,
    lang: str,
    systems: list[str],
    genres: list[str],
    decades: list[str],
    video_type: str,
    limit: int,
) -> SearchCandidatesResponse:
    criteria = consume_once_orchestrator._parse_domain_criteria(
        domain,
        {
            "lang": lang,
            "systems": systems,
            "genres": genres,
            "decades": decades,
            "video_type": video_type,
        },
        top_lang=lang,
    )
    found = await source_chain_service.search_candidates(
        criteria=criteria,
        query=query,
        limit=limit,
    )
    items = [_candidate_to_search_model(c, criteria.lang) for c in found]
    return SearchCandidatesResponse(
        domain=domain,
        query=query,
        lang=criteria.lang,
        count=len(items),
        items=items,
    )


@app.get(
    "/api/v1/{domain}/search",
    response_model=SearchCandidatesResponse,
    tags=["Discovery", "On-Demand & Voice Assistant"],
)
async def search_domain_candidates_get(
    domain: DomainType,
    q: str = Query(default="", description="Optional text query (title, ROM name, franchise). Empty returns filtered browse candidates."),
    query: Optional[str] = Query(default=None, description="Optional alias for `q`."),
    lang: str = Query(default="fr", description="Content broadcast language (`fr`, `en`, `es`, `de`, `it`, `pt`, `ja`)."),
    systems: Optional[str] = Query(default=None, description="Comma-separated canonical systems (for `games`)."),
    genres: Optional[str] = Query(default=None, description="Comma-separated canonical genres."),
    decades: Optional[str] = Query(default=None, description="Comma-separated decades (e.g. `1990s,2000s`)."),
    video_type: str = Query(default="any", description="Preferred video type (`any`, `opening`, `ending`, `pv`, `trailer`)."),
    limit: int = Query(default=8, ge=1, le=20, description="Maximum number of candidates to return (`1..20`)."),
) -> SearchCandidatesResponse:
    """
    Search or browse multiple localized media candidates honoring `domain`, `lang`, `systems`, `genres`, `decades`,
    and optional text `q`. Returned candidates are staged in memory so selecting any `candidate_id` via
    `POST /api/v1/clients/{dmd_id}/queue` starts transcoding immediately without re-scraping.
    """
    eff_q = (query if query is not None else q).strip()
    sys_list = [s.strip() for s in systems.split(",") if s.strip()] if systems else []
    gen_list = [g.strip() for g in genres.split(",") if g.strip()] if genres else []
    dec_list = [d.strip() for d in decades.split(",") if d.strip()] if decades else []
    return await _execute_domain_search(
        domain=domain,
        query=eff_q,
        lang=lang,
        systems=sys_list,
        genres=gen_list,
        decades=dec_list,
        video_type=video_type,
        limit=limit,
    )


@app.post(
    "/api/v1/{domain}/search",
    response_model=SearchCandidatesResponse,
    tags=["Discovery", "On-Demand & Voice Assistant"],
)
async def search_domain_candidates_post(
    domain: DomainType,
    payload: SearchRequestPayload,
) -> SearchCandidatesResponse:
    """POST variant of `/api/v1/{domain}/search` accepting JSON filter arrays and content `lang`."""
    eff_q = (payload.query if payload.query is not None else payload.q).strip()
    return await _execute_domain_search(
        domain=domain,
        query=eff_q,
        lang=payload.lang,
        systems=list(payload.systems),
        genres=list(payload.genres),
        decades=list(payload.decades),
        video_type=payload.video_type,
        limit=payload.limit,
    )


@app.get("/api/v1/clients", tags=["DMD Profiles & Passive Mode"])
async def list_dmd_clients() -> Dict[str, Any]:
    """
    Return the consolidated list of all connected DMD clients, persisted `dmd_profiles.json` profiles,
    their active operating mode (`client` vs `server` 2A/2B), broadcast `lang`, and On-Demand queue depth.
    """
    admin_view = await get_admin_config()
    return {
        "clients": admin_view["clients"],
        "profiles": dmd_profile_store.list_profiles(),
        "queues": {
            dmd_id: consume_once_orchestrator.list_queue(dmd_id)
            for dmd_id in sorted(consume_once_orchestrator.priority_queues.keys())
            if consume_once_orchestrator.list_queue(dmd_id)
        },
    }


@app.post("/api/v1/clients/hello", tags=["DMD Profiles & Passive Mode"])
async def announce_passive_dmd_client(
    payload: ClientHelloPayload,
    request: Request,
) -> Dict[str, Any]:
    """
    Lightweight HTTP registration/heartbeat endpoint for passive ESP32/RPi receivers without MQTT.
    Auto-creates or updates hardware geometry and LAN IP in `dmd_profiles.json` without overwriting playlists.
    """
    peer_ip = request.client.host if request.client else ""
    prof = await consume_once_orchestrator.handle_client_hello(
        payload.dmd_id,
        payload.model_dump(),
        detected_ip=peer_ip,
    )
    return {
        "status": "ok",
        "dmd_id": payload.dmd_id,
        "profile": prof,
        "queue": consume_once_orchestrator.list_queue(payload.dmd_id),
    }


@app.get("/api/v1/clients/{dmd_id}/profile", tags=["DMD Profiles & Passive Mode"])
async def get_dmd_client_profile(dmd_id: str) -> Dict[str, Any]:
    """Retrieve the persisted `dmd_profiles.json` configuration for `dmd_id` (or synthesize a live template if connected)."""
    prof = dmd_profile_store.get_profile(dmd_id)
    if prof is not None:
        return {"persisted": True, "profile": prof}
    # Synthesize a pre-filled template if `dmd_id` is currently connected in Mode 1
    live_matches = [
        (dom, geom, crit)
        for (cid, dom), (geom, crit) in consume_once_orchestrator.client_configs.items()
        if cid == dmd_id
    ]
    if not live_matches:
        raise HTTPException(status_code=404, detail=f"DMD profile '{dmd_id}' not found.")
    _dom, geom, crit = live_matches[0]
    from app.infrastructure.persistence.dmd_profile_store import normalize_dmd_profile

    template = normalize_dmd_profile(
        dmd_id,
        {
            "dmd_id": dmd_id,
            "display_name": dmd_id,
            "config_source": "client",
            "supports_local_card": True,
            "always_stream_udp": False,
            "width": geom.width,
            "height": geom.height,
            "udp_ip": consume_once_orchestrator.client_udp_ips.get(dmd_id, ""),
            "udp_port": consume_once_orchestrator.client_udp_ports.get(dmd_id, 4210),
            "lang": crit.lang,
        },
    )
    return {"persisted": False, "profile": template}


@app.put("/api/v1/clients/{dmd_id}/profile", tags=["DMD Profiles & Passive Mode"])
async def put_dmd_client_profile(
    dmd_id: str,
    payload: DmdProfilePayload,
) -> Dict[str, Any]:
    """Create or update a DMD profile and multi-domain playlist in `./data/dmd_profiles.json` and hot-apply immediately."""
    raw = payload.model_dump(exclude_none=True)
    raw["dmd_id"] = dmd_id
    saved = await consume_once_orchestrator.upsert_dmd_profile(dmd_id, raw)
    return {"status": "saved", "dmd_id": dmd_id, "profile": saved}


@app.delete("/api/v1/clients/{dmd_id}/profile", tags=["DMD Profiles & Passive Mode"])
async def delete_dmd_client_profile(dmd_id: str) -> Dict[str, Any]:
    """Delete a persisted DMD profile from `./data/dmd_profiles.json` and stop any associated passive loop."""
    deleted = await consume_once_orchestrator.delete_dmd_profile(dmd_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"DMD profile '{dmd_id}' not found.")
    return {"status": "deleted", "dmd_id": dmd_id}


@app.get("/api/v1/clients/{dmd_id}/queue", tags=["On-Demand & Voice Assistant"])
async def get_dmd_priority_queue(dmd_id: str) -> Dict[str, Any]:
    """Inspect the active On-Demand priority queue (`queued`, `preparing`, `ready`, `playing`) for `dmd_id`."""
    items = consume_once_orchestrator.list_queue(dmd_id)
    return {"dmd_id": dmd_id, "count": len(items), "items": items}


@app.post("/api/v1/clients/{dmd_id}/queue", tags=["On-Demand & Voice Assistant"])
async def add_to_dmd_priority_queue(
    dmd_id: str,
    req: QueueAddRequest,
) -> Dict[str, Any]:
    """
    Add a media item to `dmd_id`'s On-Demand priority queue (`mode='enqueue_next'`) or play it immediately
    (`mode='play_now'`). Supports both:
    - Exact selection via `candidate_id` from `/api/v1/{domain}/search`, and
    - Direct 1-shot lookup via `{domain, query, systems, lang, mark_seen}` (ready for `FrontendSync` & webhooks).
    """
    try:
        item = await consume_once_orchestrator.enqueue_on_demand(dmd_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "queued",
        "dmd_id": dmd_id,
        "item": item,
        "queue": consume_once_orchestrator.list_queue(dmd_id),
    }


@app.delete("/api/v1/clients/{dmd_id}/queue/{queue_id}", tags=["On-Demand & Voice Assistant"])
async def remove_from_dmd_priority_queue(
    dmd_id: str,
    queue_id: str,
) -> Dict[str, Any]:
    """Remove an item from `dmd_id`'s On-Demand priority queue and purge any unconsumed temporary files."""
    removed = await consume_once_orchestrator.remove_queue_item(dmd_id, queue_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Queue item '{queue_id}' not found on '{dmd_id}'.")
    return {
        "status": "removed",
        "dmd_id": dmd_id,
        "queue_id": queue_id,
        "queue": consume_once_orchestrator.list_queue(dmd_id),
    }


@app.post("/api/v1/clients/{dmd_id}/control", tags=["DMD Profiles & Passive Mode"])
async def control_dmd_client(
    dmd_id: str,
    req: ClientControlRequest,
) -> Dict[str, Any]:
    """Send a real-time playback control (`skip`, `pause`, `resume`, `stop`) to `dmd_id`."""
    res = await consume_once_orchestrator.control_client(dmd_id, req.action)
    return {"status": "ok", **res}


@app.post("/api/v1/{domain}/config", tags=["Consume-Once Protocol"])
async def register_consume_once_config(
    domain: DomainType,
    req: SessionRegisterRequest,
    request: Request,
) -> Dict[str, Any]:
    """
    HTTP equivalent of publishing `dmd_showcase/clients/{dmd_id}/config` (`retain=True`).
    Registers the DMD client's panel geometry and filter criteria and triggers background
    preparation of 1 unseen `PreparedShowcasePackage`.
    """
    dmd_id = req.client_id or f"http_{domain}_{req.width}x{req.height}"
    peer_ip = request.client.host if request.client else ""
    consume_once_orchestrator.record_http_client(dmd_id, domain, peer_ip)
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
    request: Request,
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
    peer_ip = request.client.host if request.client else ""
    consume_once_orchestrator.record_http_client(effective_id, domain, peer_ip)
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
    ses_id = batch_manager._make_session_id(domain, req)
    existed_before = ses_id in batch_manager.sessions
    t0 = time.monotonic()
    reg = await batch_manager.register_session(domain, req)
    cid = req.client_id or reg.session_id
    if cid != "webui_tester":
        _session_client_meta[(cid, domain)] = (client_ip, time.time())
    if not req.include_video and not existed_before and reg.ready_count > 0:
        consume_once_orchestrator.record_completed_job(time.monotonic() - t0, count=reg.ready_count)
    return reg


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
    sess_state = batch_manager.sessions.get(session_id)
    if sess_state is not None:
        cid = sess_state.request.client_id or session_id
        if cid != "webui_tester":
            _session_client_meta[(cid, domain)] = (client_ip, time.time())
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
    video_max_duration_s: float = Query(default=6.0, ge=1.5, le=120.0),
    video_fps: int = Query(default=15, ge=6, le=60),
    systems: Optional[str] = Query(default=None),
    genres: Optional[str] = Query(default=None),
    decade: Optional[str] = Query(default=None),
) -> BatchStatusResponse:
    client_ip = request.client.host if request.client else "unknown"
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
    if client_id != "webui_tester":
        _session_client_meta[(client_id, domain)] = (client_ip, time.time())
    ses_id = batch_manager._make_session_id(domain, req)
    if ses_id in batch_manager.sessions:
        existing = await batch_manager.get_batch_status(domain, ses_id)
        if existing is not None:
            return existing
    t0 = time.monotonic()
    reg = await batch_manager.register_session(domain, req)
    if not include_video and reg.ready_count > 0:
        consume_once_orchestrator.record_completed_job(time.monotonic() - t0, count=reg.ready_count)
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
    max_duration_s: Optional[float] = Query(default=None, ge=0.5, le=120.0),
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

    is_video_asset = "trailer" in filename or "video" in filename
    eff_max_dur: Optional[float] = None
    if max_duration_s is not None:
        eff_max_dur = min(float(max_duration_s), float(settings.video_max_duration_limit_s))
    elif is_video_asset:
        eff_max_dur = float(settings.video_max_duration_limit_s)

    if filename.endswith(".rgb565.stream"):
        raw_bytes = (
            truncate_rgb565_stream(asset_path, eff_max_dur)
            if eff_max_dur is not None
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
            truncate_gif_bytes(asset_path, eff_max_dur)
            if eff_max_dur is not None
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

    target_app: Any = app if getattr(sys, "frozen", False) else "app.main:app"
    uvicorn.run(
        target_app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=settings.trust_proxy_headers,
        forwarded_allow_ips="*",
        **ssl_kwargs,
    )


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    run()
