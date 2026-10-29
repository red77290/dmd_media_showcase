"""
DMD Client Profiles, Passive Playlists & On-Demand Priority Queue Routes (`app/infrastructure/http/routers/clients_and_queue.py`).
"""

from __future__ import annotations

from typing import Any, Dict
import shutil

from fastapi import APIRouter, HTTPException, Query, Request

from app.infrastructure.http.dependencies import (
    build_connected_clients_snapshot,
    collect_http_sessions_with_ip,
    get_batch_manager,
    get_on_demand_cache,
    get_orchestrator,
    get_profile_store,
    get_session_client_meta,
)
from app.models import (
    ClientControlRequest,
    ClientHelloPayload,
    DmdProfilePayload,
    OnDemandCacheConfigPayload,
    QueueAddRequest,
)

router = APIRouter()


@router.get("/api/v1/clients", tags=["DMD Profiles & Passive Mode"])
async def list_dmd_clients() -> Dict[str, Any]:
    """
    Return the consolidated list of all connected DMD clients, persisted `dmd_profiles.json` profiles,
    their active operating mode (`client` vs `server` 2A/2B), broadcast `lang`, and On-Demand queue depth.
    """
    co = get_orchestrator()
    return {
        "clients": build_connected_clients_snapshot(),
        "profiles": get_profile_store().list_profiles(),
        "queues": {
            dmd_id: co.list_queue(dmd_id)
            for dmd_id in sorted(co.priority_queues.keys())
            if co.list_queue(dmd_id)
        },
    }


@router.post("/api/v1/clients/hello", tags=["DMD Profiles & Passive Mode"])
async def announce_passive_dmd_client(
    payload: ClientHelloPayload,
    request: Request,
) -> Dict[str, Any]:
    """
    Lightweight HTTP registration/heartbeat endpoint for passive ESP32/RPi receivers without MQTT.
    Auto-creates or updates hardware geometry and LAN IP in `dmd_profiles.json` without overwriting playlists.
    """
    co = get_orchestrator()
    peer_ip = request.client.host if request.client else ""
    prof = await co.handle_client_hello(
        payload.dmd_id,
        payload.model_dump(),
        detected_ip=peer_ip,
    )
    return {
        "status": "ok",
        "dmd_id": payload.dmd_id,
        "profile": prof,
        "queue": co.list_queue(payload.dmd_id),
    }


@router.get("/api/v1/clients/{dmd_id}/profile", tags=["DMD Profiles & Passive Mode"])
async def get_dmd_client_profile(dmd_id: str) -> Dict[str, Any]:
    """
    Retrieve the persisted `dmd_profiles.json` configuration for `dmd_id` (Mode 2A / 2B),
    or synthesize a read-only live inspector profile from the actual MQTT Retain / HTTP Polling
    configuration pushed by an active Mode 1 DMD (`ArcadeMatrix` / `ArcadeMatrix_RPi`).
    """
    co = get_orchestrator()
    prof = get_profile_store().get_profile(dmd_id)
    if prof is not None:
        is_client_cfg = str(prof.get("config_source") or "server") == "client"
        live_payload = None
        if is_client_cfg:
            live_syn = co.synthesize_live_client_profile(
                dmd_id, extra_http_sessions=collect_http_sessions_with_ip()
            )
            if live_syn is not None:
                return live_syn
        return {
            "persisted": True,
            "read_only_client_managed": is_client_cfg,
            "profile": prof,
            "client_live_payload": live_payload,
        }

    live_syn = co.synthesize_live_client_profile(
        dmd_id, extra_http_sessions=collect_http_sessions_with_ip()
    )
    if live_syn is not None:
        return live_syn
    raise HTTPException(status_code=404, detail=f"DMD profile '{dmd_id}' not found.")


@router.put("/api/v1/clients/{dmd_id}/profile", tags=["DMD Profiles & Passive Mode"])
async def put_dmd_client_profile(
    dmd_id: str,
    payload: DmdProfilePayload,
    force_override: bool = Query(
        default=False,
        description="Explicitly convert an active Mode 1 client-driven DMD into a server-managed profile.",
    ),
) -> Dict[str, Any]:
    """Create or update a DMD profile and multi-domain playlist in `./data/dmd_profiles.json` and hot-apply immediately."""
    co = get_orchestrator()
    if not force_override and co.is_live_client_managed_dmd(
        dmd_id, extra_http_sessions=collect_http_sessions_with_ip()
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"DMD '{dmd_id}' is actively driven by the DMD client over MQTT/HTTP (Mode 1, read-only). "
                f"Inject transient items via POST /api/v1/clients/{dmd_id}/queue instead, "
                "or pass ?force_override=true to explicitly convert it to a server-managed profile."
            ),
        )
    raw = payload.model_dump(exclude_none=True)
    raw["dmd_id"] = dmd_id
    saved = await co.upsert_dmd_profile(dmd_id, raw)
    return {"status": "saved", "dmd_id": dmd_id, "profile": saved}


@router.delete("/api/v1/clients/{dmd_id}/profile", tags=["DMD Profiles & Passive Mode"])
async def delete_dmd_client_profile(dmd_id: str) -> Dict[str, Any]:
    """Delete a persisted DMD profile from `./data/dmd_profiles.json` and stop any associated passive loop."""
    deleted = await get_orchestrator().delete_dmd_profile(dmd_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"DMD profile '{dmd_id}' not found.")
    return {"status": "deleted", "dmd_id": dmd_id}


@router.delete("/api/v1/clients/{dmd_id}", tags=["DMD Profiles & Passive Mode"])
async def purge_dmd_client_and_retained_topics(dmd_id: str) -> Dict[str, Any]:
    """
    Force-clean a stale or renamed `dmd_id`: clears all retained MQTT topics on the broker,
    removes persisted `mqtt_retained_clients.json` and `dmd_profiles.json` entries, cancels
    active N/N+1 preparation jobs, stops UDP streams, and purges disk caches and HTTP sessions.
    """
    clean_id = str(dmd_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="dmd_id must not be empty.")

    res = await get_orchestrator().purge_dmd_client(clean_id)
    bm = get_batch_manager()
    meta_map = get_session_client_meta()
    http_cleared = 0
    for sid, sess in list(bm.sessions.items()):
        cid = str(sess.request.client_id or sess.session_id).strip()
        if cid == clean_id or sid == clean_id:
            bm.sessions.pop(sid, None)
            http_cleared += 1
            for slot in (sess.current_batch, sess.next_batch, sess.retired_batch):
                if slot is not None:
                    for item_dir in slot.item_dirs.values():
                        shutil.rmtree(item_dir, ignore_errors=True)
    for meta_key in [k for k in list(meta_map.keys()) if k[0] == clean_id]:
        meta_map.pop(meta_key, None)

    return {
        "status": "purged",
        **res,
        "http_sessions_cleared": http_cleared,
    }


@router.get("/api/v1/clients/{dmd_id}/queue", tags=["On-Demand & Voice Assistant"])
async def get_dmd_priority_queue(dmd_id: str) -> Dict[str, Any]:
    """Inspect the active On-Demand priority queue (`queued`, `preparing`, `ready`, `playing`) for `dmd_id`."""
    items = get_orchestrator().list_queue(dmd_id)
    return {"dmd_id": dmd_id, "count": len(items), "items": items}


@router.post("/api/v1/clients/{dmd_id}/queue", tags=["On-Demand & Voice Assistant"])
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
    co = get_orchestrator()
    try:
        item = await co.enqueue_on_demand(dmd_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "queued",
        "dmd_id": dmd_id,
        "item": item,
        "queue": co.list_queue(dmd_id),
    }


@router.post(
    "/api/v1/clients/{dmd_id}/queue/async",
    status_code=202,
    tags=["On-Demand & Voice Assistant"],
)
async def add_to_dmd_priority_queue_async(
    dmd_id: str,
    req: QueueAddRequest,
) -> Dict[str, Any]:
    """
    Non-blocking asynchronous On-Demand enqueue endpoint (`202 Accepted` in `< 3 ms`).
    - If a persistent conversion cache entry matches `(domain, systems, query, geometry, criteria)`,
      promotes it immediately (`status='ready'`, `cache_hit=True`) with zero scraper lookup or transcoding.
    - Otherwise schedules both scraper lookup and conversion asynchronously in the background and returns
      immediately with `status='preparing'`.
    """
    co = get_orchestrator()
    async_req = req.model_copy(update={"async_prepare": True})
    try:
        item = await co.enqueue_on_demand(dmd_id, async_req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "ready" if item.get("status") == "ready" else "accepted",
        "dmd_id": dmd_id,
        "item": item,
        "queue": co.list_queue(dmd_id),
    }


@router.delete("/api/v1/clients/{dmd_id}/queue/{queue_id}", tags=["On-Demand & Voice Assistant"])
async def remove_from_dmd_priority_queue(
    dmd_id: str,
    queue_id: str,
) -> Dict[str, Any]:
    """Remove an item from `dmd_id`'s On-Demand priority queue and purge any unconsumed temporary files."""
    co = get_orchestrator()
    removed = await co.remove_queue_item(dmd_id, queue_id)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Queue item '{queue_id}' not found on '{dmd_id}'.",
        )
    return {
        "status": "removed",
        "dmd_id": dmd_id,
        "queue_id": queue_id,
        "queue": co.list_queue(dmd_id),
    }


@router.post("/api/v1/clients/{dmd_id}/control", tags=["DMD Profiles & Passive Mode"])
async def control_dmd_client(
    dmd_id: str,
    req: ClientControlRequest,
) -> Dict[str, Any]:
    """Send a real-time playback control (`skip`, `pause`, `resume`, `stop`) to `dmd_id`."""
    res = await get_orchestrator().control_client(dmd_id, req.action)
    return {"status": "ok", **res}


@router.get("/api/v1/cache/on-demand", tags=["On-Demand & Voice Assistant"])
async def get_on_demand_cache_stats() -> Dict[str, Any]:
    """Inspect persistent request-keyed On-Demand conversion cache configuration, size, and items."""
    return get_on_demand_cache().get_stats()


@router.put("/api/v1/cache/on-demand/config", tags=["On-Demand & Voice Assistant"])
async def update_on_demand_cache_config(
    payload: OnDemandCacheConfigPayload,
) -> Dict[str, Any]:
    """Enable/disable the persistent On-Demand conversion cache or adjust its MiB quota."""
    return get_on_demand_cache().update_settings(
        enabled=payload.enabled,
        max_mb=payload.max_mb,
    )


@router.post("/api/v1/cache/on-demand/evict", tags=["On-Demand & Voice Assistant"])
@router.delete("/api/v1/cache/on-demand", tags=["On-Demand & Voice Assistant"])
async def evict_on_demand_cache() -> Dict[str, Any]:
    """Atomically purge all cached On-Demand conversions from disk."""
    return get_on_demand_cache().evict_all()
