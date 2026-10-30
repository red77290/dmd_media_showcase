"""
Stateless Consume-Once HTTP Polling & Streaming Routes (`app/infrastructure/http/routers/consume_once.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.domain.entities import PanelGeometry
from app.infrastructure.http.dependencies import get_orchestrator
from app.infrastructure.rendering.dmdz_codec import DMDZ_CODEC_ID, DMDZ_MAGIC, ensure_dmdz_sibling
from app.models import DomainType, SessionRegisterRequest

router = APIRouter()
logger = logging.getLogger("dmd_showcase.http.stream")

# Compact DMDZ frames (< 1.5 KB) may lead the real-time clock slightly so the client's ~5.7 KB
# TCP window acts as a jitter buffer; larger video frames (> 1.5 KB) pace strictly 1-per-period
# so NAT proxies never burst multiple multi-KB frames into a constrained ESP32 Wi-Fi RX queue.
DMDZ_SEND_LEAD_S = 0.06
DMDZ_LARGE_FRAME_BYTES = 1536
DMDZ_SUBPACKET_BYTES = 1024
DMDZ_WS_INITIAL_CREDITS = 2
DMDZ_WS_ACK_TIMEOUT_S = 4.0


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
    background_tasks: BackgroundTasks,
    purge: bool = Query(
        default=True,
        description="Purge package from disk immediately after HTTP TCP response completes (pass purge=false only when preloading before an explicit stop_stream purge)",
    ),
    paced: bool = Query(
        default=False,
        description="Pace DMDS frames in real time at header FPS so constrained embedded TCP clients never stall LwIP TCP_WND",
    ),
    drop_late: bool = Query(
        default=False,
        description="Skip stale frames via wall-clock seek for HTTP/1.1 chunk-decoding clients (omits Content-Length and enables Transfer-Encoding: chunked; omit drop_late on raw unchunked lwip_socket readers)",
    ),
    codec: str = Query(
        default="",
        description="Optional lossless frame codec accepted by the client (`dmdz1`). Honoured only with `paced=1` and without `drop_late`; otherwise the raw `DMDS` stream is served.",
    ),
) -> Response:
    """
    Stream the unified `DMDS` binary sequence (`Jacket Panorama -> Marquee Panorama -> Snapshots -> Video`)
    for `item_id` over HTTP TCP (`MQTT+TCP` or `HTTP` Consume-Once) and immediately purge the consumed
    files from disk once the HTTP response completes (unless `purge=false` is explicitly passed).

    ### Binary HTTP TCP `DMDS` Header (16 Bytes, Little-Endian `<4sBBHHHHH`)
    - `0..3` (`char[4]`): `"DMDS"` (`0x44 0x4D 0x44 0x53`)
    - `4` (`uint8`): `version` (`1`)
    - `5` (`uint8`): `fps` (`8..60`)
    - `6..7` (`uint16le`): `width` (panel width in pixels, e.g. `128`)
    - `8..9` (`uint16le`): `height` (panel height in pixels, e.g. `32`)
    - `10..11` (`uint16le`): `total_frames` (`1..65535`)
    - `12..13` (`uint16le`): `frame_dur_ms` (`round(1000 / fps)`)
    - `14..15` (`uint16le`): `reserved` (`0`)
    - `16..EOF`: Contiguous `total_frames * width * height * 2` bytes of Little-Endian RGB565 pixels.

    For passive Mode 2B UDP `DMDF` datagram streaming (`10`-byte `<4sHBBH` header), see `/asyncapi.yaml`.
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
    if paced and not drop_late and codec.strip().lower() == DMDZ_CODEC_ID and file_size > 16:
        dmdz_path = await asyncio.to_thread(ensure_dmdz_sibling, stream_path)
        if dmdz_path is not None:
            return _paced_dmdz_response(co, pkg, domain, item_id, dmdz_path, purge)
    if paced and file_size > 16:
        with stream_path.open("rb") as probe_fh:
            hdr = probe_fh.read(16)
        if len(hdr) == 16 and hdr[:4] == b"DMDS":
            _, _ver, hdr_fps, width, height, _total_frames, _frame_dur_ms, _res = struct.unpack(
                "<4sBBHHHHH", hdr
            )
            frame_bytes = max(2, int(width) * int(height) * 2)
            eff_fps = max(4, min(60, int(hdr_fps or pkg.card.stream_fps or 15)))
            total_frames_avail = max(1, (file_size - 16) // frame_bytes)
            claim_key = (pkg.dmd_id, domain)

            async def _paced_dmds_generator() -> AsyncIterator[bytes]:
                fh = stream_path.open("rb")
                co.state.active_tcp_streams += 1
                try:
                    now_wall = time.time()
                    co.state.client_last_seen[claim_key] = now_wall
                    co.state.last_claimed_until[claim_key] = now_wall + 15.0
                    first_payload = fh.read(16 + frame_bytes)
                    if first_payload:
                        yield first_payload
                    t0 = time.perf_counter()
                    frame_idx = 1
                    frame_period_s = 1.0 / float(eff_fps)
                    while frame_idx < total_frames_avail:
                        target_t = t0 + (frame_idx * frame_period_s)
                        now_perf = time.perf_counter()
                        sleep_s = target_t - now_perf
                        if sleep_s > 0.001:
                            await asyncio.sleep(sleep_s)
                            now_perf = time.perf_counter()
                            sleep_s = target_t - now_perf
                        if drop_late and sleep_s < -(1.25 * frame_period_s):
                            due_frame = int((now_perf - t0) * float(eff_fps))
                            if due_frame >= total_frames_avail:
                                break
                            if due_frame > frame_idx:
                                frame_idx = due_frame
                                fh.seek(16 + frame_idx * frame_bytes)
                        elif not drop_late and sleep_s <= 0.001:
                            if sleep_s < -frame_period_s:
                                # Re-anchor monotonic clock as soon as the stream falls >1 frame behind
                                # so subsequent frames resume smooth nominal FPS instead of bursting at 30+ FPS.
                                t0 = now_perf - (frame_idx * frame_period_s)
                            await asyncio.sleep(0.0015)

                        chunk = fh.read(frame_bytes)
                        if not chunk:
                            break
                        if (frame_idx % 15) == 0:
                            ts_now = time.time()
                            co.state.client_last_seen[claim_key] = ts_now
                            co.state.last_claimed_until[claim_key] = ts_now + 15.0
                        yield chunk
                        frame_idx += 1
                finally:
                    co.state.active_tcp_streams = max(0, co.state.active_tcp_streams - 1)
                    fh.close()
                    co.state.last_claimed_until.pop(claim_key, None)
                    if purge:
                        await co.purge_consumed_package(item_id)

            paced_headers: Dict[str, str] = {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "X-DMD-Consume-Once": "1",
                "X-DMD-Paced": "1",
                "X-Accel-Buffering": "no",
            }
            if drop_late:
                paced_headers["X-DMD-Drop-Late"] = "1"
            else:
                paced_headers["Content-Length"] = str(file_size)

            return StreamingResponse(
                _paced_dmds_generator(),
                media_type="application/octet-stream",
                headers=paced_headers,
            )

    async def _chunked_dmds_generator() -> AsyncIterator[bytes]:
        fh = stream_path.open("rb")
        try:
            while True:
                block = fh.read(65536)
                if not block:
                    break
                yield block
        finally:
            fh.close()
            if purge:
                await co.purge_consumed_package(item_id)

    return StreamingResponse(
        _chunked_dmds_generator(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(file_size),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-DMD-Consume-Once": "1",
        },
    )


def _paced_dmdz_response(
    co: Any,
    pkg: Any,
    domain: str,
    item_id: str,
    dmdz_path: Path,
    purge: bool,
) -> Response:
    """
    Serve the lossless `DMDZ` sibling of a unified stream with lead pacing.

    Frames are sent up to `DMDZ_SEND_LEAD_S` ahead of the real-time clock: compressed frames are small
    enough for several to sit in an embedded client's TCP window, which presents them on its own clock.
    TCP backpressure (window full) re-anchors the server clock instead of triggering catch-up bursts.
    """
    file_size = dmdz_path.stat().st_size
    with dmdz_path.open("rb") as probe_fh:
        hdr = probe_fh.read(16)
    _, _ver, hdr_fps, _w, _h, total_frames, _dur, _kf = struct.unpack("<4sBBHHHHH", hdr)
    if hdr[:4] != DMDZ_MAGIC:
        raise HTTPException(status_code=500, detail="Corrupted DMDZ stream sibling.")
    eff_fps = max(4, min(60, int(hdr_fps or pkg.card.stream_fps or 15)))
    claim_key = (pkg.dmd_id, domain)

    async def _generator() -> AsyncIterator[bytes]:
        fh = dmdz_path.open("rb")
        co.state.active_tcp_streams += 1
        sent = 0
        reanchors = 0
        max_block_ms = 0.0
        started = time.perf_counter()
        try:
            now_wall = time.time()
            co.state.client_last_seen[claim_key] = now_wall
            co.state.last_claimed_until[claim_key] = now_wall + 15.0
            header = fh.read(16)
            period = 1.0 / float(eff_fps)
            t0 = time.perf_counter()
            pending = header
            prev_wire_bytes = 0
            for frame_idx in range(int(total_frames)):
                size_raw = fh.read(2)
                if len(size_raw) < 2:
                    break
                (length,) = struct.unpack("<H", size_raw)
                payload = fh.read(length)
                if len(payload) < length:
                    break
                wire_chunk = pending + size_raw + payload
                if frame_idx > 0:
                    eff_lead = (
                        0.0
                        if (prev_wire_bytes >= DMDZ_LARGE_FRAME_BYTES or len(wire_chunk) >= DMDZ_LARGE_FRAME_BYTES)
                        else min(DMDZ_SEND_LEAD_S, period)
                    )
                    target_t = t0 + (frame_idx * period) - eff_lead
                    delay = target_t - time.perf_counter()
                    if delay > 0.001:
                        await asyncio.sleep(delay)
                    elif delay < -(period + eff_lead):
                        t0 = time.perf_counter() - (frame_idx * period) + eff_lead
                        reanchors += 1
                if (frame_idx % 15) == 0:
                    ts_now = time.time()
                    co.state.client_last_seen[claim_key] = ts_now
                    co.state.last_claimed_until[claim_key] = ts_now + 15.0
                block_t = time.perf_counter()
                if len(wire_chunk) <= DMDZ_SUBPACKET_BYTES:
                    yield wire_chunk
                else:
                    for off in range(0, len(wire_chunk), DMDZ_SUBPACKET_BYTES):
                        if off > 0:
                            await asyncio.sleep(0.002)
                        yield wire_chunk[off : off + DMDZ_SUBPACKET_BYTES]
                max_block_ms = max(max_block_ms, (time.perf_counter() - block_t) * 1000.0)
                prev_wire_bytes = len(wire_chunk)
                pending = b""
                sent += 1
        finally:
            co.state.active_tcp_streams = max(0, co.state.active_tcp_streams - 1)
            fh.close()
            co.state.last_claimed_until.pop(claim_key, None)
            logger.info(
                "[STREAM] dmdz1 '%s' -> %s: frames=%d/%d dur=%.1fs reanchors=%d max_send_block=%.0fms size=%dB",
                item_id,
                pkg.dmd_id,
                sent,
                int(total_frames),
                time.perf_counter() - started,
                reanchors,
                max_block_ms,
                file_size,
            )
            if purge:
                await co.purge_consumed_package(item_id)

    return StreamingResponse(
        _generator(),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-DMD-Consume-Once": "1",
            "X-DMD-Paced": "1",
            "X-DMD-Codec": DMDZ_CODEC_ID,
            "X-Accel-Buffering": "no",
            "Content-Length": str(file_size),
        },
    )


@router.websocket("/api/v1/{domain}/ws/{item_id}.dmdz")
async def stream_consume_once_dmdz_ws(
    websocket: WebSocket,
    domain: DomainType,
    item_id: str,
    purge: bool = Query(default=True),
    client_id: Optional[str] = Query(default=None),
) -> None:
    """
    ZeDMD-style credit-acknowledged lossless `DMDZ` WebSocket stream for constrained ESP32 clients.

    Why credit-acked WebSocket beats blind HTTP TCP push over NAT/Docker proxies + Wi-Fi:
    - `101 Switching Protocols` disables HTTP response buffering across proxies (`gvproxy`, Nginx).
    - Every binary message is capped at `<= 1,024` bytes (single Wi-Fi MTU packet) and gated by a
      2-message sliding credit window (`<= 2,048` bytes max in flight). The ESP32 returns a 1-byte
      ACK after each message, keeping 100% of RX traffic inside pre-allocated static Wi-Fi RX
      buffers (`0` dynamic heap allocations and `0` LwIP `ooseq` reassembly deadlocks).
    """
    co = get_orchestrator()
    pkg = co.active_packages.get(item_id)
    if pkg is None:
        for (slot_dmd, slot_dom), rpkg in list(co.ready_packages.items()):
            if rpkg.item_id == item_id:
                pkg = await co.claim_next_package(slot_dmd, slot_dom, item_id=item_id)
                break
    if pkg is None or not pkg.unified_stream_path or not pkg.unified_stream_path.exists():
        await websocket.close(code=1008)
        return

    dmdz_path = await asyncio.to_thread(ensure_dmdz_sibling, pkg.unified_stream_path)
    if dmdz_path is None or not dmdz_path.exists():
        await websocket.close(code=1011)
        return

    with dmdz_path.open("rb") as probe_fh:
        hdr = probe_fh.read(16)
    if len(hdr) < 16 or hdr[:4] != DMDZ_MAGIC:
        await websocket.close(code=1011)
        return

    _, _ver, hdr_fps, _w, _h, total_frames, _dur, _kf = struct.unpack("<4sBBHHHHH", hdr)
    eff_fps = max(4, min(60, int(hdr_fps or pkg.card.stream_fps or 15)))
    file_size = dmdz_path.stat().st_size
    effective_dmd = (client_id or "").strip() or pkg.dmd_id
    claim_key = (pkg.dmd_id, domain)
    if claim_key in co.state.client_configs and co.state.client_transports.get(claim_key) != "HTTP":
        co.state.client_transports[claim_key] = "MQTT+TCP"

    await websocket.accept()

    credits = DMDZ_WS_INITIAL_CREDITS
    peer_closed = False
    credit_evt = asyncio.Event()
    credit_evt.set()

    async def _ack_reader() -> None:
        nonlocal credits, peer_closed
        try:
            while True:
                msg = await websocket.receive()
                mtype = msg.get("type")
                if mtype == "websocket.disconnect":
                    peer_closed = True
                    credit_evt.set()
                    return
                raw_b = msg.get("bytes")
                raw_t = msg.get("text")
                inc = len(raw_b) if raw_b else (len(raw_t) if raw_t else 0)
                if inc > 0:
                    credits += inc
                    credit_evt.set()
        except (WebSocketDisconnect, RuntimeError, OSError):
            peer_closed = True
            credit_evt.set()

    async def _send_credited(chunk: bytes) -> bool:
        nonlocal credits
        while credits <= 0 and not peer_closed:
            credit_evt.clear()
            if credits > 0 or peer_closed:
                break
            try:
                await asyncio.wait_for(credit_evt.wait(), timeout=DMDZ_WS_ACK_TIMEOUT_S)
            except asyncio.TimeoutError:
                return False
        if peer_closed:
            return False
        credits -= 1
        await websocket.send_bytes(chunk)
        return True

    ack_task = asyncio.create_task(_ack_reader())
    fh = dmdz_path.open("rb")
    co.state.active_tcp_streams += 1
    sent_frames = 0
    reanchors = 0
    started = time.perf_counter()
    end_reason = "eof"
    try:
        now_wall = time.time()
        co.state.client_last_seen[claim_key] = now_wall
        co.state.last_claimed_until[claim_key] = now_wall + 15.0
        header = fh.read(16)
        if not await _send_credited(header):
            end_reason = "hdr_abort"
            return

        period = 1.0 / float(eff_fps)
        t0 = time.perf_counter()
        for frame_idx in range(int(total_frames)):
            size_raw = fh.read(2)
            if len(size_raw) < 2:
                break
            (length,) = struct.unpack("<H", size_raw)
            payload = fh.read(length)
            if len(payload) < length:
                break
            if frame_idx > 0:
                target_t = t0 + (frame_idx * period)
                delay = target_t - time.perf_counter()
                if delay > 0.001:
                    await asyncio.sleep(delay)
                elif delay < -period:
                    t0 = time.perf_counter() - (frame_idx * period)
                    reanchors += 1
            if (frame_idx % 15) == 0:
                ts_now = time.time()
                co.state.client_last_seen[claim_key] = ts_now
                co.state.last_claimed_until[claim_key] = ts_now + 15.0

            wire_frame = size_raw + payload
            ok_frame = True
            for off in range(0, len(wire_frame), DMDZ_SUBPACKET_BYTES):
                if not await _send_credited(wire_frame[off : off + DMDZ_SUBPACKET_BYTES]):
                    ok_frame = False
                    break
            if not ok_frame:
                end_reason = "peer_closed" if peer_closed else "ack_timeout"
                break
            sent_frames += 1

        # Wait briefly for final in-flight ACKs so TCP never closes before ESP32 drains the tail
        drain_deadline = time.perf_counter() + 0.8
        while (
            sent_frames == int(total_frames)
            and credits < DMDZ_WS_INITIAL_CREDITS
            and not peer_closed
            and time.perf_counter() < drain_deadline
        ):
            credit_evt.clear()
            if credits >= DMDZ_WS_INITIAL_CREDITS or peer_closed:
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(credit_evt.wait(), timeout=0.1)
    except (WebSocketDisconnect, RuntimeError, OSError):
        end_reason = "peer_closed"
    finally:
        ack_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await ack_task
        with contextlib.suppress(Exception):
            if not peer_closed:
                await websocket.close()
        co.state.active_tcp_streams = max(0, co.state.active_tcp_streams - 1)
        fh.close()
        co.state.last_claimed_until.pop(claim_key, None)
        logger.info(
            "[STREAM] ws-dmdz1 '%s' -> %s (%s): frames=%d/%d dur=%.1fs reanchors=%d size=%dB",
            item_id,
            effective_dmd,
            end_reason,
            sent_frames,
            int(total_frames),
            time.perf_counter() - started,
            reanchors,
            file_size,
        )
        if purge:
            await co.purge_consumed_package(item_id)
