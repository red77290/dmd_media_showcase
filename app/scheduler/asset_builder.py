"""
Isolated worker functions that build DMD-ready posters (.png, .rgb565),
top-to-bottom scrolling panorama images (.gif, .rgb565.stream), and real
video clips (.gif, .rgb565.stream) using `dmd_gif_converter`.

Rules enforced:
- ZERO synthetic grid+ball fallback: if no real video is available (direct MP4/WebM
  or YouTube via `yt-dlp`), `has_video` is set to `False` so the DMD skips the video
  phase cleanly and advances to the next item.
- Up to `image_count` distinct images per item are converted by `dmd_gif_converter`
  in vertical Panorama mode (`scroll_enabled=True`) scrolling smoothly from top to bottom.
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

from dmd_gif_converter import (
    DmdClipConfig,
    DmdMediaProcessor,
    extract_frames_from_gif,
    rgb888_to_rgb565_le,
    write_rgb565_stream,
)
from src.engine.conversion.core import DEFAULT_PARAMS, process_file

logger = logging.getLogger("dmd_showcase.converter")


def _apply_worker_nice(nice_inc: int = 10) -> None:
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
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 dmd-media-showcase/1.1"
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


def _extract_youtube_clip_ytdlp(
    youtube_url: Optional[str],
    out_mp4: Path,
    max_duration_s: float = 10.0,
) -> bool:
    """
    Extract a short 360p MP4 clip from a YouTube URL (e.g. ArcadeItalia `youtube_id`,
    TMDB trailer, or Jikan PV) using `yt-dlp`.
    """
    if not youtube_url or not youtube_url.startswith(("http://", "https://")):
        return False

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    out_mp4.unlink(missing_ok=True)

    sec_end = int(max(12, min(28, 8 + round(max_duration_s))))
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "--socket-timeout",
        "6",
        "--max-filesize",
        "25M",
        "-f",
        "best[ext=mp4][height<=360]/best[ext=mp4][height<=480]/best[height<=480]/best",
        "--download-sections",
        f"*6-{sec_end}",
        "-o",
        str(out_mp4),
        youtube_url,
    ]
    t0 = time.monotonic()
    try:
        logger.info("[CONVERTER] Extracting YouTube clip via yt-dlp: %s", youtube_url)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
        if res.returncode == 0 and out_mp4.exists() and out_mp4.stat().st_size > 4096:
            logger.info(
                "[CONVERTER] yt-dlp extracted %.1f KB in %.2fs from %s",
                out_mp4.stat().st_size / 1024.0,
                time.monotonic() - t0,
                youtube_url,
            )
            return True

        # Fallback without --download-sections if ffmpeg section cutting was rejected by stream
        cmd_full = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--socket-timeout",
            "6",
            "--max-filesize",
            "18M",
            "-f",
            "worst[ext=mp4][height>=240]/best[ext=mp4][height<=360]/best",
            "-o",
            str(out_mp4),
            youtube_url,
        ]
        res2 = subprocess.run(cmd_full, capture_output=True, text=True, timeout=15)
        if res2.returncode == 0 and out_mp4.exists() and out_mp4.stat().st_size > 4096:
            logger.info(
                "[CONVERTER] yt-dlp extracted full short clip (%.1f KB) in %.2fs from %s",
                out_mp4.stat().st_size / 1024.0,
                time.monotonic() - t0,
                youtube_url,
            )
            return True
    except Exception as exc:
        logger.info("[CONVERTER] yt-dlp extraction skipped for %s: %s", youtube_url, exc)
    return False


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
    Resize a boxart/flyer/cover to `(width, height)` (e.g. 32x32) without cropping
    away half of rectangular covers.
    - If aspect ratio is close to square (0.75..1.33), performs a gentle center fit.
    - Otherwise, scales the full cover onto a dark ambient background tinted with the
      cover's palette so 100% of the jacket is visible on the LED matrix.
    """
    rgb = img.convert("RGB")
    src_w, src_h = rgb.size
    ratio = src_w / max(1, src_h)

    if 0.75 <= ratio <= 1.35:
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
    fps: int = 15,
    duration_s: float = 4.0,
    color_boost: bool = True,
) -> Optional[Tuple[str, str, str]]:
    """
    Convert a static image (flyer, title screen, in-game screenshot, backdrop) into:
    1. A top-to-bottom scrolling panorama `.gif` using `dmd_gif_converter` (`scroll_enabled=True`).
    2. A binary `.rgb565.stream` (`DMDS`) scrolling from top to bottom.
    3. A tall `width x (height*2+8)` `.rgb565` static pan surface.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pan_h = height * 2 + 8

    try:
        with Image.open(io.BytesIO(raw_image_bytes)) as pil_src:
            rgb_src = pil_src.convert("RGB")
            # Ensure the image has enough vertical height (`>= width * 0.75`) for a rich top-to-bottom scroll
            target_tall_h = max(pan_h, int(round(width * 0.75)))
            src_w, src_h = rgb_src.size
            scaled_h_at_w = int(round((width / max(1, src_w)) * src_h))
            if scaled_h_at_w < height + 20:
                tall_img = ImageOps.fit(
                    rgb_src,
                    (width * 2, target_tall_h * 2),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            else:
                tall_img = rgb_src.copy()

            # Also write static tall pan backdrop (.png + .rgb565)
            pan_fitted = ImageOps.fit(
                rgb_src,
                (width, pan_h),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            if color_boost:
                pan_fitted = ImageEnhance.Contrast(pan_fitted).enhance(1.20)
                pan_fitted = ImageEnhance.Color(pan_fitted).enhance(1.25)
            static_png_name = f"{stem}_{width}x{pan_h}.png"
            static_rgb565_name = f"{stem}_{width}x{pan_h}.rgb565"
            pan_fitted.save(out_dir / static_png_name, format="PNG", optimize=True)
            (out_dir / static_rgb565_name).write_bytes(
                rgb888_to_rgb565_le(np.asarray(pan_fitted, dtype=np.uint8))
            )

            # Save a 2-frame seed GIF so FFmpeg's `-stream_loop -1` inside `dmd_gif_converter.process_file`
            # streams frames continuously across the full top-to-bottom `crop_y` trajectory
            seed_gif = out_dir / f"_seed_{stem}.gif"
            tall_img.save(
                seed_gif,
                format="GIF",
                save_all=True,
                append_images=[tall_img],
                duration=100,
                loop=0,
            )
    except Exception as exc:
        logger.info("[CONVERTER] Failed to decode source image for %s: %s", stem, exc)
        return None

    gif_name = f"{stem}_{width}x{height}.gif"
    stream_name = f"{stem}_{width}x{height}.rgb565.stream"
    gif_out = out_dir / gif_name
    stream_out = out_dir / stream_name

    # Compute scroll speed so the top-to-bottom panorama completes in ~duration_s
    scroll_speed = max(12.0, min(40.0, 64.0 / max(1.5, duration_s)))
    params = {
        **DEFAULT_PARAMS,
        "target_width": width,
        "target_height": height,
        "fps_min": fps,
        "fps_max": fps,
        "scroll_enabled": True,
        "scroll_cycles": 0.999,  # Pure top-to-bottom scroll (y = 0 -> max_scroll_dist)
        "scroll_speed": scroll_speed,
        "top_crop_pct": 0.0,
        "bottom_crop_pct": 0.0,
        "auto_action_enabled": False,
        "auto_color_enabled": color_boost,
        "contrast": 1.18 if color_boost else 1.0,
        "saturation": 1.25 if color_boost else 1.0,
    }

    try:
        ok, msg = process_file(
            src_path=str(seed_gif),
            out_path=str(gif_out),
            params=params,
        )
        seed_gif.unlink(missing_ok=True)
        if not ok or not gif_out.exists():
            logger.info("[CONVERTER] process_file panorama failed for %s: %s", stem, msg)
            return None

        frames_rgb, eff_fps = extract_frames_from_gif(gif_out)
        if not frames_rgb:
            return None
        write_rgb565_stream(frames_rgb, stream_out, fps=eff_fps)
        logger.info(
            "[CONVERTER] Built top-to-bottom panorama '%s' (%dx%d, %d frames @ %dfps)",
            stem,
            width,
            height,
            len(frames_rgb),
            eff_fps,
        )
        return gif_name, stream_name, static_rgb565_name
    except Exception as exc:
        seed_gif.unlink(missing_ok=True)
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
    image_count: int = 2,
    width: int = 128,
    height: int = 32,
    fps: int = 15,
    color_boost: bool = True,
) -> Dict[str, object]:
    """
    Build DMD Poster (`32x32` with full-jacket fitting), Marquee (`WxH`), and up to
    `image_count` top-to-bottom scrolling panorama images using `dmd_gif_converter`.
    """
    t0 = time.monotonic()
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    processor = DmdMediaProcessor()

    # 1. Poster (height x height square, e.g. 32x32) — preserves full jacket without cutting in half
    poster_dim = min(width, height)
    poster_png_name = f"poster_{poster_dim}x{poster_dim}.png"
    poster_rgb_name = f"poster_{poster_dim}x{poster_dim}.rgb565"
    poster_bytes = _download_bytes(poster_url, timeout=4.0)

    poster_img: Optional[Image.Image] = None
    if poster_bytes:
        try:
            with Image.open(io.BytesIO(poster_bytes)) as src_p:
                poster_img = _fit_poster_preserving_cover(
                    src_p, poster_dim, poster_dim, palette, color_boost=color_boost
                )
        except Exception as exc:
            logger.info("[CONVERTER] Poster decode failed for '%s': %s", title, exc)

    if poster_img is None:
        raw_poster = out_dir / "src_poster.png"
        _generate_fallback_artwork(raw_poster, poster_dim, poster_dim, title, badge, palette)
        with Image.open(raw_poster) as src_p:
            poster_img = src_p.convert("RGB")
        raw_poster.unlink(missing_ok=True)

    poster_img.save(out_dir / poster_png_name, format="PNG", optimize=True)
    (out_dir / poster_rgb_name).write_bytes(
        rgb888_to_rgb565_le(np.asarray(poster_img, dtype=np.uint8))
    )

    # 2. Marquee / Banner (width x height, e.g. 128x32)
    raw_marquee = out_dir / "src_marquee.img"
    marquee_bytes = _download_bytes(marquee_url, timeout=3.5)
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

    # 3. Top-to-Bottom Scrolling Panorama Images (`0..image_count`) via `dmd_gif_converter`
    pan_h = height * 2 + 8
    candidate_urls: List[str] = []
    for u in (image_urls or []):
        if u and u not in candidate_urls:
            candidate_urls.append(u)
    if backdrop_url and backdrop_url not in candidate_urls:
        candidate_urls.insert(0, backdrop_url)
    if poster_url and poster_url not in candidate_urls:
        candidate_urls.append(poster_url)

    panorama_gifs: List[str] = []
    panorama_streams: List[str] = []
    snapshots_rgb565: List[str] = []

    target_count = max(0, min(5, int(image_count)))
    for url in candidate_urls:
        if len(panorama_streams) >= target_count:
            break
        img_bytes = _download_bytes(url, timeout=4.0)
        if not img_bytes:
            continue
        idx = len(panorama_streams)
        pan_res = _convert_image_to_panorama_gif_and_stream(
            raw_image_bytes=img_bytes,
            out_dir=out_dir,
            stem=f"pan_{idx}",
            width=width,
            height=height,
            fps=fps,
            duration_s=4.0,
            color_boost=color_boost,
        )
        if pan_res:
            gif_f, stream_f, static_rgb_f = pan_res
            panorama_gifs.append(gif_f)
            panorama_streams.append(stream_f)
            snapshots_rgb565.append(static_rgb_f)

    # Also ensure legacy `snap_{width}x{pan_h}.png` and `.rgb565` exist for backward compatibility
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
        "[CONVERTER] Static + %d/%d panorama image(s) built for '%s' in %.2fs",
        len(panorama_streams),
        target_count,
        title,
        time.monotonic() - t0,
    )

    return {
        "poster_png": poster_png_name,
        "poster_rgb565": poster_rgb_name,
        "marquee_png": f"marquee_{width}x{height}.png",
        "marquee_rgb565": f"marquee_{width}x{height}.rgb565",
        "snapshot_pan_png": snap_png_default,
        "snapshot_pan_rgb565": snap_rgb_default,
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
    1. Attempts direct download from `video_url` (e.g. ScreenScraper, ArcadeItalia shortplay MP4, AnimeThemes).
    2. If unavailable, attempts extraction from `youtube_url` via `yt-dlp`.
    3. If neither is available, returns `has_video=False` immediately (NO synthetic grid+ball!).
    """
    _apply_worker_nice(nice_inc)
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_video_path = out_dir / "src_trailer.mp4"

    have_real_video = False
    vid_bytes = _download_bytes(video_url, timeout=8.0)
    if vid_bytes and len(vid_bytes) > 8192:
        src_video_path.write_bytes(vid_bytes)
        have_real_video = True
    elif youtube_url:
        have_real_video = _extract_youtube_clip_ytdlp(
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

    # For 4:3 arcade gameplay videos or `panorama` mode, enable vertical scroll unless auto_action is explicitly requested
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
