"""
Server-Side Info Card Layout Engine & `ICardPreviewRendererPort` Adapter (`app/infrastructure/rendering/card_layout_engine.py`).

Supports 5 pixel-perfect layouts (`classic_split`, `poster_right`, `badge_header`, `hero_banner`,
`minimal_arcade`) with automatic YOKO & TATE reflow, 5 LED themes (`tinted`, `pure_black`,
`neon_border`, `poster_wash`, `amber_dmd`), bitmap/CJK typography, and `< 100 ms` Studio previews.
"""

from __future__ import annotations

import hashlib
import io
import logging
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.domain.ports.card_renderer import ICardPreviewRendererPort
from app.domain.style_specs import (
    VALID_CARD_LAYOUTS,
    VALID_CARD_THEMES,
    parse_hex_rgb,
)
from app.infrastructure.rendering.card_geometry import (
    CardLayoutGeometry,
    compute_card_layout_geometry,
)
from app.infrastructure.rendering.imaging_primitives import (
    apply_amber_dmd_palette,
    fit_poster_preserving_cover,
    generate_fallback_artwork,
    load_card_font,
    resolve_active_downloader,
    resolve_scroll_px_per_sec,
    sanitize_card_text,
)
from dmd_gif_converter import write_rgb565_stream

logger = logging.getLogger("dmd_showcase.converter")


def build_card_stream_sync(
    item_dir: str,
    width: int = 128,
    height: int = 32,
    fps: int = 15,
    title: str = "",
    year: str = "",
    badge: str = "",
    badge_color: str = "#FF6D00",
    subtitle: str = "",
    rating: str = "",
    synopsis: str = "",
    poster_png_name: str = "",
    include_poster: bool = True,
    include_synopsis: bool = True,
    text_scroll_speed: str = "normal",
    include_card_intro: bool = True,
    card_layout: str = "classic_split",
    card_theme: str = "tinted",
    card_font: str = "arcade_pixel",
    card_accent_color: str = "auto",
    show_badge: bool = True,
    show_year: bool = True,
    show_rating: bool = True,
    card_hold_s: float = 0.9,
) -> str:
    """Render Phase 1 Info Card directly into `card_intro_{W}x{H}.rgb565.stream`."""
    if not include_card_intro:
        return ""

    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    eff_fps = max(8, min(60, int(fps)))

    layout = str(card_layout or "classic_split").strip().lower()
    if layout not in VALID_CARD_LAYOUTS:
        layout = "classic_split"
    theme = str(card_theme or "tinted").strip().lower()
    if theme not in VALID_CARD_THEMES:
        theme = "tinted"
    font_style = str(card_font or "arcade_pixel").strip().lower()

    accent_hex = (
        card_accent_color
        if (card_accent_color and str(card_accent_color).strip().lower() not in ("auto", ""))
        else badge_color
    )
    accent = parse_hex_rgb(accent_hex)
    if theme == "amber_dmd":
        accent = (255, 115, 0)

    clean_title = sanitize_card_text(title) or "SHOWCASE"
    if layout == "minimal_arcade":
        clean_title = clean_title.upper()
    clean_badge = (sanitize_card_text(badge) or "DMD").upper()[:14] if show_badge else ""
    raw_year = sanitize_card_text(year) if show_year else ""
    if len(raw_year) > 4 and raw_year[:4].isdigit() and raw_year[4] in "-/. ":
        clean_year = raw_year[:4]
    else:
        clean_year = raw_year[:6]
    raw_rating = sanitize_card_text(rating).lstrip("*★ ").strip() if show_rating else ""
    clean_rating = raw_rating[:8]

    clean_body = (
        "" if (layout == "minimal_arcade" or not include_synopsis) else sanitize_card_text(synopsis or subtitle)
    )

    font_px_size = 9 if height < 48 else (11 if height >= 64 else 10)
    font = load_card_font(f"{clean_title} {clean_body}", size=font_px_size, font_style=font_style)
    if not isinstance(font, ImageFont.FreeTypeFont):
        clean_title = clean_title.encode("latin-1", "replace").decode("latin-1")
        clean_badge = clean_badge.encode("latin-1", "replace").decode("latin-1")
        clean_year = clean_year.encode("latin-1", "replace").decode("latin-1")
        clean_rating = clean_rating.encode("latin-1", "replace").decode("latin-1")
        clean_body = clean_body.encode("latin-1", "replace").decode("latin-1")

    raw_poster_img: Optional[Image.Image] = None
    if poster_png_name:
        p_path = out_dir / poster_png_name
        if p_path.exists():
            try:
                with Image.open(p_path) as raw_p:
                    raw_poster_img = raw_p.convert("RGB")
            except Exception:
                raw_poster_img = None

    init_poster = raw_poster_img.copy() if (include_poster and raw_poster_img is not None) else None

    if theme == "pure_black":
        bg_tint = (0, 0, 0)
    elif theme == "amber_dmd":
        bg_tint = (10, 4, 0)
    else:
        bg_tint = (max(4, accent[0] // 14), max(4, accent[1] // 14), max(6, accent[2] // 14))

    base_bg = Image.new("RGB", (width, height), bg_tint)
    if raw_poster_img is not None and (
        theme == "poster_wash" or (layout == "hero_banner" and theme != "pure_black" and include_poster)
    ):
        fitted_wash = ImageOps.fit(raw_poster_img, (width, height), method=Image.Resampling.BILINEAR)
        wash_arr = (np.asarray(fitted_wash, dtype=np.uint16) * 22 // 100).astype(np.uint8)
        base_bg = Image.fromarray(wash_arr, mode="RGB")

    geom = compute_card_layout_geometry(layout, theme, width, height, init_poster)
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (width, height)))
    if font_style == "arcade_pixel":
        dummy_draw.fontmode = "1"

    def _measure_w(txt: str) -> int:
        if not txt:
            return 0
        try:
            bbox = dummy_draw.textbbox((0, 0), txt, font=font)
            return max(0, int(bbox[2] - bbox[0]))
        except Exception:
            return len(txt) * 6

    title_px = _measure_w(clean_title)
    body_px = _measure_w(clean_body)
    px_per_sec = resolve_scroll_px_per_sec(text_scroll_speed)
    px_per_frame = max(0.75, px_per_sec / float(eff_fps))

    meta_bits = " ".join(b for b in (clean_year, f"*{clean_rating}" if clean_rating else "") if b)
    raw_badge_w = (_measure_w(clean_badge) + 6) if clean_badge else 0
    meta_w = _measure_w(meta_bits) if meta_bits else 0

    if geom.header_bar_h > 0:
        banner_container_w = max(8, width - (2 if theme == "neon_border" else 1))
        banner_align_meta_right = True
    elif geom.top_y >= 0:
        banner_container_w = max(8, geom.text_w)
        banner_align_meta_right = False
    else:
        banner_container_w = 0
        banner_align_meta_right = False

    banner_gap = 4 if (clean_badge and meta_bits) else 0
    banner_unit_w = raw_badge_w + banner_gap + meta_w
    banner_sep_gap = 14
    banner_mode = "none"
    banner_scroll_dist = 0

    if banner_container_w > 0 and (clean_badge or meta_bits):
        if banner_unit_w <= banner_container_w:
            banner_mode = "static"
        elif clean_badge and meta_bits:
            avail_after_badge = banner_container_w - (raw_badge_w + banner_gap)
            if raw_badge_w <= banner_container_w and avail_after_badge >= 28:
                banner_mode = "scroll_meta"
                banner_scroll_dist = meta_w + banner_sep_gap
            else:
                banner_mode = "scroll_full"
                banner_scroll_dist = banner_unit_w + banner_sep_gap
        else:
            banner_mode = "scroll_full"
            banner_scroll_dist = banner_unit_w + banner_sep_gap

    scroll_dist = max(
        0,
        (title_px - geom.text_w + 16) if title_px > geom.text_w else 0,
        (body_px + geom.text_w // 2) if (clean_body and geom.body_y >= 0 and body_px > 0) else 0,
        banner_scroll_dist,
    )
    eff_hold_s = max(0.2, min(5.0, float(card_hold_s)))
    hold_intro_frames = max(3, int(round(eff_fps * eff_hold_s)))
    banner_hold_frames = min(hold_intro_frames, max(2, int(round(eff_fps * 0.35))))
    hold_outro_frames = max(4, int(round(eff_fps * 0.5)))
    scroll_frames = int(scroll_dist / px_per_frame) if scroll_dist > 0 else int(eff_fps * 2.0)
    total_frames = max(
        int(eff_fps * max(2.2, eff_hold_s + 1.4)),
        min(int(eff_fps * 16.0), hold_intro_frames + scroll_frames + hold_outro_frames),
    )

    def _render_banner_region(
        target_img: Image.Image,
        box_x: int,
        box_y: int,
        box_w: int,
        pill_h: int,
        text_dy: int,
        b_scroll_px: int,
    ) -> None:
        if banner_mode == "none" or box_w <= 0:
            return
        crop_y0 = max(0, box_y - 1)
        crop_y1 = min(target_img.height, box_y + pill_h + 2)
        if crop_y1 <= crop_y0:
            return
        rel_y = box_y - crop_y0

        if banner_mode == "static":
            sub = target_img.crop((box_x, crop_y0, box_x + box_w, crop_y1))
            bdraw = ImageDraw.Draw(sub)
            if font_style == "arcade_pixel":
                bdraw.fontmode = "1"
            if clean_badge:
                bdraw.rectangle([0, rel_y, raw_badge_w - 1, rel_y + pill_h - 1], fill=accent)
                bdraw.text((3, rel_y + text_dy), clean_badge, fill=(255, 255, 255), font=font)
            if meta_bits:
                if banner_align_meta_right and clean_badge:
                    mx = max(raw_badge_w + banner_gap, box_w - meta_w - 1)
                elif clean_badge:
                    mx = raw_badge_w + banner_gap
                else:
                    mx = 2 if banner_align_meta_right else 0
                bdraw.text((mx, rel_y + text_dy), meta_bits, fill=(255, 214, 102), font=font)
            target_img.paste(sub, (box_x, crop_y0))
            return

        if banner_mode == "scroll_meta":
            pdraw = ImageDraw.Draw(target_img)
            if font_style == "arcade_pixel":
                pdraw.fontmode = "1"
            pdraw.rectangle(
                [box_x, box_y, box_x + raw_badge_w - 1, box_y + pill_h - 1],
                fill=accent,
            )
            pdraw.text((box_x + 3, box_y + text_dy), clean_badge, fill=(255, 255, 255), font=font)
            mx0 = box_x + raw_badge_w + banner_gap
            mw_avail = box_w - (raw_badge_w + banner_gap)
            if mw_avail > 2 and meta_bits:
                msub = target_img.crop((mx0, crop_y0, mx0 + mw_avail, crop_y1))
                mdraw = ImageDraw.Draw(msub)
                if font_style == "arcade_pixel":
                    mdraw.fontmode = "1"
                cycle = max(1, meta_w + banner_sep_gap)
                shift = b_scroll_px % cycle
                for rx in (-shift, -shift + cycle):
                    mdraw.text((rx, rel_y + text_dy), meta_bits, fill=(255, 214, 102), font=font)
                target_img.paste(msub, (mx0, crop_y0))
            return

        full_x = max(box_x, 1 if (geom.header_bar_h > 0 and theme == "neon_border") else box_x)
        full_w = max(4, box_w - (full_x - box_x))
        fsub = target_img.crop((full_x, crop_y0, full_x + full_w, crop_y1))
        fdraw = ImageDraw.Draw(fsub)
        if font_style == "arcade_pixel":
            fdraw.fontmode = "1"
        cycle = max(1, banner_unit_w + banner_sep_gap)
        shift = b_scroll_px % cycle
        for rx in (-shift, -shift + cycle):
            cur_x = rx
            if clean_badge:
                fdraw.rectangle([cur_x, rel_y, cur_x + raw_badge_w - 1, rel_y + pill_h - 1], fill=accent)
                fdraw.text((cur_x + 3, rel_y + text_dy), clean_badge, fill=(255, 255, 255), font=font)
                cur_x += raw_badge_w + banner_gap
            if meta_bits:
                fdraw.text((cur_x, rel_y + text_dy), meta_bits, fill=(255, 214, 102), font=font)
        target_img.paste(fsub, (full_x, crop_y0))

    frames_np: List[np.ndarray] = []
    for idx in range(total_frames):
        canvas = base_bg.copy()
        draw = ImageDraw.Draw(canvas)
        if font_style == "arcade_pixel":
            draw.fontmode = "1"

        scroll_px = int(round((idx - hold_intro_frames) * px_per_frame)) if idx > hold_intro_frames else 0
        banner_scroll_px = (
            int(round((idx - banner_hold_frames) * px_per_frame)) if idx > banner_hold_frames else 0
        )

        if geom.header_bar_h > 0:
            draw.rectangle(
                [0, 0, width - 1, geom.header_bar_h - 1],
                fill=(max(12, accent[0] // 3), max(12, accent[1] // 3), max(14, accent[2] // 3)),
            )
            _render_banner_region(
                canvas,
                box_x=0,
                box_y=0,
                box_w=banner_container_w,
                pill_h=geom.header_bar_h,
                text_dy=0 if geom.header_bar_h <= 10 else 1,
                b_scroll_px=banner_scroll_px,
            )

        strip = canvas.crop((geom.text_x, 0, geom.text_x + geom.text_w, height))
        sdraw = ImageDraw.Draw(strip)
        if font_style == "arcade_pixel":
            sdraw.fontmode = "1"

        if geom.top_y >= 0:
            _render_banner_region(
                strip,
                box_x=0,
                box_y=geom.top_y,
                box_w=banner_container_w,
                pill_h=10,
                text_dy=-1,
                b_scroll_px=banner_scroll_px,
            )

        if geom.title_y >= 0 and geom.title_y + 7 <= height:
            tx = 0 if title_px <= geom.text_w else -min(title_px - geom.text_w + 10, scroll_px)
            sdraw.text((tx, geom.title_y), clean_title, fill=(255, 255, 255), font=font)

        if clean_body and geom.body_y >= 0 and geom.body_y + 7 <= height:
            bx = 0 if body_px <= geom.text_w else (max(8, geom.text_w // 3) - scroll_px)
            sdraw.text((bx, geom.body_y), clean_body, fill=(190, 225, 255), font=font)

        canvas.paste(strip, (geom.text_x, 0))

        if geom.poster_img is not None:
            canvas.paste(geom.poster_img, (geom.poster_x, geom.poster_y))
        if geom.sep_line is not None:
            draw.line(
                [geom.sep_line[0], geom.sep_line[1]],
                fill=accent if layout == "minimal_arcade" else (accent[0] // 2, accent[1] // 2, accent[2] // 2),
            )
        if theme == "neon_border":
            draw.rectangle([0, 0, width - 1, height - 1], outline=accent)

        arr = np.asarray(canvas, dtype=np.uint8)
        if theme == "amber_dmd":
            arr = apply_amber_dmd_palette(arr)
        frames_np.append(arr)

    out_stream = out_dir / f"card_intro_{width}x{height}.rgb565.stream"
    write_rgb565_stream(frames_np, out_stream, fps=eff_fps)
    logger.info(
        "[CONVERTER] Built server-side Info Card stream '%s' (%dx%d, layout=%s, theme=%s, %d frames @ %dfps)",
        out_stream.name,
        width,
        height,
        layout,
        theme,
        len(frames_np),
        eff_fps,
    )
    return out_stream.name


def build_fast_studio_card_preview_sync(
    cache_root: str,
    width: int = 128,
    height: int = 32,
    fps: int = 20,
    title: str = "Super Street Fighter II Turbo",
    year: str = "1994",
    badge: str = "CPS2",
    badge_color: str = "#FF6D00",
    subtitle: str = "Capcom • Arcade Fighting",
    rating: str = "9.2",
    synopsis: str = "",
    poster_url: Optional[str] = None,
    poster_w: int = 32,
    poster_h: int = 32,
    include_card_intro: bool = True,
    card_layout: str = "classic_split",
    card_theme: str = "tinted",
    card_font: str = "arcade_pixel",
    card_accent_color: str = "auto",
    include_poster: bool = True,
    show_badge: bool = True,
    show_year: bool = True,
    show_rating: bool = True,
    include_synopsis: bool = True,
    text_scroll_speed: str = "normal",
    card_hold_s: float = 0.9,
    color_boost: Union[str, bool] = "auto",
) -> Tuple[bytes, int, int, float]:
    """Fast (< 100 ms) Card-only `DMDS` preview builder caching raw `poster_url` bytes on disk."""
    download_fn = resolve_active_downloader()
    eff_fps = max(8, min(60, int(fps)))
    palette = parse_hex_rgb(
        card_accent_color
        if (card_accent_color and str(card_accent_color).strip().lower() not in ("auto", ""))
        else badge_color
    )
    color_boost_bool = not (
        color_boost is False
        or str(color_boost).strip().lower() in ("false", "0", "no", "off", "none")
    )

    poster_cache_dir = Path(cache_root) / "studio_posters"
    poster_cache_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes: Optional[bytes] = None
    if poster_url and poster_url.startswith(("http://", "https://")):
        url_key = hashlib.sha1(poster_url.encode("utf-8")).hexdigest()[:20]
        cached_file = poster_cache_dir / f"{url_key}.bin"
        if cached_file.exists():
            try:
                raw_bytes = cached_file.read_bytes()
            except Exception:
                raw_bytes = None
        else:
            raw_bytes = download_fn(poster_url, timeout=3.5)
            if raw_bytes:
                try:
                    cached_file.write_bytes(raw_bytes)
                except Exception:
                    pass

    with tempfile.TemporaryDirectory(prefix="studio_card_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        poster_png_name = f"poster_{poster_w}x{poster_h}.png"
        poster_img: Optional[Image.Image] = None
        if raw_bytes:
            try:
                with Image.open(io.BytesIO(raw_bytes)) as src_p:
                    poster_img = fit_poster_preserving_cover(
                        src_p, poster_w, poster_h, palette, color_boost=color_boost_bool
                    )
            except Exception:
                poster_img = None
        if poster_img is None:
            generate_fallback_artwork(
                tmp_path / poster_png_name, poster_w, poster_h, title or "DMD", badge or "DMD", palette
            )
        else:
            poster_img.save(tmp_path / poster_png_name, format="PNG")

        if not include_card_intro:
            blank = np.zeros((height, width, 3), dtype=np.uint8)
            out_stream = tmp_path / f"card_disabled_{width}x{height}.rgb565.stream"
            write_rgb565_stream([blank] * eff_fps, out_stream, fps=eff_fps)
            return out_stream.read_bytes(), eff_fps, eff_fps, 1.0

        stream_name = build_card_stream_sync(
            item_dir=str(tmp_path),
            width=width,
            height=height,
            fps=eff_fps,
            title=title,
            year=year,
            badge=badge,
            badge_color=badge_color,
            subtitle=subtitle,
            rating=rating,
            synopsis=synopsis,
            poster_png_name=poster_png_name,
            include_poster=include_poster,
            include_synopsis=include_synopsis,
            text_scroll_speed=text_scroll_speed,
            include_card_intro=True,
            card_layout=card_layout,
            card_theme=card_theme,
            card_font=card_font,
            card_accent_color=card_accent_color,
            show_badge=show_badge,
            show_year=show_year,
            show_rating=show_rating,
            card_hold_s=card_hold_s,
        )
        stream_path = tmp_path / stream_name
        dmds_bytes = stream_path.read_bytes()
        frame_bytes = max(2, width * height * 2)
        n_frames = max(1, (len(dmds_bytes) - 16) // frame_bytes)
        return dmds_bytes, n_frames, eff_fps, round(n_frames / float(eff_fps), 2)


class PilCardPreviewRendererAdapter(ICardPreviewRendererPort):
    """Concrete Hexagonal Adapter implementing `ICardPreviewRendererPort`."""

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
        return build_fast_studio_card_preview_sync(
            cache_root=cache_root,
            width=width,
            height=height,
            fps=fps,
            title=title,
            year=year,
            badge=badge,
            badge_color=badge_color,
            subtitle=subtitle,
            rating=rating,
            synopsis=synopsis,
            poster_url=poster_url,
            poster_w=poster_w,
            poster_h=poster_h,
            include_card_intro=include_card_intro,
            card_layout=card_layout,
            card_theme=card_theme,
            card_font=card_font,
            card_accent_color=card_accent_color,
            include_poster=include_poster,
            show_badge=show_badge,
            show_year=show_year,
            show_rating=show_rating,
            include_synopsis=include_synopsis,
            text_scroll_speed=text_scroll_speed,
            card_hold_s=card_hold_s,
            color_boost=color_boost,
        )

    def merge_streams(
        self,
        stream_paths: Sequence[Path],
        out_path: Path,
        width: int,
        height: int,
        target_fps: int,
    ) -> Tuple[int, float]:
        from app.infrastructure.transcoder.dmd_converter_adapter import merge_rgb565_streams

        return merge_rgb565_streams(list(stream_paths), out_path, width, height, target_fps)


pil_card_preview_renderer = PilCardPreviewRendererAdapter()
