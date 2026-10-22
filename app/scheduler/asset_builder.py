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

import io
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

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
install_ipv4_preference()

logger = logging.getLogger("dmd_showcase.converter")


def _apply_worker_nice(nice_inc: int = 10) -> None:
    install_ipv4_preference()
    if nice_inc > 0 and hasattr(os, "nice"):
        try:
            current = os.nice(0)
            if current < nice_inc:
                os.nice(nice_inc - current)
        except Exception:
            pass
    try:
        cv2.setNumThreads(1)
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
                data = resp.read(20 * 1024 * 1024)
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
) -> bool:
    """Slice a clean HD (`720p`) segment directly over HTTP using FFmpeg."""
    return slice_hd_remote_video_ffmpeg(
        video_url=video_url,
        out_mp4=out_mp4,
        max_duration_s=max_duration_s,
        start_offset_s=start_offset_s,
    )


def _extract_video_clip_ytdlp(
    video_page_url: Optional[str],
    out_mp4: Path,
    max_duration_s: float = 10.0,
) -> bool:
    """Extract a clean HD (`720p`/`1080p`) MP4 clip from YouTube using `yt-dlp`."""
    return extract_hd_youtube_clip_ytdlp(
        video_page_url=video_page_url,
        out_mp4=out_mp4,
        max_duration_s=max_duration_s,
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
    color_boost: bool = True,
    poster_w: int = 32,
    poster_h: int = 32,
    jacket_url: Optional[str] = None,
    jacket_panorama: bool = True,
    marquee_panorama: bool = False,
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

    # 1. Mini-Jacket on Info Card (`poster_w x poster_h`)
    poster_png_name = f"poster_{poster_w}x{poster_h}.png"
    poster_rgb_name = f"poster_{poster_w}x{poster_h}.rgb565"
    poster_bytes = _download_bytes(poster_url or jacket_url, timeout=4.5)

    poster_img: Optional[Image.Image] = None
    if poster_bytes:
        try:
            with Image.open(io.BytesIO(poster_bytes)) as src_p:
                poster_img = _fit_poster_preserving_cover(
                    src_p, poster_w, poster_h, palette, color_boost=color_boost
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
            smart_color_boost=color_boost,
            stem="marquee",
        )
    except Exception:
        _generate_fallback_artwork(raw_marquee, width * 2, height * 2, title, badge, palette)
        processor.process_poster(
            raw_marquee,
            out_dir,
            width=width,
            height=height,
            smart_color_boost=color_boost,
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
                duration_s=3.8,
                color_boost=color_boost,
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
                duration_s=3.6,
                color_boost=color_boost,
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
                duration_s=3.8,
                color_boost=color_boost,
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
            smart_color_boost=color_boost,
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
    color_boost: bool = True,
    nice_inc: int = 10,
) -> Dict[str, object]:
    """
    Top-level worker function executed in the bounded ProcessPoolExecutor.
    1. If `video_url` is a large `.webm`/`.mp4` stream (e.g. AnimeThemes), slices a short
       segment directly over HTTP via `ffmpeg -ss 10 -t <dur> -i <url>`.
    2. Otherwise downloads `video_url` directly (e.g. ScreenScraper or ArcadeItalia shortplay MP4).
    3. If unavailable, extracts from `youtube_url` (Dailymotion / YouTube) via `yt-dlp --force-ipv4`.
    4. If neither is available, returns `has_video=False` immediately (NO synthetic grid+ball!).
    """
    _apply_worker_nice(nice_inc)
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_video_path = out_dir / "src_trailer.mp4"

    have_real_video = False
    if video_url:
        if video_url.endswith(".webm") or "animethemes.moe" in video_url:
            have_real_video = _slice_remote_video_ffmpeg(
                video_url, src_video_path, max_duration_s=max_duration_s, start_offset_s=12.0
            )
        if not have_real_video:
            vid_bytes = _download_bytes(video_url, timeout=8.0)
            if vid_bytes and len(vid_bytes) > 8192:
                src_video_path.write_bytes(vid_bytes)
                have_real_video = True

    if not have_real_video and youtube_url:
        have_real_video = _extract_video_clip_ytdlp(
            youtube_url, src_video_path, max_duration_s=max_duration_s
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

    scroll_on_video = video_mode in ("panorama", "pixel_art")
    use_auto_action = video_mode == "auto_action"
    preset_mode = video_mode if video_mode in ("pixel_art", "anime", "cinema") else "pixel_art"

    clip_cfg = DmdClipConfig(
        width=width,
        height=height,
        fps=fps,
        max_duration_s=max_duration_s,
        mode=preset_mode,
        use_ai_moments=True,
        use_auto_action=use_auto_action,
        use_smart_color_boost=color_boost,
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