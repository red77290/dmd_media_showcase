"""
Stateless Consume-Once HTTP Polling & Binary Streaming Routes (`app/infrastructure/http/routers/consume_once.py`).
"""

from __future__ import annotations

import asyncio
from concurrent.futures.process import BrokenProcessPool
import logging
from pathlib import Path
import time
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.config import settings
from app.domain.entities import PanelGeometry
from app.infrastructure.http.dependencies import get_orchestrator
from app.infrastructure.rendering.dmdz_codec import (
    DMDZ_CODEC_ID,
    dmdz_sibling_path,
    ensure_dmdz_sibling,
)
from app.models import DomainType, SessionRegisterRequest
from app.scheduler.asset_builder import encode_dmdz_sibling_worker_task
from app.scheduler.worker_pool import batch_manager

router = APIRouter()
logger = logging.getLogger("dmd_showcase.http.stream")


async def _resolve_dmdz_sibling_nonblocking(raw_stream_path: Path) -> Optional[Path]:
    """
    Return an up-to-date lossless `.dmdz2.stream` sibling without blocking `uvicorn`'s main-process GIL.
    """
    try:
        dest = dmdz_sibling_path(raw_stream_path)
        if dest.exists() and dest.stat().st_mtime >= raw_stream_path.stat().st_mtime:
            return dest
    except OSError:
        return None

    loop = asyncio.get_running_loop()
    exec_pool = batch_manager.shared_executor()
    try:
        out_str = await loop.run_in_executor(
            exec_pool,
            encode_dmdz_sibling_worker_task,
            str(raw_stream_path),
            settings.worker_nice_increment,
        )
        return Path(out_str) if out_str else None
    except BrokenProcessPool:
        return ensure_dmdz_sibling(raw_stream_path)


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
    co = get_orchestrator()
    pkg = co.active_packages.get(item_id)
    if pkg is None:
        for (slot_dmd, slot_dom), rpkg in list(co.ready_packages.items()):
            if rpkg.item_id == item_id:
                pkg = await co.claim_next_package(slot_dmd, slot_dom, item_id=item_id)
                break
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
    purge: bool = Query(
        default=True,
        description="Purge package from disk immediately after HTTP TCP response completes (pass purge=false when preloading before an explicit MQTT stop_stream or HTTP DELETE purge).",
    ),
    codec: str = Query(
        default="",
        description="Optional lossless `DMDZ` v2 codec (`dmdz2` serves `.dmdz2.stream` with `X-DMD-Codec: dmdz2`; omit `codec` for raw `DMDS` `.rgb565.stream`).",
    ),
) -> Response:
    """
    Serve the unified lossless 16-bit True-RGB565 binary sequence
    (`Jacket Panorama -> Marquee Panorama -> Snapshots -> Video`) over plain HTTP `1.1` with
    deterministic `Content-Length`:
    - **`esp32dev` (`0 KB` PSRAM)**: Downloads `?purge=false&codec=dmdz2` onto its `25 MHz` SD card
      (`/cache/showcase_live.dmdz`) during the Info Card dwell, closes TCP (`0` Wi-Fi traffic during
      playback), and decodes locally from SD into its Zero-Copy `SRAM1` Canvas.
    - **`ESP32-S3` (Octal PSRAM)**: Preloads `?purge=false&codec=dmdz2` (`<= 4 MB`) directly into PSRAM
      during the Info Card (`0` Wi-Fi traffic during playback), or streams oversized `256x64` HD clips
      into a `1 MB` PSRAM SPSC ring buffer.
    - **`ArcadeMatrix_RPi` (Linux RAM)**: Preloads `?purge=false&codec=dmdz2` (or raw `DMDS` when `codec`
      is omitted) directly into RAM during the Info Card.

    ### Binary Lossless `DMDZ` v2 Container (`codec=dmdz2` — `<4sBBHHHHH` 16-Byte Header)
    - `0..3`: `"DMDZ"`, `4`: `version` (`2`), `5`: `fps`, `6..7`: `width`, `8..9`: `height`,
      `10..11`: `total_frames`, `12..13`: `frame_dur_ms`, `14..15`: `keyframe_interval`.
    - `16..EOF`: Per frame `<H` `payload_len` + lossless frame ops (`0=delta`, `1=scroll(dy)`,
      `2=keyframe`, `3=scroll_2d(dx,dy)`, `4=raw`).

    ### Binary Raw `DMDS` Container (`codec` omitted — `<4sBBHHHHH` 16 Bytes + RGB565LE Frames)
    - `0..3`: `"DMDS"`, `4`: `1`, `5`: `fps`, `6..7`: `width`, `8..9`: `height`, `10..11`: `total_frames`,
      `12..13`: `frame_dur_ms`, `14..15`: `0`.
    """
    co = get_orchestrator()
    pkg = co.active_packages.get(item_id)
    if pkg is None:
        for (slot_dmd, slot_dom), rpkg in list(co.ready_packages.items()):
            if rpkg.item_id == item_id:
                pkg = await co.claim_next_package(slot_dmd, slot_dom, item_id=item_id)
                break
    if pkg is None or not pkg.unified_stream_path or not pkg.unified_stream_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stream '{item_id}' not found or already consumed and purged.",
        )

    claim_slot = (pkg.dmd_id, domain)
    if claim_slot in co.state.client_configs and co.state.client_transports.get(claim_slot) != "HTTP":
        co.state.client_transports[claim_slot] = "MQTT+TCP"
    co.state.client_last_seen[claim_slot] = time.time()

    stream_path = pkg.unified_stream_path
    file_size = stream_path.stat().st_size
    norm_codec = codec.strip().lower()
    if norm_codec == DMDZ_CODEC_ID and file_size > 16:
        dmdz_path = await _resolve_dmdz_sibling_nonblocking(stream_path)
        if dmdz_path is not None and dmdz_path.exists():
            stream_path = dmdz_path
            file_size = dmdz_path.stat().st_size

    async def _stream_file_generator() -> AsyncIterator[bytes]:
        fh = stream_path.open("rb")
        started = time.perf_counter()
        sent_bytes = 0
        try:
            while True:
                block = fh.read(65536)
                if not block:
                    break
                sent_bytes += len(block)
                yield block
        finally:
            fh.close()
            logger.info(
                "[STREAM] %s '%s' -> %s: bytes=%d/%d dur=%.2fs purge=%s",
                norm_codec or "dmds",
                item_id,
                pkg.dmd_id,
                sent_bytes,
                file_size,
                time.perf_counter() - started,
                purge,
            )
            if purge:
                await co.purge_consumed_package(item_id)

    headers: Dict[str, str] = {
        "Content-Length": str(file_size),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "X-DMD-Consume-Once": "1",
    }
    if stream_path != pkg.unified_stream_path and norm_codec == DMDZ_CODEC_ID:
        headers["X-DMD-Codec"] = DMDZ_CODEC_ID

    return StreamingResponse(
        _stream_file_generator(),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.delete("/api/v1/{domain}/stream/{item_id}.rgb565.stream", tags=["Consume-Once Protocol"])
async def purge_consume_once_sequence(
    domain: DomainType,
    item_id: str,
) -> Dict[str, str]:
    """
    Idempotently purge a consumed package that was preloaded with `purge=false`
    (HTTP equivalent of publishing MQTT `{"action": "stop_stream", "purge": true}`).
    """
    co = get_orchestrator()
    await co.purge_consumed_package(item_id)
    return {
        "status": "purged",
        "domain": domain,
        "item_id": item_id,
    }
