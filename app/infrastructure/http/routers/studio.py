"""
Mode 2B Card Studio & Binary `DMDS` Simulator Routes (`app/infrastructure/http/routers/studio.py`).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Response

from app.infrastructure.http.dependencies import get_orchestrator
from app.models import StudioCardPreviewRequest, StudioFullPreviewRequest

router = APIRouter()


@router.post(
    "/api/v1/studio/card-preview",
    tags=["Card Studio & Simulator"],
    responses={
        200: {
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            "description": "Fast (< 100 ms) binary `DMDS` RGB565LE stream of the customized Mode 2B Info Card.",
        }
    },
)
async def build_studio_card_preview_endpoint(
    payload: StudioCardPreviewRequest,
) -> Response:
    """
    Render an ultra-fast (`< 100 ms`) binary `DMDS` (`<4sBBHHHHH` + RGB565LE frames) preview of the
    Mode 2B Info Card honoring `width` x `height` (YOKO & TATE), `card_layout`, `card_theme`, `card_font`,
    `text_scroll_speed`, and `card_hold_s`. Runs outside the heavy video transcoding semaphore and
    caches poster downloads on disk so UI slider tweaks respond instantaneously.
    """
    dmds_bytes, chapters = await get_orchestrator().build_studio_card_preview(payload)
    return Response(
        content=dmds_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(dmds_bytes)),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-DMD-Chapters": json.dumps(chapters),
            "Access-Control-Expose-Headers": "X-DMD-Chapters",
        },
    )


@router.post(
    "/api/v1/studio/full-preview",
    tags=["Card Studio & Simulator"],
    responses={
        200: {
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            "description": "Full customized binary `DMDS` stream (`Card + Panoramas + Snapshots + Video`) with `X-DMD-Preview-Id` and `X-DMD-Chapters` headers.",
        }
    },
)
async def build_studio_full_preview_endpoint(
    payload: StudioFullPreviewRequest,
) -> Response:
    """
    Assemble the complete Mode 2B `DMDS` sequence (`Card Intro + Jacket/Marquee Panoramas + 0..5 Snapshots + Video`)
    for interactive playback in the WebUI HUB75 LED Simulator.

    Returns `X-DMD-Preview-Id` (`sim_...`) in response headers so clicking **Add Simulation to Active DMD Queue**
    (`POST /api/v1/clients/{dmd_id}/queue` with `preview_id`) promotes the rendered stream into the live DMD queue
    in `0 ms` with zero re-transcoding when panel dimensions match.
    """
    dmds_bytes, preview_id, chapters = await get_orchestrator().build_studio_full_preview(payload)
    return Response(
        content=dmds_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(dmds_bytes)),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-DMD-Preview-Id": preview_id,
            "X-DMD-Chapters": json.dumps(chapters),
            "Access-Control-Expose-Headers": "X-DMD-Preview-Id, X-DMD-Chapters",
        },
    )
