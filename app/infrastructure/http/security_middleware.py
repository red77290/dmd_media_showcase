"""
Optional API-Key Security Middleware (`app/infrastructure/http/security_middleware.py`).
"""

from __future__ import annotations

from fastapi import Request, Response

from app.config import settings
from app.infrastructure.http.dependencies import get_server_config_store

PUBLIC_API_PATHS = frozenset({"/api/v1/health", "/api/v1/asyncapi.yaml"})


async def optional_api_key_security_middleware(request: Request, call_next) -> Response:
    """
    Hybrid Home-Lab HTTP (zero-config for ESP32/RPi) & Online HTTPS/Token Guard:
    - Public routes (`/`, `/admin`, `/static/*`, `/api/v1/health`, `/api/v1/asyncapi.yaml`, `/docs`)
      are always accessible so the Control Center shell and stylesheets load before authentication.
    - When `showcase_api_key` is configured, all other `/api/v1/*` routes require a matching
      `X-Showcase-Key` header, `Authorization: Bearer <key>` header, or `?api_key=<key>` query param.
    """
    path = request.url.path
    if path.startswith("/api/v1/") and path not in PUBLIC_API_PATHS:
        srv_store = get_server_config_store()
        expected_key = str(
            srv_store.get_all().get("showcase_api_key") or settings.showcase_api_key or ""
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
    response = await call_next(request)
    if path in ("/", "/admin") or path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response
