"""
Session/Batch Rotation & Binary Asset Streaming Routes (`app/infrastructure/http/routers/rotation_batch.py`).
"""

from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Optional

from dmd_gif_converter import truncate_gif_bytes, truncate_rgb565_stream
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.config import settings
from app.infrastructure.http.dependencies import (
    get_batch_manager,
    get_orchestrator,
    get_session_client_meta,
)
from app.models import (
    BatchStatusResponse,
    DomainType,
    MediaShowcaseItem,
    OnDemandRequest,
    SessionRegisterRequest,
    SessionRegisterResponse,
)

logger = logging.getLogger("dmd_showcase.http")

router = APIRouter()


@router.post(
    "/api/v1/{domain}/session",
    response_model=SessionRegisterResponse,
    tags=["DMD Rotation Protocol"],
)
async def register_domain_session(
    domain: DomainType,
    req: SessionRegisterRequest,
    request: Request,
) -> SessionRegisterResponse:
    bm = get_batch_manager()
    co = get_orchestrator()
    meta_map = get_session_client_meta()

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
    ses_id = bm._make_session_id(domain, req)
    existed_before = ses_id in bm.sessions
    t0 = time.monotonic()
    reg = await bm.register_session(domain, req)
    cid = req.client_id or reg.session_id
    if cid != "webui_tester":
        meta_map[(cid, domain)] = (client_ip, time.time())
    if not req.include_video and not existed_before and reg.ready_count > 0:
        co.record_completed_job(time.monotonic() - t0, count=reg.ready_count)
    return reg


@router.get(
    "/api/v1/{domain}/batch",
    response_model=BatchStatusResponse,
    tags=["DMD Rotation Protocol"],
)
async def get_domain_batch(
    domain: DomainType,
    request: Request,
    session_id: str = Query(..., description="Session ID returned by POST /api/v1/{domain}/session"),
) -> BatchStatusResponse:
    bm = get_batch_manager()
    meta_map = get_session_client_meta()

    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "[HTTP IN] --> GET /api/v1/%s/batch?session_id=%s from %s",
        domain,
        session_id,
        client_ip,
    )
    res = await bm.get_batch_status(domain, session_id)
    if res is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown or expired session_id '{session_id}' for domain '{domain}'.",
        )
    sess_state = bm.sessions.get(session_id)
    if sess_state is not None:
        cid = sess_state.request.client_id or session_id
        if cid != "webui_tester":
            meta_map[(cid, domain)] = (client_ip, time.time())
    return res


@router.get(
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
    bm = get_batch_manager()
    co = get_orchestrator()
    meta_map = get_session_client_meta()

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
        systems=[s.strip() for s in systems.split(",") if s.strip()]
        if systems
        else ["arcade", "neogeo", "snes", "megadrive"],
        genres=[g.strip() for g in genres.split(",") if g.strip()] if genres else [],
        decade=decade,
    )
    if client_id != "webui_tester":
        meta_map[(client_id, domain)] = (client_ip, time.time())
    ses_id = bm._make_session_id(domain, req)
    if ses_id in bm.sessions:
        existing = await bm.get_batch_status(domain, ses_id)
        if existing is not None:
            return existing
    t0 = time.monotonic()
    reg = await bm.register_session(domain, req)
    if not include_video and reg.ready_count > 0:
        co.record_completed_job(time.monotonic() - t0, count=reg.ready_count)
    status = await bm.get_batch_status(domain, reg.session_id)
    assert status is not None
    return status


@router.post(
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
        return await get_batch_manager().prepare_on_demand(req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _iter_aligned_chunks(data: bytes, chunk_size: int = 8192):
    for offset in range(0, len(data), chunk_size):
        yield data[offset : offset + chunk_size]


@router.get("/api/v1/assets/{item_key}/{filename}", tags=["Assets & Streaming"])
async def stream_media_asset(
    item_key: str,
    filename: str,
    request: Request,
    max_duration_s: Optional[float] = Query(default=None, ge=0.5, le=120.0),
) -> Response:
    asset_path: Optional[Path] = get_batch_manager().resolve_asset_path(item_key, filename)
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
