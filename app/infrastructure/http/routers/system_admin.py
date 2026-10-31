"""
System, Health, Configuration & AsyncAPI Routes (`app/infrastructure/http/routers/system_admin.py`).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import urlparse

import dmd_gif_converter
from fastapi import APIRouter, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from app import __version__
from app.asyncapi_spec import ASYNCAPI_YAML
from app.config import settings
from app.domain.board_identity import canonical_dmd_board_key, strip_mqtt_gen_suffix
from app.infrastructure.http.dependencies import (
    build_connected_clients_snapshot,
    get_batch_manager,
    get_http_client_factory,
    get_mqtt_gateway,
    get_on_demand_cache,
    get_orchestrator,
    get_profile_store,
    get_scraper_registry,
    get_seen_repo,
    get_server_config_store,
    get_session_client_meta,
    get_update_checker,
    normalized_seen_stats,
)
from app.infrastructure.http.runtime_reloader import apply_runtime_config_live
from app.infrastructure.scrapers.referential_store import (
    anilist_referential_strategy,
    arcadeitalia_referential_strategy,
    jikan_referential_strategy,
    libretro_wikidata_referential_strategy,
    tvmaze_referential_strategy,
)
from app.infrastructure.web.dashboard_loader import load_admin_dashboard_html
from app.models import (
    AdminServerConfigPayload,
    DomainType,
    HealthResponse,
    UpdateCheckResponse,
)

router = APIRouter()

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#0e101c" stroke="#38bdf8" stroke-width="2"/>'
    '<circle cx="9" cy="11" r="2.2" fill="#ffa800"/>'
    '<circle cx="16" cy="11" r="2.2" fill="#38bdf8"/>'
    '<circle cx="23" cy="11" r="2.2" fill="#f43f5e"/>'
    '<polygon points="12,15 12,23 21,19" fill="#ffc420"/>'
    "</svg>"
)


@router.get("/favicon.ico", include_in_schema=False)
async def web_favicon() -> Response:
    """Serve a compact HUB75 LED matrix favicon so browsers do not log 404 Not Found."""
    return Response(
        content=_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def web_admin_dashboard() -> HTMLResponse:
    """Serve the centralized Web Administration Dashboard (`http://dmdmediashowcase.local:8890/`)."""
    return HTMLResponse(content=load_admin_dashboard_html())


@router.get("/swagger", include_in_schema=False)
async def swagger_alias(request: Request) -> HTMLResponse:
    """Alias route serving the interactive Swagger UI at `/swagger`."""
    fastapi_app = request.app
    return get_swagger_ui_html(
        openapi_url=fastapi_app.openapi_url or "/openapi.json",
        title=f"{fastapi_app.title} - Swagger UI",
    )


@router.get("/asyncapi.yaml", include_in_schema=False)
@router.get(
    "/api/v1/asyncapi.yaml",
    tags=["System & Admin"],
    summary="Get AsyncAPI 2.6 Specification (MQTT 3.1.1, Credit-Acked WebSocket DMDZ & UDP DMDF Streaming)",
    responses={
        200: {
            "content": {"application/yaml": {"schema": {"type": "string"}}},
            "description": "AsyncAPI 2.6.0 YAML specification for MQTT `:1883` topics, credit-acked WebSocket `DMDZ` (`/api/v1/{domain}/ws/{item_id}.dmdz`), and UDP `DMDF` `<4sHBBH` frame datagrams.",
        }
    },
)
async def get_asyncapi_spec() -> Response:
    """
    Serve the authoritative AsyncAPI 2.6.0 specification describing:
    - MQTT 3.1.1 retained & event channels (`status`, `referential`, `config`, `ready`, `events`, `stream_state`)
    - Credit-acknowledged `DMDZ` (`v1`/`v2`) WebSocket streaming (`WS /api/v1/{domain}/ws/{item_id}.dmdz`, `profile=sram|psram|psram_hd`, coalesced `16 B` header + Frame `0`, `<= 1,400 B` `FRAME_PAL8` SRAM cap + `'A'` credit ACKs)
    - Real-time UDP `DMDF` binary frame chunk datagrams (`10`-byte Little-Endian `<4sHBBH` header + `<= 1408B` RGB565LE)
    """
    return Response(content=ASYNCAPI_YAML, media_type="application/yaml; charset=utf-8")


@router.get("/health", response_model=HealthResponse, include_in_schema=False)
@router.get("/api/v1/health", response_model=HealthResponse, tags=["System & Admin"])
async def get_health(request: Request) -> HealthResponse:
    """Return server health, worker pool saturation, MQTT broker mode, and active scraper chains."""
    bm = get_batch_manager()
    co = get_orchestrator()
    mqtt_gw = get_mqtt_gateway()
    srv_store = get_server_config_store()
    reg = get_scraper_registry()
    meta_map = get_session_client_meta()

    bm.start()
    srv_cfg = srv_store.get_all()
    mqtt_mode_eff = str(srv_cfg.get("mqtt_mode") or "embedded")
    ext_url = str(srv_cfg.get("mqtt_external_url") or "").strip()
    req_host_hdr = str(request.headers.get("host") or "").strip()
    fallback_host = req_host_hdr.split(":")[0] if req_host_hdr else ""
    if mqtt_gw.external_client is not None or (mqtt_mode_eff == "external" and ext_url):
        parsed_mqtt = urlparse(ext_url if "://" in ext_url else f"mqtt://{ext_url}")
        mqtt_mode_eff = "external"
        mqtt_host = parsed_mqtt.hostname or fallback_host
        mqtt_port = int(parsed_mqtt.port or 1883)
    else:
        mqtt_mode_eff = "embedded"
        mqtt_host = fallback_host
        mqtt_port = (
            mqtt_gw.embedded_broker.port
            if mqtt_gw.embedded_broker
            else int(srv_cfg.get("mqtt_port") or 1883)
        )
    board_keys: Set[str] = set(co.distinct_board_keys())

    mqtt_tcp_sessions = 0
    if mqtt_gw.embedded_broker is not None:
        for cid, mqtt_sess in list(mqtt_gw.embedded_broker.clients.items()):
            if cid == "dmd_media_showcase_server":
                continue
            mqtt_tcp_sessions += 1
            base_id = strip_mqtt_gen_suffix(cid)
            best_ip = co.client_udp_ips.get(base_id) or mqtt_sess.peer_ip
            board_keys.add(canonical_dmd_board_key(base_id, best_ip))
    elif mqtt_gw.external_client is not None:
        mqtt_tcp_sessions = 1

    non_tester_sessions = [
        s
        for s in bm.sessions.values()
        if (s.request.client_id or s.session_id) != "webui_tester"
    ]
    slot_keys: Set[Tuple[str, str]] = {(dmd_id, dom) for (dmd_id, dom) in co.client_configs}
    for s in non_tester_sessions:
        cid = s.request.client_id or s.session_id
        slot_keys.add((cid, s.domain))
        meta = meta_map.get((cid, s.domain))
        sess_ip = meta[0] if meta else ""
        board_keys.add(canonical_dmd_board_key(cid, sess_ip))

    distinct_dmds = len(board_keys)
    total_completed = bm.completed_jobs_total + co.completed_jobs_total
    total_duration = bm._total_job_duration_s + co.total_job_duration_s
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
            bm.active_workers + co.active_workers,
        ),
        max_workers=settings.effective_max_workers,
        queue_depth=bm.queue_depth + co.queue_depth,
        active_sessions=max(distinct_dmds, len(slot_keys)),
        completed_jobs_total=total_completed,
        avg_job_duration_s=combined_avg_s,
        providers_configured=reg.describe_active_chains(),
        providers_detailed=reg.describe_chains_detailed(),
        mqtt_mode=mqtt_mode_eff,
        mqtt_host=mqtt_host,
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
        seen_history_counts=normalized_seen_stats(),
    )


@router.get(
    "/api/v1/system/update-check",
    response_model=UpdateCheckResponse,
    tags=["System & Admin"],
)
async def check_system_update(
    force: bool = Query(
        default=False,
        description="Bypass the 1-hour in-memory cache and re-query GitHub Releases immediately.",
    ),
) -> UpdateCheckResponse:
    """
    Check whether a newer official GitHub Release of `dmd_media_showcase` is available for the
    current runtime operating system (`macos`, `windows`, `linux`, or `docker`), returning the
    release page URL and matching OS binary download link when available.
    """
    res = await get_update_checker().check_for_update(force=force)
    if isinstance(res, UpdateCheckResponse):
        return res
    return UpdateCheckResponse(**res.to_dict())


@router.get("/api/v1/admin/config", tags=["System & Admin"])
async def get_admin_config() -> Dict[str, Any]:
    """
    Return sanitized server configuration (`server_config.json`), encrypted vault status,
    active scraper chains, connected DMD clients, and `seen_history.json` counts.
    Developer API keys are never exposed in plaintext.
    """
    srv_store = get_server_config_store()
    reg = get_scraper_registry()
    prof_store = get_profile_store()
    return {
        "config": srv_store.get_public_admin_config(),
        "vault_status": srv_store.get_vault_status(),
        "providers": reg.describe_chains_detailed(),
        "providers_summary": reg.describe_active_chains(),
        "seen_history": normalized_seen_stats(),
        "on_demand_cache": get_on_demand_cache().get_stats(),
        "clients": build_connected_clients_snapshot(),
        "dmd_profiles": prof_store.list_profiles(),
    }


@router.post("/api/v1/admin/config", tags=["System & Admin"])
async def update_admin_config(payload: AdminServerConfigPayload) -> Dict[str, Any]:
    """
    Update and immediately hot-apply centralized runtime settings, worker pool concurrency,
    video duration/FPS ceilings, MQTT/mDNS settings, and scraper credentials in `./data/server_config.json`.
    """
    srv_store = get_server_config_store()
    reg = get_scraper_registry()
    od_cache = get_on_demand_cache()
    prev_cfg = srv_store.get_all()
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    updated = srv_store.update(updates)
    new_cfg = srv_store.get_all()
    if "on_demand_cache_enabled" in updates or "on_demand_cache_max_mb" in updates:
        od_cache.update_settings(
            enabled=bool(new_cfg.get("on_demand_cache_enabled", False)),
            max_mb=int(new_cfg.get("on_demand_cache_max_mb") or 0),
        )
    applied_live = await apply_runtime_config_live(prev_cfg, new_cfg, set(updates.keys()))
    return {
        "status": "saved",
        "config": updated,
        "applied_live": applied_live,
        "on_demand_cache": od_cache.get_stats(),
        "vault_status": srv_store.get_vault_status(),
        "providers": reg.describe_chains_detailed(),
        "providers_summary": reg.describe_active_chains(),
    }


@router.post("/api/v1/admin/history/reset", tags=["System & Admin"])
async def reset_seen_history(
    domain: Optional[DomainType] = Query(
        default=None,
        description="Optional domain to reset (or all if omitted)",
    ),
) -> Dict[str, Any]:
    """Reset `seen_history.json` for a single domain or all domains."""
    cleared = get_seen_repo().reset(domain)
    return {
        "status": "reset",
        "domain": domain or "all",
        "cleared_count": cleared,
        "seen_history": normalized_seen_stats(),
    }


@router.post("/api/v1/admin/referentials/sync", tags=["System & Admin"])
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
    client_factory = get_http_client_factory()
    async with client_factory(timeout=6.0) as client:
        for strat in strategies:
            ref = await strat.sync_referential(client, force=True)
            summary[strat.source_id] = len(ref.get("tokens") or [])
    default_lang = str(get_server_config_store().get_all().get("default_lang") or "fr")
    await get_mqtt_gateway().publish_catalog_referentials(lang=default_lang)
    return {"status": "synced", "referential_counts": summary}
