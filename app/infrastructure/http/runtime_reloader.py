"""
Live Runtime Configuration Reloader (`app/infrastructure/http/runtime_reloader.py`).

Hot-applies changes to worker pool concurrency, Consume-Once video/FPS ceilings,
log levels, MQTT broker mode, and mDNS responder without restarting the server.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Set

from app.config import settings
from app.infrastructure.http.dependencies import (
    get_batch_manager,
    get_mdns_responder,
    get_mqtt_gateway,
    get_orchestrator,
)


def apply_live_log_level(level_name: str) -> str:
    """Update root and `dmd_showcase*` logger verbosity in place."""
    resolved_name = str(level_name or "INFO").strip().upper()
    lvl = getattr(logging, resolved_name, logging.INFO)
    logging.getLogger().setLevel(lvl)
    for name, lg in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(lg, logging.Logger) and name.startswith("dmd_showcase"):
            lg.setLevel(lvl)
    return logging.getLevelName(lvl)


async def apply_runtime_config_live(
    prev_cfg: Dict[str, Any],
    new_cfg: Dict[str, Any],
    touched_keys: Set[str],
) -> Dict[str, Any]:
    """Hot-apply runtime configuration changes across active services with zero restart."""
    bm = get_batch_manager()
    co = get_orchestrator()
    mqtt_gw = get_mqtt_gateway()
    mdns = get_mdns_responder()

    applied_log_level = apply_live_log_level(settings.log_level)

    if "cache_dir" in touched_keys:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)

    worker_changed = bool(
        touched_keys & {"max_concurrent_jobs", "use_process_pool", "cache_dir"}
        or prev_cfg.get("max_concurrent_jobs") != new_cfg.get("max_concurrent_jobs")
        or prev_cfg.get("use_process_pool") != new_cfg.get("use_process_pool")
    )
    if worker_changed and (bm._running or bm._worker_tasks):
        effective_workers = await bm.reconfigure_workers()
    else:
        effective_workers = settings.effective_max_workers
    co.reconfigure_concurrency(effective_workers)

    invalidated_slots = 0
    if touched_keys & {"video_max_duration_limit_s", "video_max_fps_limit"}:
        invalidated_slots = await co.apply_runtime_limits()

    expired_sessions = 0
    if "session_ttl_seconds" in touched_keys:
        expired_sessions = bm.cleanup_expired_sessions()

    mqtt_reloaded = False
    mqtt_changed = any(
        prev_cfg.get(k) != new_cfg.get(k)
        for k in ("mqtt_mode", "mqtt_port", "mqtt_external_url")
    )
    if mqtt_changed and mqtt_gw._running:
        await mqtt_gw.stop()
        await mqtt_gw.start()
        mqtt_reloaded = True
    elif prev_cfg.get("default_lang") != new_cfg.get("default_lang") and mqtt_gw._running:
        await mqtt_gw.publish_catalog_referentials(
            lang=str(new_cfg.get("default_lang") or "fr")
        )

    mdns_reloaded = False
    mdns_changed = any(
        prev_cfg.get(k) != new_cfg.get(k) for k in ("mdns_enabled", "mdns_hostname")
    )
    if mdns_changed and (mdns._transport is not None or mqtt_gw._running):
        await mdns.stop()
        if settings.mdns_enabled:
            mdns.hostname = settings.mdns_hostname
            await mdns.start(port=settings.port)
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
