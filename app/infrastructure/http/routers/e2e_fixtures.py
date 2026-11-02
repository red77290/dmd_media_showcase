"""
Env-gated Local E2E Fixture Static Hosting & Protocol Audit Telemetry (`app/infrastructure/http/routers/e2e_fixtures.py`).

Strictly inactive in normal production runs (`settings.e2e_fixtures_dir == ""`).
When `DMD_E2E_FIXTURES_DIR` points to an existing local fixture folder:
1. Registers `LocalFixtureCatalogAdapter` at the head of `scraper_registry` for offline deterministic lookups.
2. Serves `/_e2e_fixtures/{rel_path}` over loopback HTTP with `Content-Length` for worker subprocess downloads.
3. Exposes `/api/v1/e2e/audit` & `/api/v1/e2e/audit/reset` so automated `pytest` E2E harnesses can verify exact
   `POST /queue/async`, `DELETE /queue`, `.rgb565` poster, and `.rgb565.stream?codec=dmdz2` interactions per DMD.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.config import settings
from app.domain.ports.scraper_registry_port import IScraperRegistryPort
from app.infrastructure.http.dependencies import get_orchestrator
from app.infrastructure.scrapers.local_fixture_adapter import LocalFixtureCatalogAdapter

logger = logging.getLogger("dmd_showcase.http.e2e")

_QUEUE_ASYNC_RE = re.compile(r"^/api/v1/clients/([^/]+)/queue(?:/async)?$")
_POSTER_RE = re.compile(r"^/api/v1/([^/]+)/poster/([^/]+)\.rgb565$")
_STREAM_RE = re.compile(r"^/api/v1/([^/]+)/stream/([^/]+)\.rgb565\.stream$")

_AUDIT_LOCK = threading.Lock()
_AUDIT_EVENTS: Dict[str, List[Dict[str, Any]]] = {
    "queue_posts": [],
    "queue_deletes": [],
    "poster_gets": [],
    "stream_gets": [],
    "stream_deletes": [],
    "hello_posts": [],
}

router = APIRouter()


def _active_fixtures_root() -> Path | None:
    raw_dir = (settings.e2e_fixtures_dir or "").strip()
    if not raw_dir:
        return None
    root = Path(raw_dir).resolve()
    return root if root.is_dir() else None


def reset_e2e_audit() -> None:
    with _AUDIT_LOCK:
        for lst in _AUDIT_EVENTS.values():
            lst.clear()


def _append_bounded(bucket: str, entry: Dict[str, Any], cap: int = 250) -> None:
    with _AUDIT_LOCK:
        lst = _AUDIT_EVENTS[bucket]
        lst.append(entry)
        if len(lst) > cap:
            del lst[: len(lst) - cap]


def ensure_e2e_scraper_registered(registry: IScraperRegistryPort) -> bool:
    """Register `LocalFixtureCatalogAdapter` at the head of `games` when `DMD_E2E_FIXTURES_DIR` is active."""
    root = _active_fixtures_root()
    if root is None:
        return False
    adapter = LocalFixtureCatalogAdapter(
        fixtures_dir=root,
        base_url_resolver=lambda: f"http://127.0.0.1:{settings.port}/_e2e_fixtures",
        domain="games",
    )
    registry.register(adapter, prepend=True)
    logger.info("[E2E-FIXTURES] Active offline fixture catalog mounted from %s", root)
    return True


@router.get("/_e2e_fixtures/{rel_path:path}", include_in_schema=False)
async def serve_e2e_fixture_file(rel_path: str) -> Response:
    root = _active_fixtures_root()
    if root is None:
        raise HTTPException(status_code=404, detail="E2E fixtures disabled")
    candidate = (root / rel_path).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Fixture asset not found")
    return FileResponse(path=str(candidate))


@router.get("/api/v1/e2e/audit", include_in_schema=False)
async def get_e2e_audit_snapshot() -> Dict[str, Any]:
    root = _active_fixtures_root()
    if root is None:
        raise HTTPException(status_code=404, detail="E2E fixtures disabled")
    co = get_orchestrator()
    with _AUDIT_LOCK:
        events_copy = {k: list(v) for k, v in _AUDIT_EVENTS.items()}
    active_queues = {
        dmd_id: co.list_queue(dmd_id)
        for dmd_id in sorted(co.priority_queues.keys())
        if co.list_queue(dmd_id)
    }
    return {
        "e2e_enabled": True,
        "fixtures_dir": str(root),
        "counts": {k: len(v) for k, v in events_copy.items()},
        "events": events_copy,
        "active_queues": active_queues,
    }


@router.post("/api/v1/e2e/audit/reset", include_in_schema=False)
async def post_reset_e2e_audit() -> Dict[str, Any]:
    if _active_fixtures_root() is None:
        raise HTTPException(status_code=404, detail="E2E fixtures disabled")
    reset_e2e_audit()
    return {"ok": True}


def install_e2e_routes_and_middleware(app: FastAPI, registry: IScraperRegistryPort) -> None:
    """Attach optional E2E routes & zero-overhead conditional audit middleware at app assembly."""
    ensure_e2e_scraper_registered(registry)
    app.include_router(router)

    @app.middleware("http")
    async def e2e_protocol_audit_middleware(request: Request, call_next) -> Response:
        if not settings.e2e_fixtures_dir:
            return await call_next(request)

        path = request.url.path
        method = request.method.upper()
        req_body_json: Any = None

        q_match = _QUEUE_ASYNC_RE.match(path)
        if q_match and method == "POST":
            raw_bytes = await request.body()
            try:
                req_body_json = json.loads(raw_bytes.decode("utf-8"))
            except Exception:
                req_body_json = None

        response = await call_next(request)
        ts = round(time.time(), 3)

        if q_match:
            dmd_id = q_match.group(1)
            if method == "POST":
                _append_bounded(
                    "queue_posts",
                    {
                        "dmd_id": dmd_id,
                        "path": path,
                        "status_code": response.status_code,
                        "payload": req_body_json,
                        "ts": ts,
                    },
                )
            elif method == "DELETE":
                _append_bounded(
                    "queue_deletes",
                    {
                        "dmd_id": dmd_id,
                        "path": path,
                        "status_code": response.status_code,
                        "ts": ts,
                    },
                )
            return response

        p_match = _POSTER_RE.match(path)
        if p_match and method == "GET":
            _append_bounded(
                "poster_gets",
                {
                    "domain": p_match.group(1),
                    "item_id": p_match.group(2),
                    "status_code": response.status_code,
                    "ts": ts,
                },
            )
            return response

        s_match = _STREAM_RE.match(path)
        if s_match:
            if method == "GET":
                _append_bounded(
                    "stream_gets",
                    {
                        "domain": s_match.group(1),
                        "item_id": s_match.group(2),
                        "codec": request.query_params.get("codec", ""),
                        "dmd_id": request.query_params.get("dmd_id", ""),
                        "status_code": response.status_code,
                        "ts": ts,
                    },
                )
            elif method == "DELETE":
                _append_bounded(
                    "stream_deletes",
                    {
                        "domain": s_match.group(1),
                        "item_id": s_match.group(2),
                        "dmd_id": request.query_params.get("dmd_id", ""),
                        "status_code": response.status_code,
                        "ts": ts,
                    },
                )
            return response

        if path == "/api/v1/clients/hello" and method == "POST":
            _append_bounded(
                "hello_posts",
                {"status_code": response.status_code, "ts": ts},
            )

        return response
