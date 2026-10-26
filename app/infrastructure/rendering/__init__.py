"""
Modular DMD Rendering & Asset Pipeline (`app/infrastructure/rendering`).
"""

from app.infrastructure.rendering.card_layout_engine import (
    CardLayoutGeometry,
    PilCardPreviewRendererAdapter,
    build_card_stream_sync,
    build_fast_studio_card_preview_sync,
    compute_card_layout_geometry,
    pil_card_preview_renderer,
)
from app.infrastructure.rendering.imaging_primitives import (
    apply_amber_dmd_palette,
    apply_worker_nice,
    default_http_download_bytes,
    fit_poster_preserving_cover,
    generate_fallback_artwork,
    load_card_font,
    parse_hex_rgb,
    resolve_framing_and_color_params,
    resolve_scroll_px_per_sec,
    sanitize_card_text,
)
from app.infrastructure.rendering.panorama_and_static import (
    build_static_assets_sync,
    convert_image_to_panorama_gif_and_stream,
)
from app.infrastructure.rendering.video_pipeline import build_video_assets_worker_task

__all__ = [
    "CardLayoutGeometry",
    "PilCardPreviewRendererAdapter",
    "apply_amber_dmd_palette",
    "apply_worker_nice",
    "build_card_stream_sync",
    "build_fast_studio_card_preview_sync",
    "build_static_assets_sync",
    "build_video_assets_worker_task",
    "compute_card_layout_geometry",
    "convert_image_to_panorama_gif_and_stream",
    "default_http_download_bytes",
    "fit_poster_preserving_cover",
    "generate_fallback_artwork",
    "load_card_font",
    "parse_hex_rgb",
    "pil_card_preview_renderer",
    "resolve_framing_and_color_params",
    "resolve_scroll_px_per_sec",
    "sanitize_card_text",
]
