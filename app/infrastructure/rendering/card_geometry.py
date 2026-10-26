"""
YOKO & TATE Info Card Layout Geometry Calculator (`app/infrastructure/rendering/card_geometry.py`).

Pure pixel-coordinate solver for all 5 LED Info Card layout presets (`classic_split`,
`poster_right`, `badge_header`, `hero_banner`, `minimal_arcade`) across horizontal (YOKO)
and vertical (TATE) HUB75 matrices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image


@dataclass
class CardLayoutGeometry:
    """Computed pixel coordinates for a YOKO or TATE Info Card layout."""

    poster_img: Optional[Image.Image]
    poster_x: int
    poster_y: int
    header_bar_h: int
    sep_line: Optional[Tuple[Tuple[int, int], Tuple[int, int]]]
    text_x: int
    text_w: int
    top_y: int
    title_y: int
    body_y: int


def compute_card_layout_geometry(
    layout: str,
    theme: str,
    width: int,
    height: int,
    poster_img: Optional[Image.Image],
) -> CardLayoutGeometry:
    """Compute YOKO / TATE coordinates across all 5 card layout presets."""
    is_tate = height > width
    header_bar_h = 0
    sep_line: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None
    poster_x, poster_y = 0, 0

    if layout == "hero_banner":
        poster_img = None
        text_x = 3
        text_w = max(16, width - 6)
        if is_tate:
            top_y, title_y, body_y = 4, 18, 33
        elif height >= 64:
            top_y, title_y, body_y = 5, 22, 41
        else:
            top_y, title_y, body_y = 1, 11, 21
    elif layout == "badge_header":
        header_bar_h = 12 if height >= 64 else 10
        if poster_img is not None:
            avail_h = max(14, height - header_bar_h - 2)
            side = min(avail_h, max(16, width // 3))
            poster_img = poster_img.resize((side, side), Image.Resampling.BILINEAR)
            if is_tate:
                poster_x = (width - side) // 2
                poster_y = header_bar_h + 2
                text_x = 2
                text_w = max(16, width - 4)
                top_y = -100
                title_y = poster_y + side + 3
                body_y = title_y + 12
            else:
                poster_x = 1
                poster_y = header_bar_h + max(0, (height - header_bar_h - side) // 2)
                text_x = side + 4
                text_w = max(16, width - text_x - 2)
                top_y = -100
                title_y = header_bar_h + (6 if height >= 64 else 0)
                body_y = header_bar_h + (24 if height >= 64 else 10)
        else:
            text_x = 3
            text_w = max(16, width - 6)
            top_y = -100
            title_y = header_bar_h + (6 if height >= 64 else 0)
            body_y = header_bar_h + (24 if height >= 64 else 10)
    elif is_tate:
        text_x = 2
        text_w = max(16, width - 4)
        if poster_img is not None:
            max_side = 40 if width >= 64 else 22
            if poster_img.width > max_side or poster_img.height > max_side:
                poster_img = poster_img.resize((max_side, max_side), Image.Resampling.BILINEAR)
            poster_x = (width - poster_img.width) // 2
            if layout == "poster_right":
                poster_y = max(2, height - poster_img.height - 2)
                sep_y = max(1, poster_y - 2)
                sep_line = ((2, sep_y), (width - 3, sep_y))
                top_y, title_y = 3, 16
                body_y = 29 if layout != "minimal_arcade" else -100
            else:
                poster_y = 2
                sep_y = poster_y + poster_img.height + 1
                sep_line = ((2, sep_y), (width - 3, sep_y))
                top_y = poster_img.height + 4
                title_y = top_y + 13
                body_y = title_y + 13 if layout != "minimal_arcade" else -100
        else:
            top_y, title_y = 4, 18
            body_y = 32 if layout != "minimal_arcade" else -100
    else:
        if poster_img is not None:
            if width <= 64 and (poster_img.width > 22 or poster_img.height > 22):
                poster_img = poster_img.resize((22, 22), Image.Resampling.BILINEAR)
            poster_y = (height - poster_img.height) // 2
            if layout == "poster_right":
                poster_x = width - poster_img.width
                sep_x = max(0, poster_x - 2)
                sep_line = ((sep_x, 0), (sep_x, height - 1))
                text_x = 3
                text_w = max(16, poster_x - 5)
            else:
                poster_x = 0
                sep_x = min(width - 1, poster_img.width + 1)
                sep_line = ((sep_x, 0), (sep_x, height - 1))
                text_x = poster_img.width + 3
                text_w = max(16, width - text_x - 2)
        else:
            text_x = 3
            text_w = max(16, width - 6)

        if layout == "minimal_arcade":
            top_y = 6 if height >= 64 else 3
            title_y = 30 if height >= 64 else 17
            body_y = -100
        elif height >= 64:
            top_y, title_y, body_y = 4, 21, 39
        else:
            top_y, title_y = 1, 11
            body_y = 20 if theme == "neon_border" else 21

    return CardLayoutGeometry(
        poster_img=poster_img,
        poster_x=poster_x,
        poster_y=poster_y,
        header_bar_h=header_bar_h,
        sep_line=sep_line,
        text_x=text_x,
        text_w=text_w,
        top_y=top_y,
        title_y=title_y,
        body_y=body_y,
    )
