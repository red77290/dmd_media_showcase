"""
Stateless Consume-Once HTTP Polling & Streaming Routes (`app/infrastructure/http/routers/consume_once.py`).
"""

from __future__ import annotations

import asyncio
import struct
import time
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.domain.entities import PanelGeometry
from app.infrastructure.http.dependencies import get_orchestrator
from app.models import DomainType, SessionRegisterRequest

router = APIRouter()


@router.post("/api/v1/{domain}/config", tags=["Consume-Once Protocol"])
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
    co = get_orchestrator()
    dmd_id = req.client_id or f"http_{domain}_{req.width}x{req.height}"
    peer_ip = request.client.host if request.client else ""
    co.record_http_client(dmd_id, domain, peer_ip)
    geometry = PanelGeometry(
        width=req.width,
        height=req.height,
        poster_width=req.poster_width,
        poster_height=req.poster_height,
    )
    criteria = co._parse_domain_criteria(
        domain,
        req.model_dump(),
        top_lang=req.lang,
    )
    await co.register_client_domain(dmd_id, domain, geometry, criteria)
    ready_pkg = co.ready_packages.get((dmd_id, domain))
    return {
        "dmd_id": dmd_id,
        "domain": domain,
        "ready": ready_pkg is not None,
        "status": "ready" if ready_pkg is not None else "preparing",
    }


@router.get("/api/v1/{domain}/next", tags=["Consume-Once Protocol"])
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
    co = get_orchestrator()
    effective_id = (client_id or "").strip() or dmd_id
    peer_ip = request.client.host if request.client else ""
    co.record_http_client(effective_id, domain, peer_ip)
    key = (effective_id, domain)
    if key not in co.client_configs:
        geometry = PanelGeometry(width=width, height=height)
        criteria = co._parse_domain_criteria(domain, {"lang": lang}, top_lang=lang)
        await co.register_client_domain(effective_id, domain, geometry, criteria)

    pkg = await co.claim_next_package(effective_id, domain)
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
        background_tasks.add_task(co.purge_consumed_package, pkg.item_id)

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


@router.get("/api/v1/{domain}/poster/{item_id}.rgb565", tags=["Consume-Once Protocol"])
async def stream_consume_once_poster(
    domain: DomainType,
    item_id: str,
) -> Response:
    """Serve the binary RGB565 mini-jacket for `item_id` over HTTP/HTTPS."""
    pkg = get_orchestrator().active_packages.get(item_id)
    if pkg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Poster for '{item_id}' not found or already purged.",
        )
    poster_fname = str(pkg.files_map.get("poster_rgb565") or "")
    poster_path = (pkg.item_dir / poster_fname) if poster_fname else None
    if poster_path is None or not poster_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Poster file for '{item_id}' is unavailable.",
        )
    raw = poster_path.read_bytes()
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(raw)),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        },
    )


@router.get("/api/v1/{domain}/stream/{item_id}.rgb565.stream", tags=["Consume-Once Protocol"])
async def stream_consume_once_sequence(
    domain: DomainType,
    item_id: str,
    background_tasks: BackgroundTasks,
    purge: bool = Query(
        default=True,
        description="Purge package from disk immediately after HTTP response (pass purge=false when preloading in mqtt_udp mode)",
    ),
    paced: bool = Query(
        default=False,
        description="Pace DMDS frames in real time at header FPS so constrained embedded TCP clients never stall LwIP TCP_WND",
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
    co = get_orchestrator()
    pkg = co.active_packages.get(item_id)
    if pkg is None or not pkg.unified_stream_path or not pkg.unified_stream_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stream '{item_id}' not found or already consumed and purged.",
        )

    stream_path = pkg.unified_stream_path
    file_size = stream_path.stat().st_size
    if paced and file_size > 16:
        with stream_path.open("rb") as probe_fh:
            hdr = probe_fh.read(16)
        if len(hdr) == 16 and hdr[:4] == b"DMDS":
            _, _ver, hdr_fps, width, height, _total_frames, _frame_dur_ms, _res = struct.unpack(
                "<4sBBHHHHH", hdr
            )
            frame_bytes = max(2, int(width) * int(height) * 2)
            eff_fps = max(4, min(60, int(hdr_fps or pkg.card.stream_fps or 15)))
            claim_key = (pkg.dmd_id, domain)

            async def _paced_dmds_generator() -> AsyncIterator[bytes]:
                fh = stream_path.open("rb")
                try:
                    now_wall = time.time()
                    co.state.client_last_seen[claim_key] = now_wall
                    co.state.last_claimed_until[claim_key] = now_wall + 15.0
                    first_payload = fh.read(16 + frame_bytes)
                    if first_payload:
                        yield first_payload
                    t0 = time.monotonic()
                    frame_idx = 1
                    while True:
                        chunk = fh.read(frame_bytes)
                        if not chunk:
                            break
                        target_t = t0 + (frame_idx / float(eff_fps))
                        sleep_s = target_t - time.monotonic()
                        if sleep_s > 0.001:
                            await asyncio.sleep(sleep_s)
                        if (frame_idx % 15) == 0:
                            ts_now = time.time()
                            co.state.client_last_seen[claim_key] = ts_now
                            co.state.last_claimed_until[claim_key] = ts_now + 15.0
                        yield chunk
                        frame_idx += 1
                finally:
                    fh.close()
                    co.state.last_claimed_until.pop(claim_key, None)
                    if purge:
                        await co.purge_consumed_package(item_id)

            return StreamingResponse(
                _paced_dmds_generator(),
                media_type="application/octet-stream",
                headers={
                    "Content-Length": str(file_size),
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "X-DMD-Consume-Once": "1",
                    "X-DMD-Paced": "1",
                    "X-Accel-Buffering": "no",
                },
            )

    raw_bytes = stream_path.read_bytes()
    if purge:
        background_tasks.add_task(co.purge_consumed_package, item_id)
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
