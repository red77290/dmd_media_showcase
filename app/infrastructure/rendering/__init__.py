"""
Modular DMD Rendering & Asset Pipeline (`app/infrastructure/rendering`).

Uses PEP 562 lazy attribute loading (`__getattr__`) so lightweight submodules like
`app.infrastructure.rendering.dmdz_codec` can be imported standalone in workers and tests
without eagerly importing `card_layout_engine` / `video_pipeline`.
"""

from __future__ import annotations

from typing import Any

_EXPORT_MAP = {
    "CardLayoutGeometry": ("app.infrastructure.rendering.card_layout_engine", "CardLayoutGeometry"),
    "PilCardPreviewRendererAdapter": ("app.infrastructure.rendering.card_layout_engine", "PilCardPreviewRendererAdapter"),
    "build_card_stream_sync": ("app.infrastructure.rendering.card_layout_engine", "build_card_stream_sync"),
    "build_fast_studio_card_preview_sync": ("app.infrastructure.rendering.card_layout_engine", "build_fast_studio_card_preview_sync"),
    "compute_card_layout_geometry": ("app.infrastructure.rendering.card_layout_engine", "compute_card_layout_geometry"),
    "pil_card_preview_renderer": ("app.infrastructure.rendering.card_layout_engine", "pil_card_preview_renderer"),
    "apply_amber_dmd_palette": ("app.infrastructure.rendering.imaging_primitives", "apply_amber_dmd_palette"),
    "apply_worker_nice": ("app.infrastructure.rendering.imaging_primitives", "apply_worker_nice"),
    "default_http_download_bytes": ("app.infrastructure.rendering.imaging_primitives", "default_http_download_bytes"),
    "fit_poster_preserving_cover": ("app.infrastructure.rendering.imaging_primitives", "fit_poster_preserving_cover"),
    "generate_fallback_artwork": ("app.infrastructure.rendering.imaging_primitives", "generate_fallback_artwork"),
    "load_card_font": ("app.infrastructure.rendering.imaging_primitives", "load_card_font"),
    "parse_hex_rgb": ("app.infrastructure.rendering.imaging_primitives", "parse_hex_rgb"),
    "resolve_framing_and_color_params": ("app.infrastructure.rendering.imaging_primitives", "resolve_framing_and_color_params"),
    "resolve_scroll_px_per_sec": ("app.infrastructure.rendering.imaging_primitives", "resolve_scroll_px_per_sec"),
    "sanitize_card_text": ("app.infrastructure.rendering.imaging_primitives", "sanitize_card_text"),
    "build_static_assets_sync": ("app.infrastructure.rendering.panorama_and_static", "build_static_assets_sync"),
    "convert_image_to_panorama_gif_and_stream": ("app.infrastructure.rendering.panorama_and_static", "convert_image_to_panorama_gif_and_stream"),
    "build_video_assets_worker_task": ("app.infrastructure.rendering.video_pipeline", "build_video_assets_worker_task"),
}

__all__ = sorted(_EXPORT_MAP.keys())


def __getattr__(name: str) -> Any:
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = target
    import importlib

    mod = importlib.import_module(mod_name)
    val = getattr(mod, attr_name)
    globals()[name] = val
    return val
