"""
Isolated worker functions that build DMD-ready mini-jackets (.png, .rgb565),
granular top-to-bottom scrolling panorama streams (`jacket_panorama`,
`marquee_panorama`, `snapshot_panorama_count` via `dmd_gif_converter`), and
real language-agnostic video clips (.gif, .rgb565.stream).

Rules enforced:
- ZERO synthetic grid+ball fallback: if no real video is available (direct MP4/WebM
  or Dailymotion/YouTube via `yt-dlp`), `has_video` is set to `False` so the DMD
  skips the video phase cleanly and advances to the next item.
- Supports adaptive multi-resolution (`256x64`, `128x64`, `128x32`, `64x64`) and
  vertical TATE (`64x128`, `64x256`, `32x128`) geometries.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, Union
from urllib.request import Request, urlopen

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from app.infrastructure.scrapers.youtube_resolver import (
    extract_hd_youtube_clip_ytdlp,
    slice_hd_remote_video_ffmpeg,
)
from app.providers.base import install_ipv4_preference
from dmd_gif_converter import (
    DmdClipConfig,
    DmdMediaProcessor,
    extract_frames_from_gif,
    rgb888_to_rgb565_le,
    write_rgb565_stream,
)
from src.engine.conversion.core import DEFAULT_PARAMS, process_file

DEFAULT_PARAMS["font_name"] = None
DEFAULT_PARAMS["text_overlay_enabled"] = False
DEFAULT_PARAMS["text_font_file"] = __file__
install_ipv4_preference()

logger = logging.getLogger("dmd_showcase.converter")


def _apply_worker_nice(nice_inc: int = 10, ffmpeg_threads: int = 1) -> None:
    install_ipv4_preference()
    eff_threads = max(1, min(8, int(ffmpeg_threads)))
    os.environ["OMP_NUM_THREADS"] = str(eff_threads)
    if nice_inc > 0 and hasattr(os, "nice"):
        try:
            current = os.nice(0)
            if current < nice_inc:
                os.nice(nice_inc - current)
        except Exception:
            pass
    try:
        cv2.setNumThreads(eff_threads)
    except Exception:
        pass


def _download_bytes(url: Optional[str], timeout: float = 6.0) -> Optional[bytes]:
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 dmd-media-showcase/2.1"
                )
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = resp.read(80 * 1024 * 1024)
                if len(data) < 128:
                    logger.info(
                        "[CONVERTER] Ignored empty/truncated payload (%d bytes) from %s",
                        len(data),
                        url,
                    )
                    return None
                logger.info(
                    "[CONVERTER] Downloaded %.1f KB from %s",
                    len(data) / 1024.0,
                    url,
                )
                return data
    except Exception as exc:
        logger.info("[CONVERTER] Download failed for %s: %s", url, exc)
    return None


def _slice_remote_video_ffmpeg(
    video_url: Optional[str],
    out_mp4: Path,
    max_duration_s: float = 10.0,
    start_offset_s: float = 10.0,
    ffmpeg_threads: int = 1,
) -> bool:
    """Slice a clean HD (`720p`) segment directly over HTTP using FFmpeg."""
    return slice_hd_remote_video_ffmpeg(
        video_url=video_url,
        out_mp4=out_mp4,
        max_duration_s=max_duration_s,
        start_offset_s=start_offset_s,
        ffmpeg_threads=ffmpeg_threads,
    )


def _extract_video_clip_ytdlp(
    video_page_url: Optional[str],
    out_mp4: Path,
    max_duration_s: float = 10.0,
    max_filesize_mb: int = 60,
) -> bool:
    """Extract a clean HD (`720p`/`1080p`) MP4 clip from YouTube using `yt-dlp`."""
    return extract_hd_youtube_clip_ytdlp(
        video_page_url=video_page_url,
        out_mp4=out_mp4,
        max_duration_s=max_duration_s,
        max_filesize_mb=max_filesize_mb,
    )


def _generate_fallback_artwork(
    path: Path,
    width: int,
    height: int,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
) -> Path:
    """
    Generate clean LED-friendly badge artwork when a remote poster image is offline.
    """
    r0, g0, b0 = palette
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        factor = 0.30 + 0.70 * (1.0 - y / max(1, height - 1))
        arr[y, :, 0] = min(255, int(r0 * factor))
        arr[y, :, 1] = min(255, int(g0 * factor))
        arr[y, :, 2] = min(255, int(b0 * factor))

    img = Image.fromarray(arr, mode="RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(255, 255, 255), width=1)
    short_label = (badge[:6] if width <= 40 else f"{badge}: {title[:16]}").upper()
    draw.text((3, max(2, height // 2 - 5)), short_label, fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")
    return path


def _fit_poster_preserving_cover(
    img: Image.Image,
    width: int,
    height: int,
    palette: Tuple[int, int, int],
    color_boost: bool = True,
) -> Image.Image:
    """
    Resize a boxart/flyer/cover to `(width, height)` (e.g. `32x32`, `48x64`, `40x40`, `24x24`)
    without cropping away half of rectangular covers.
    """
    rgb = img.convert("RGB")
    src_w, src_h = rgb.size
    ratio = src_w / max(1, src_h)
    target_ratio = width / max(1, height)

    if abs(ratio - target_ratio) <= 0.35:
        canvas = ImageOps.fit(
            rgb,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.4),
        )
    else:
        bg_r, bg_g, bg_b = (palette[0] // 6, palette[1] // 6, palette[2] // 6)
        canvas = Image.new("RGB", (width, height), (bg_r, bg_g, bg_b))
        contained = ImageOps.contain(rgb, (width, height), method=Image.Resampling.LANCZOS)
        ox = (width - contained.width) // 2
        oy = (height - contained.height) // 2
        canvas.paste(contained, (ox, oy))

    if color_boost:
        canvas = ImageEnhance.Contrast(canvas).enhance(1.22)
        canvas = ImageEnhance.Color(canvas).enhance(1.30)
        canvas = ImageEnhance.Sharpness(canvas).enhance(1.15)
    return canvas


def _convert_image_to_panorama_gif_and_stream(
    raw_image_bytes: bytes,
    out_dir: Path,
    stem: str,
    width: int,
    height: int,
    fps: int = 20,
    duration_s: float = 4.0,
    color_boost: bool = True,
) -> Optional[Tuple[str, str, str]]:
    """
    Convert a static image (jacket/boxart/poster, marquee/banner, or snapshot/still) into:
    1. A smooth sub-pixel top-to-bottom scrolling `.gif` (`{stem}_{width}x{height}.gif`)
    2. A binary `.rgb565.stream` (`{stem}_{width}x{height}.rgb565.stream`)
    3. A tall `width x (height*2+8)` `.rgb565` static pan surface
    using `DmdMediaProcessor.process_image_panorama()`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pan_h = height * 2 + 8
    src_img_path = out_dir / f"_src_{stem}.png"

    processor = DmdMediaProcessor()
    try:
        with Image.open(io.BytesIO(raw_image_bytes)) as pil_src:
            pil_src.convert("RGB").save(src_img_path, format="PNG")

        _, static_rgb565_path = processor.process_pan_backdrop(
            input_image=src_img_path,
            output_dir=out_dir,
            width=width,
            height=pan_h,
            smart_color_boost=color_boost,
            stem=stem,
        )
        res = processor.process_image_panorama(
            input_image=src_img_path,
            output_dir=out_dir,
            width=width,
            height=height,
            fps=max(15, fps),
            duration_s=duration_s,
            smart_color_boost=color_boost,
            export_gif=True,
            export_rgb565_stream=True,
            stem=stem,
        )
        src_img_path.unlink(missing_ok=True)
        if res.gif_path and res.rgb565_stream_path and res.frame_count > 0:
            logger.info(
                "[CONVERTER] Built panorama '%s' via dmd_gif_converter pipeline (%dx%d, %d frames @ %dfps)",
                stem,
                width,
                height,
                res.frame_count,
                res.fps,
            )
            return res.gif_path.name, res.rgb565_stream_path.name, static_rgb565_path.name
        return None
    except Exception as exc:
        src_img_path.unlink(missing_ok=True)
        logger.info("[CONVERTER] Panorama conversion error for %s: %s", stem, exc)
        return None


def resolve_framing_and_color_params(
    video_mode: str = "auto_action",
    color_boost: Union[str, bool] = "auto",
) -> Tuple[bool, bool, bool, Literal["pixel_art", "anime", "cinema"]]:
    """
    Resolve orthogonal framing (`video_mode`) and LED colorimetry (`color_boost`) settings:
      Returns `(use_auto_action, scroll_on_video, use_smart_color_boost, preset_mode)`:
      - `video_mode` controls framing:
        * `'auto_action'` (default): Let Me Handle It AI (`use_auto_action=True`, `scroll_on_video=False`)
        * `'panorama'`: top-to-bottom vertical scan (`use_auto_action=False`, `scroll_on_video=True`)
        * `'center_crop'`: static center crop (`use_auto_action=False`, `scroll_on_video=False`)
      - `color_boost` controls LED colorimetry:
        * `'auto'` / `True` (default): Smart Auto-Color AI (`use_smart_color_boost=True`)
        * `'pixel_art'`: fixed Pixel Art preset (`use_smart_color_boost=False`, `preset_mode='pixel_art'`)
        * `'anime'`: fixed Anime preset (`use_smart_color_boost=False`, `preset_mode='anime'`)
        * `'cinema'`: fixed Cinema preset (`use_smart_color_boost=False`, `preset_mode='cinema'`)
    """
    vm = str(video_mode or "auto_action").strip().lower()
    scroll_on_video = vm == "panorama"
    use_auto_action = vm not in ("panorama", "center_crop")

    if isinstance(color_boost, bool):
        cb = "auto" if color_boost else "cinema"
    else:
        cb = str(color_boost or "auto").strip().lower()

    if cb in ("pixel_art", "anime", "cinema"):
        preset_mode: Literal["pixel_art", "anime", "cinema"] = cb  # type: ignore[assignment]
        use_smart_color_boost = False
    elif cb in ("false", "0", "no", "off", "none"):
        preset_mode = "cinema"
        use_smart_color_boost = False
    else:
        # 'auto' / 'true' / default: Smart Auto-Color AI
        if vm in ("pixel_art", "anime", "cinema"):
            preset_mode = vm  # type: ignore[assignment]
        else:
            preset_mode = "pixel_art"
        use_smart_color_boost = True

    return use_auto_action, scroll_on_video, use_smart_color_boost, preset_mode


def build_static_assets_sync(
    item_dir: str,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
    poster_url: Optional[str],
    marquee_url: Optional[str],
    backdrop_url: Optional[str],
    image_urls: Optional[List[str]] = None,
    image_count: int = 0,
    width: int = 128,
    height: int = 32,
    fps: int = 20,
    color_boost: Union[str, bool] = "auto",
    poster_w: int = 32,
    poster_h: int = 32,
    jacket_url: Optional[str] = None,
    jacket_panorama: bool = True,
    marquee_panorama: bool = False,
    panorama_duration_s: float = 4.0,
) -> Dict[str, object]:
    """
    Build:
    1. Mini-Jacket for the Info Card (`poster_{poster_w}x{poster_h}.png` & `.rgb565`),
    2. Static Marquee (`marquee_{width}x{height}.png` & `.rgb565`),
    3. Granular full-screen top-to-bottom scrolling panoramas:
       - `jacket_stream_rgb565` (when `jacket_panorama=True`, default `True`)
       - `marquee_stream_rgb565` (when `marquee_panorama=True`, default `False`)
       - `snapshot_streams_rgb565` (`0..image_count` screenshots/stills)
    """
    t0 = time.monotonic()
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    processor = DmdMediaProcessor()
    color_boost_bool = not (
        color_boost is False
        or str(color_boost).strip().lower() in ("false", "0", "no", "off", "none")
    )

    # 1. Mini-Jacket on Info Card (`poster_w x poster_h`)
    poster_png_name = f"poster_{poster_w}x{poster_h}.png"
    poster_rgb_name = f"poster_{poster_w}x{poster_h}.rgb565"
    poster_bytes = _download_bytes(poster_url or jacket_url, timeout=4.5)
    if not poster_bytes and marquee_url and marquee_url != poster_url:
        poster_bytes = _download_bytes(marquee_url, timeout=4.5)
    if not poster_bytes and image_urls:
        poster_bytes = _download_bytes(image_urls[0], timeout=4.5)

    poster_img: Optional[Image.Image] = None
    if poster_bytes:
        try:
            with Image.open(io.BytesIO(poster_bytes)) as src_p:
                poster_img = _fit_poster_preserving_cover(
                    src_p, poster_w, poster_h, palette, color_boost=color_boost_bool
                )
        except Exception as exc:
            logger.info("[CONVERTER] Poster decode failed for '%s': %s", title, exc)

    if poster_img is None:
        raw_poster = out_dir / "src_poster.png"
        _generate_fallback_artwork(raw_poster, poster_w, poster_h, title, badge, palette)
        with Image.open(raw_poster) as src_p:
            poster_img = src_p.convert("RGB")
        raw_poster.unlink(missing_ok=True)

    poster_img.save(out_dir / poster_png_name, format="PNG", optimize=True)
    (out_dir / poster_rgb_name).write_bytes(
        rgb888_to_rgb565_le(np.asarray(poster_img, dtype=np.uint8))
    )

    # 2. Static Marquee / Banner (`width x height`)
    raw_marquee = out_dir / "src_marquee.img"
    marquee_bytes = _download_bytes(marquee_url or backdrop_url, timeout=4.0)
    if marquee_bytes:
        raw_marquee.write_bytes(marquee_bytes)
    else:
        _generate_fallback_artwork(raw_marquee, width * 2, height * 2, title, badge, palette)

    try:
        processor.process_poster(
            raw_marquee,
            out_dir,
            width=width,
            height=height,
            smart_color_boost=color_boost_bool,
            stem="marquee",
        )
    except Exception:
        _generate_fallback_artwork(raw_marquee, width * 2, height * 2, title, badge, palette)
        processor.process_poster(
            raw_marquee,
            out_dir,
            width=width,
            height=height,
            smart_color_boost=color_boost_bool,
            stem="marquee",
        )
    raw_marquee.unlink(missing_ok=True)

    # 3. Granular Top-to-Bottom Scrolling Panoramas (`Jacket` -> `Marquee` -> `Snapshots`)
    pan_h = height * 2 + 8
    panorama_gifs: List[str] = []
    panorama_streams: List[str] = []
    snapshots_rgb565: List[str] = []

    jacket_gif_name = ""
    jacket_stream_name = ""
    marquee_gif_name = ""
    marquee_stream_name = ""
    snapshot_gif_names: List[str] = []
    snapshot_stream_names: List[str] = []

    pan_dur = max(1.5, min(15.0, float(panorama_duration_s)))

    # 3a. Jacket Panorama (boolean default True)
    if jacket_panorama:
        j_bytes = poster_bytes or _download_bytes(jacket_url or poster_url, timeout=4.0)
        if j_bytes:
            j_res = _convert_image_to_panorama_gif_and_stream(
                raw_image_bytes=j_bytes,
                out_dir=out_dir,
                stem="pan_jacket",
                width=width,
                height=height,
                fps=max(15, fps),
                duration_s=pan_dur,
                color_boost=color_boost_bool,
            )
            if j_res:
                jacket_gif_name, jacket_stream_name, j_static_rgb = j_res
                panorama_gifs.append(jacket_gif_name)
                panorama_streams.append(jacket_stream_name)
                snapshots_rgb565.append(j_static_rgb)

    # 3b. Marquee Panorama (boolean default False)
    if marquee_panorama:
        m_bytes = marquee_bytes or _download_bytes(marquee_url or backdrop_url, timeout=4.0)
        if m_bytes:
            m_res = _convert_image_to_panorama_gif_and_stream(
                raw_image_bytes=m_bytes,
                out_dir=out_dir,
                stem="pan_marquee",
                width=width,
                height=height,
                fps=max(15, fps),
                duration_s=pan_dur,
                color_boost=color_boost_bool,
            )
            if m_res:
                marquee_gif_name, marquee_stream_name, m_static_rgb = m_res
                panorama_gifs.append(marquee_gif_name)
                panorama_streams.append(marquee_stream_name)
                snapshots_rgb565.append(m_static_rgb)

    # 3c. Snapshot Panoramas (`0..5`, default `0`)
    target_snap_count = max(0, min(5, int(image_count)))
    if target_snap_count > 0:
        candidate_urls: List[str] = []
        for u in (image_urls or []):
            if u and u not in candidate_urls and u != poster_url:
                candidate_urls.append(u)
        if backdrop_url and backdrop_url not in candidate_urls and backdrop_url != poster_url:
            candidate_urls.insert(0, backdrop_url)

        for url in candidate_urls:
            if len(snapshot_stream_names) >= target_snap_count:
                break
            img_bytes = _download_bytes(url, timeout=4.0)
            if not img_bytes:
                continue
            idx = len(snapshot_stream_names)
            pan_res = _convert_image_to_panorama_gif_and_stream(
                raw_image_bytes=img_bytes,
                out_dir=out_dir,
                stem=f"pan_snap_{idx}",
                width=width,
                height=height,
                fps=max(15, fps),
                duration_s=pan_dur,
                color_boost=color_boost_bool,
            )
            if pan_res:
                gif_f, stream_f, static_rgb_f = pan_res
                snapshot_gif_names.append(gif_f)
                snapshot_stream_names.append(stream_f)
                panorama_gifs.append(gif_f)
                panorama_streams.append(stream_f)
                snapshots_rgb565.append(static_rgb_f)

    # Ensure legacy `snap_{width}x{pan_h}.png` and `.rgb565` exist for backward compatibility
    snap_png_default = f"snap_{width}x{pan_h}.png"
    snap_rgb_default = f"snap_{width}x{pan_h}.rgb565"
    if snapshots_rgb565:
        first_static_rgb = out_dir / snapshots_rgb565[0]
        first_static_png = out_dir / snapshots_rgb565[0].replace(".rgb565", ".png")
        if first_static_rgb.exists():
            (out_dir / snap_rgb_default).write_bytes(first_static_rgb.read_bytes())
        if first_static_png.exists():
            (out_dir / snap_png_default).write_bytes(first_static_png.read_bytes())
    else:
        raw_snap = out_dir / "src_snap.png"
        _generate_fallback_artwork(raw_snap, width, pan_h, title, badge, palette)
        processor.process_pan_backdrop(
            raw_snap,
            out_dir,
            width=width,
            height=pan_h,
            smart_color_boost=color_boost_bool,
            stem="snap",
        )
        raw_snap.unlink(missing_ok=True)

    logger.info(
        "[CONVERTER] Built static + panoramas (jacket=%s, marquee=%s, snaps=%d/%d) for '%s' in %.2fs",
        bool(jacket_stream_name),
        bool(marquee_stream_name),
        len(snapshot_stream_names),
        target_snap_count,
        title,
        time.monotonic() - t0,
    )

    return {
        "poster_png": poster_png_name,
        "poster_rgb565": poster_rgb_name,
        "poster_width": poster_w,
        "poster_height": poster_h,
        "marquee_png": f"marquee_{width}x{height}.png",
        "marquee_rgb565": f"marquee_{width}x{height}.rgb565",
        "snapshot_pan_png": snap_png_default,
        "snapshot_pan_rgb565": snap_rgb_default,
        "jacket_gif": jacket_gif_name,
        "jacket_stream_rgb565": jacket_stream_name,
        "marquee_gif": marquee_gif_name,
        "marquee_stream_rgb565": marquee_stream_name,
        "snapshot_gifs": snapshot_gif_names,
        "snapshot_streams_rgb565": snapshot_stream_names,
        "panorama_gifs": panorama_gifs,
        "panorama_streams_rgb565": panorama_streams,
        "snapshots_pan_rgb565": snapshots_rgb565,
    }


def _parse_hex_rgb(hex_color: str, fallback: Tuple[int, int, int] = (230, 74, 25)) -> Tuple[int, int, int]:
    raw = str(hex_color or "").strip().lstrip("#")
    if len(raw) == 6:
        try:
            return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        except ValueError:
            pass
    return fallback


def _sanitize_card_text(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u0153": "oe",
        "\u0152": "OE",
        "\u00a0": " ",
        "\n": " ",
        "\r": " ",
    }
    cleaned = str(text)
    for k, v in replacements.items():
        cleaned = cleaned.replace(k, v)
    return " ".join(cleaned.split())


def _load_card_font(
    text_sample: str,
    size: int = 10,
    font_style: str = "arcade_pixel",
) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    needs_unicode = any(ord(ch) > 255 for ch in text_sample)
    style = str(font_style or "arcade_pixel").strip().lower()
    if needs_unicode:
        candidates: Tuple[str, ...] = (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        )
    elif style == "clean_sans":
        candidates = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        )
    else:
        return ImageFont.load_default()

    for fp in candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _resolve_scroll_px_per_sec(speed: str) -> float:
    raw = str(speed or "normal").strip().lower()
    named = {"slow": 22.0, "normal": 34.0, "fast": 48.0, "turbo": 64.0}
    if raw in named:
        return named[raw]
    try:
        lvl = max(1, min(10, int(raw)))
        return 16.0 + lvl * 5.0
    except ValueError:
        return 34.0


def _apply_amber_dmd_palette(arr: np.ndarray) -> np.ndarray:
    """Map an RGB888 frame to classic Williams/Bally/Stern #FF7300 plasma dot-matrix amber."""
    f = arr.astype(np.float32)
    lum = 0.299 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.114 * f[:, :, 2]
    out = np.empty_like(arr, dtype=np.uint8)
    out[:, :, 0] = np.clip(lum * 1.08, 0, 255).astype(np.uint8)
    out[:, :, 1] = np.clip(lum * 0.46, 0, 255).astype(np.uint8)
    out[:, :, 2] = np.clip(lum * 0.04, 0, 255).astype(np.uint8)
    return out


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
    """
    Render the Phase 1 Info Card directly into a `DMDS` `.rgb565.stream` file
    (`card_intro_{W}x{H}.rgb565.stream`) for Mode 2B passive UDP receivers and Studio previews.
    Supports 5 pixel-perfect layouts (with automatic YOKO & TATE reflow), 5 themes (including
    `amber_dmd` pinball plasma), crisp bitmap / CJK typography, and custom hold/scroll speeds.
    """
    if not include_card_intro:
        return ""

    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    eff_fps = max(8, min(60, int(fps)))

    layout = str(card_layout or "classic_split").strip().lower()
    if layout not in ("classic_split", "poster_right", "badge_header", "hero_banner", "minimal_arcade"):
        layout = "classic_split"
    theme = str(card_theme or "tinted").strip().lower()
    if theme not in ("tinted", "pure_black", "neon_border", "poster_wash", "amber_dmd"):
        theme = "tinted"
    font_style = str(card_font or "arcade_pixel").strip().lower()

    accent_hex = (
        card_accent_color
        if (card_accent_color and str(card_accent_color).strip().lower() not in ("auto", ""))
        else badge_color
    )
    accent = _parse_hex_rgb(accent_hex)
    if theme == "amber_dmd":
        accent = (255, 115, 0)

    clean_title = _sanitize_card_text(title) or "SHOWCASE"
    if layout == "minimal_arcade":
        clean_title = clean_title.upper()
    clean_badge = (_sanitize_card_text(badge) or "DMD").upper()[:14] if show_badge else ""
    raw_year = _sanitize_card_text(year) if show_year else ""
    if len(raw_year) > 4 and raw_year[:4].isdigit() and raw_year[4] in "-/. ":
        clean_year = raw_year[:4]
    else:
        clean_year = raw_year[:6]
    raw_rating = _sanitize_card_text(rating).lstrip("*★ ").strip() if show_rating else ""
    clean_rating = raw_rating[:8]

    if layout == "minimal_arcade" or not include_synopsis:
        clean_body = ""
    else:
        clean_body = _sanitize_card_text(synopsis or subtitle)

    font_px_size = 9 if height < 48 else (11 if height >= 64 else 10)
    font = _load_card_font(f"{clean_title} {clean_body}", size=font_px_size, font_style=font_style)
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

    poster_img: Optional[Image.Image] = raw_poster_img.copy() if (include_poster and raw_poster_img is not None) else None
    is_tate = height > width

    # Prepare base panel background (`tinted`, `pure_black`, `poster_wash`, `hero_banner`, `amber_dmd`)
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

    # Compute layout coordinates for YOKO & TATE across all 5 presets
    header_bar_h = 0
    sep_line: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None
    poster_x, poster_y = 0, 0

    if layout == "hero_banner":
        poster_img = None
        text_x = 3
        text_w = max(16, width - 6)
        if is_tate:
            top_y = 4
            title_y = 18
            body_y = 33
        elif height >= 64:
            top_y = 5
            title_y = 22
            body_y = 41
        else:
            top_y = 1
            title_y = 11
            body_y = 21
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
                top_y = -100  # Rendered in top header bar
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
                top_y = 3
                title_y = 16
                body_y = 29 if layout != "minimal_arcade" else -100
            else:
                poster_y = 2
                sep_y = poster_y + poster_img.height + 1
                sep_line = ((2, sep_y), (width - 3, sep_y))
                top_y = poster_img.height + 4
                title_y = top_y + 13
                body_y = title_y + 13 if layout != "minimal_arcade" else -100
        else:
            top_y = 4
            title_y = 18
            body_y = 32 if layout != "minimal_arcade" else -100
    else:
        # Landscape YOKO: `classic_split`, `poster_right`, `minimal_arcade`
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
            top_y = 4
            title_y = 21
            body_y = 39
        else:
            top_y = 1
            title_y = 11
            body_y = 20 if theme == "neon_border" else 21

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
    px_per_sec = _resolve_scroll_px_per_sec(text_scroll_speed)
    px_per_frame = max(0.75, px_per_sec / float(eff_fps))

    meta_bits = " ".join(b for b in (clean_year, f"*{clean_rating}" if clean_rating else "") if b)
    raw_badge_w = (_measure_w(clean_badge) + 6) if clean_badge else 0
    meta_w = _measure_w(meta_bits) if meta_bits else 0

    if header_bar_h > 0:
        banner_container_w = max(8, width - (2 if theme == "neon_border" else 1))
        banner_align_meta_right = True
    elif top_y >= 0:
        banner_container_w = max(8, text_w)
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
        (title_px - text_w + 16) if title_px > text_w else 0,
        (body_px + text_w // 2) if (clean_body and body_y >= 0 and body_px > 0) else 0,
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

        # `scroll_full`: scroll `[BADGE] + gap + meta_bits` seamlessly across narrow viewports
        full_x = max(box_x, 1 if (header_bar_h > 0 and theme == "neon_border") else box_x)
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
                fdraw.rectangle(
                    [cur_x, rel_y, cur_x + raw_badge_w - 1, rel_y + pill_h - 1],
                    fill=accent,
                )
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

        scroll_px = 0
        if idx > hold_intro_frames:
            scroll_px = int(round((idx - hold_intro_frames) * px_per_frame))
        banner_scroll_px = (
            int(round((idx - banner_hold_frames) * px_per_frame))
            if idx > banner_hold_frames
            else 0
        )

        # Full-width top header bar for `badge_header` layout
        if header_bar_h > 0:
            draw.rectangle(
                [0, 0, width - 1, header_bar_h - 1],
                fill=(max(12, accent[0] // 3), max(12, accent[1] // 3), max(14, accent[2] // 3)),
            )
            _render_banner_region(
                canvas,
                box_x=0,
                box_y=0,
                box_w=banner_container_w,
                pill_h=header_bar_h,
                text_dy=0 if header_bar_h <= 10 else 1,
                b_scroll_px=banner_scroll_px,
            )

        # Render text region on a clipped sub-strip preserving any poster_wash background underneath
        strip = canvas.crop((text_x, 0, text_x + text_w, height))
        sdraw = ImageDraw.Draw(strip)
        if font_style == "arcade_pixel":
            sdraw.fontmode = "1"

        if top_y >= 0:
            _render_banner_region(
                strip,
                box_x=0,
                box_y=top_y,
                box_w=banner_container_w,
                pill_h=10,
                text_dy=-1,
                b_scroll_px=banner_scroll_px,
            )

        # Title line (static if fits, smooth horizontal scroll otherwise)
        if title_y >= 0 and title_y + 7 <= height:
            if title_px <= text_w:
                tx = 0
            else:
                max_t_shift = title_px - text_w + 10
                tx = -min(max_t_shift, scroll_px)
            sdraw.text((tx, title_y), clean_title, fill=(255, 255, 255), font=font)

        # Localized Synopsis / Subtitle ticker
        if clean_body and body_y >= 0 and body_y + 7 <= height:
            if body_px <= text_w:
                bx = 0
            else:
                bx = max(8, text_w // 3) - scroll_px
            sdraw.text((bx, body_y), clean_body, fill=(190, 225, 255), font=font)

        canvas.paste(strip, (text_x, 0))

        if poster_img is not None:
            canvas.paste(poster_img, (poster_x, poster_y))
        if sep_line is not None:
            draw.line(
                [sep_line[0], sep_line[1]],
                fill=accent if layout == "minimal_arcade" else (accent[0] // 2, accent[1] // 2, accent[2] // 2),
            )
        if theme == "neon_border":
            draw.rectangle([0, 0, width - 1, height - 1], outline=accent)

        arr = np.asarray(canvas, dtype=np.uint8)
        if theme == "amber_dmd":
            arr = _apply_amber_dmd_palette(arr)
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
    """
    Fast (< 100 ms) Card-only `DMDS` preview builder for the WebUI Studio:
    Caches raw downloaded `poster_url` bytes on disk so subsequent layout/theme/TATE/speed
    tweaks execute in pure in-memory Pillow/NumPy with zero network or FFmpeg latency.
    Returns `(dmds_bytes, frame_count, eff_fps, duration_s)`.
    """
    eff_fps = max(8, min(60, int(fps)))
    palette = _parse_hex_rgb(
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
            raw_bytes = _download_bytes(poster_url, timeout=3.5)
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
                    poster_img = _fit_poster_preserving_cover(
                        src_p, poster_w, poster_h, palette, color_boost=color_boost_bool
                    )
            except Exception:
                poster_img = None
        if poster_img is None:
            _generate_fallback_artwork(
                tmp_path / poster_png_name, poster_w, poster_h, title or "DMD", badge or "DMD", palette
            )
        else:
            poster_img.save(tmp_path / poster_png_name, format="PNG")

        if not include_card_intro:
            # Render a minimal 1-second dark placeholder frame when Card Intro is toggled OFF in Card-only mode
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


def build_video_assets_worker_task(
    item_dir: str,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
    video_url: Optional[str],
    youtube_url: Optional[str] = None,
    width: int = 128,
    height: int = 32,
    fps: int = 15,
    max_duration_s: float = 8.0,
    video_mode: str = "auto_action",
    color_boost: Union[str, bool] = "auto",
    nice_inc: int = 10,
    ffmpeg_threads: int = 1,
    ytdlp_max_filesize_mb: int = 60,
) -> Dict[str, object]:
    """
    Top-level worker function executed in the bounded ProcessPoolExecutor.
    1. If `video_url` is a large `.webm`/`.mp4` stream (e.g. AnimeThemes), slices a short
       segment directly over HTTP via `ffmpeg -ss 10 -t <dur> -i <url>`.
    2. Otherwise downloads `video_url` directly (e.g. ScreenScraper or ArcadeItalia shortplay MP4).
    3. If unavailable, extracts from `youtube_url` (Dailymotion / YouTube) via `yt-dlp --force-ipv4`.
    4. If neither is available, returns `has_video=False` immediately (NO synthetic grid+ball!).
    """
    _apply_worker_nice(nice_inc, ffmpeg_threads=ffmpeg_threads)
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_video_path = out_dir / "src_trailer.mp4"

    have_real_video = False
    if video_url:
        if (
            video_url.endswith((".webm", ".m4v"))
            or "animethemes.moe" in video_url
            or "itunes.apple.com" in video_url
            or "mzstatic.com" in video_url
            or "archive.org/download/" in video_url
        ):
            if "archive.org/download/" in video_url:
                offset_s = 4.0
            elif (
                "itunes.apple.com" in video_url
                or "mzstatic.com" in video_url
                or video_url.endswith(".m4v")
            ):
                offset_s = 8.0
            else:
                offset_s = 12.0
            have_real_video = _slice_remote_video_ffmpeg(
                video_url,
                src_video_path,
                max_duration_s=max_duration_s,
                start_offset_s=offset_s,
                ffmpeg_threads=ffmpeg_threads,
            )
        if not have_real_video:
            vid_bytes = _download_bytes(video_url, timeout=12.0)
            if vid_bytes and len(vid_bytes) > 8192:
                src_video_path.write_bytes(vid_bytes)
                have_real_video = True

    if not have_real_video and youtube_url:
        have_real_video = _extract_video_clip_ytdlp(
            youtube_url,
            src_video_path,
            max_duration_s=max_duration_s,
            max_filesize_mb=ytdlp_max_filesize_mb,
        )

    if not have_real_video:
        src_video_path.unlink(missing_ok=True)
        logger.info(
            "[CONVERTER] No video available for '%s' (video_url=%s, youtube_url=%s) -> skipping video phase",
            title,
            video_url,
            youtube_url,
        )
        return {
            "has_video": False,
            "frame_count": 0,
            "fps": fps,
            "duration_s": 0.0,
            "quality_score": 0,
            "gif_file": "",
            "stream_file": "",
        }

    use_auto_action, scroll_on_video, use_smart_color_boost, preset_mode = (
        resolve_framing_and_color_params(video_mode=video_mode, color_boost=color_boost)
    )

    clip_cfg = DmdClipConfig(
        width=width,
        height=height,
        fps=fps,
        max_duration_s=max_duration_s,
        mode=preset_mode,
        use_ai_moments=True,
        use_auto_action=use_auto_action,
        use_smart_color_boost=use_smart_color_boost,
        scroll_enabled=scroll_on_video,
    )

    processor = DmdMediaProcessor()
    stem = f"video_{width}x{height}"
    t0 = time.monotonic()
    try:
        res = processor.process_video_trailer(
            input_video=src_video_path,
            output_dir=out_dir,
            config=clip_cfg,
            export_gif=True,
            export_rgb565_stream=True,
            stem=stem,
        )
        logger.info(
            "[CONVERTER] Encoded real video for '%s' -> %dx%d @ %dfps | frames=%d (%.1fs) | score=%d | took %.2fs",
            title,
            width,
            height,
            res.fps,
            res.frame_count,
            res.duration_s,
            res.quality_score,
            time.monotonic() - t0,
        )
        return {
            "has_video": res.frame_count > 0,
            "frame_count": res.frame_count,
            "fps": res.fps,
            "duration_s": res.duration_s,
            "quality_score": res.quality_score,
            "gif_file": f"{stem}.gif",
            "stream_file": f"{stem}.rgb565.stream",
        }
    except Exception as exc:
        logger.warning(
            "[CONVERTER] Video conversion failed for '%s' (%s) -> skipping video phase",
            title,
            exc,
        )
        return {
            "has_video": False,
            "frame_count": 0,
            "fps": fps,
            "duration_s": 0.0,
            "quality_score": 0,
            "gif_file": "",
            "stream_file": "",
        }
    finally:
        src_video_path.unlink(missing_ok=True)