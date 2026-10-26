"""
Scheduler Worker Facade (`app/scheduler/asset_builder.py`).

Delegates all rendering, panorama scrolling, pixel-art Info Card layouts, and video
transcoding to the modular `app.infrastructure.rendering` package while preserving
top-level pickleable worker entrypoints for `ProcessPoolExecutor` and `_download_bytes`
seam compatibility for offline test fixtures.
"""

from __future__ import annotations

from app.infrastructure.rendering.card_layout_engine import (
    build_card_stream_sync,
    build_fast_studio_card_preview_sync,
)
from app.infrastructure.rendering.imaging_primitives import (
    apply_amber_dmd_palette as _apply_amber_dmd_palette,
    apply_worker_nice as _apply_worker_nice,
    default_http_download_bytes,
    fit_poster_preserving_cover as _fit_poster_preserving_cover,
    generate_fallback_artwork as _generate_fallback_artwork,
    load_card_font as _load_card_font,
    parse_hex_rgb as _parse_hex_rgb,
    resolve_framing_and_color_params,
    resolve_scroll_px_per_sec as _resolve_scroll_px_per_sec,
    sanitize_card_text as _sanitize_card_text,
)
from app.infrastructure.rendering.panorama_and_static import (
    build_static_assets_sync,
    convert_image_to_panorama_gif_and_stream as _convert_image_to_panorama_gif_and_stream,
)
from app.infrastructure.rendering.video_pipeline import build_video_assets_worker_task

# Mutable module-level seam preserved so offline tests monkeypatching
# `app.scheduler.asset_builder._download_bytes` intercept all rendering downloads.
_download_bytes = default_http_download_bytes

__all__ = [
    "_apply_amber_dmd_palette",
    "_apply_worker_nice",
    "_convert_image_to_panorama_gif_and_stream",
    "_download_bytes",
    "_fit_poster_preserving_cover",
    "_generate_fallback_artwork",
    "_load_card_font",
    "_parse_hex_rgb",
    "_resolve_scroll_px_per_sec",
    "_sanitize_card_text",
    "build_card_stream_sync",
    "build_fast_studio_card_preview_sync",
    "build_static_assets_sync",
    "build_video_assets_worker_task",
    "resolve_framing_and_color_params",
]
