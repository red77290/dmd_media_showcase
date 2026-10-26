"""
Request-Time Dependency Accessors (`app/infrastructure/http/dependencies.py`).

Resolves orchestrators, repositories, and gateways dynamically through `app.main`
so FastAPI routers remain decoupled while honoring runtime overrides and test
monkeypatches (`monkeypatch.setattr("app.main.<attr>", ...)`).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from app.config import settings
from app.models import DomainType

ALL_SHOWCASE_DOMAINS: Tuple[DomainType, ...] = ("games", "movies", "series", "anime")


def get_main_module() -> Any:
    import app.main as _main

    return _main


def get_orchestrator() -> Any:
    return get_main_module().consume_once_orchestrator


def get_seen_repo() -> Any:
    return get_main_module().seen_repository


def get_profile_store() -> Any:
    return get_main_module().dmd_profile_store


def get_server_config_store() -> Any:
    return get_main_module().server_config_store


def get_source_chain() -> Any:
    return get_main_module().source_chain_service


def get_scraper_registry() -> Any:
    return get_main_module().scraper_registry


def get_update_checker() -> Any:
    return get_main_module().release_update_checker


def get_http_client_factory() -> Any:
    return get_main_module().create_async_http_client


def get_batch_manager() -> Any:
    return get_main_module().batch_manager


def get_mqtt_gateway() -> Any:
    return get_main_module().showcase_mqtt_gateway


def get_mdns_responder() -> Any:
    return get_main_module().showcase_mdns_responder


def get_session_client_meta() -> Dict[Tuple[str, str], Tuple[str, float]]:
    return get_main_module()._session_client_meta


def normalized_seen_stats() -> Dict[str, int]:
    """Always return all 4 canonical Showcase domains even when `seen_history.json` is empty."""
    raw = get_seen_repo().stats() or {}
    return {dom: int(raw.get(dom, 0)) for dom in ALL_SHOWCASE_DOMAINS}


def collect_http_sessions_with_ip() -> List[Tuple[Any, str]]:
    """Collect active HTTP polling sessions alongside their detected peer LAN IP."""
    bm = get_batch_manager()
    meta_map = get_session_client_meta()
    out: List[Tuple[Any, str]] = []
    for sess in bm.sessions.values():
        cid = sess.request.client_id or sess.session_id
        meta = meta_map.get((cid, sess.domain))
        sess_ip = meta[0] if meta and meta[0] not in ("unknown", "testclient") else ""
        out.append((sess, sess_ip))
    return out


def build_connected_clients_snapshot() -> List[Dict[str, Any]]:
    """Merge Consume-Once clients with active legacy HTTP batch polling sessions."""
    now_ts = time.time()
    co = get_orchestrator()
    bm = get_batch_manager()
    meta_map = get_session_client_meta()

    clients: List[Dict[str, Any]] = co.describe_connected_clients()
    existing_keys = {(c["dmd_id"], c["domain"]) for c in clients}
    for sess in bm.sessions.values():
        cid = sess.request.client_id or sess.session_id
        if cid == "webui_tester" or (cid, sess.domain) in existing_keys:
            continue
        cur_items = sess.current_batch.items
        nxt_items = sess.next_batch.items if sess.next_batch else []
        meta = meta_map.get((cid, sess.domain))
        sess_ip = meta[0] if meta and meta[0] not in ("unknown", "testclient") else ""
        last_seen_ago = int(max(0.0, now_ts - meta[1])) if meta else None
        cur_title = cur_items[0].title if cur_items else None
        is_served = sess.current_batch.served_count > 0 and cur_title is not None
        clients.append(
            {
                "dmd_id": cid,
                "display_name": cid,
                "domain": sess.domain,
                "config_source": "client",
                "supports_local_card": True,
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
    return clients
