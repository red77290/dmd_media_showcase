"""
Domain Port: `ICardPreviewRendererPort` for server-side pixel-perfect Info Card rendering
and binary `DMDS` RGB565 stream composition.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union


class ICardPreviewRendererPort(ABC):
    """
    Hexagonal port rendering Info Card previews (`DMDS` binary streams) and merging
    multi-chapter `.rgb565.stream` sequences without coupling Application Services to
    Pillow, NumPy, or `dmd_gif_converter`.
    """

    @abstractmethod
    def build_fast_card_preview_sync(
        self,
        *,
        cache_root: str,
        width: int,
        height: int,
        fps: int,
        title: str,
        year: str,
        badge: str,
        badge_color: str,
        subtitle: str,
        rating: str,
        synopsis: str,
        poster_url: Optional[str],
        poster_w: int,
        poster_h: int,
        include_card_intro: bool,
        card_layout: str,
        card_theme: str,
        card_font: str,
        card_accent_color: str,
        include_poster: bool,
        show_badge: bool,
        show_year: bool,
        show_rating: bool,
        include_synopsis: bool,
        text_scroll_speed: str,
        card_hold_s: float,
        color_boost: Union[str, bool],
    ) -> Tuple[bytes, int, int, float]:
        """Render a `< 100 ms` Card-only `DMDS` stream, returning `(bytes, frames, fps, duration_s)`."""

    @abstractmethod
    def merge_streams(
        self,
        stream_paths: Sequence[Path],
        out_path: Path,
        width: int,
        height: int,
        target_fps: int,
    ) -> Tuple[int, float]:
        """Concatenate multiple `.rgb565.stream` files into `out_path`, returning `(frames, duration_s)`."""
