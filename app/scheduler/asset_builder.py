"""
Isolated worker functions that build DMD-ready images (.png, .rgb565) and
videos (.gif, .rgb565.stream) using `dmd_gif_converter`.

Designed to execute inside a separate worker process (`ProcessPoolExecutor`)
with lowered CPU priority (`os.nice`) so the FastAPI HTTP/Streaming event loop
experiences zero GIL contention.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.request import Request, urlopen

import cv2
import numpy as np
from PIL import Image, ImageDraw

from dmd_gif_converter import (
    DmdClipConfig,
    DmdMediaProcessor,
    rgb888_to_rgb565_le,
)

logger = logging.getLogger(__name__)


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


def _download_bytes(url: Optional[str], timeout: float = 5.0) -> Optional[bytes]:
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        req = Request(url, headers={"User-Agent": "dmd-media-showcase/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read(12 * 1024 * 1024)
    except Exception as exc:
        logger.debug("Download skipped for %s: %s", url, exc)
    return None


def _generate_fallback_artwork(
    path: Path,
    width: int,
    height: int,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
) -> Path:
    """
    Generate vibrant LED-friendly artwork when a remote scraper image is offline
    or unavailable in Zero-Config mode.
    """
    r0, g0, b0 = palette
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        factor = 0.35 + 0.65 * (1.0 - y / max(1, height - 1))
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


def _generate_synthetic_trailer_mp4(
    path: Path,
    width: int,
    height: int,
    fps: int,
    duration_s: float,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
) -> Path:
    """
    Synthesize a dynamic animated trailer video clip when no remote video stream
    is reachable, ensuring 100% deterministic operation in Zero-Config / offline mode.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    src_w, src_h = max(128, width * 2), max(64, height * 2)
    total_frames = max(12, int(round(duration_s * fps)))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, float(fps), (src_w, src_h))

    r0, g0, b0 = palette
    for idx in range(total_frames):
        t = idx / max(1, total_frames)
        frame = np.zeros((src_h, src_w, 3), dtype=np.uint8)

        # Animated cyber-grid / starfield background in BGR for OpenCV
        shift = int(idx * 4) % 32
        for x in range(shift, src_w, 32):
            cv2.line(frame, (x, 0), (x, src_h), (int(b0 * 0.5), int(g0 * 0.5), int(r0 * 0.5)), 1)
        for y in range(0, src_h, 16):
            cv2.line(frame, (0, y), (src_w, y), (int(b0 * 0.4), int(g0 * 0.4), int(r0 * 0.4)), 1)

        # Animated spotlight / action sprite
        cx = int((0.15 + 0.70 * (0.5 + 0.5 * math.sin(t * 2.0 * math.pi))) * src_w)
        cy = int((0.5 + 0.25 * math.cos(t * 4.0 * math.pi)) * src_h)
        cv2.circle(frame, (cx, cy), 18, (b0, g0, r0), -1)
        cv2.circle(frame, (cx, cy), 20, (255, 255, 255), 2)

        cv2.putText(
            frame,
            badge[:12].upper(),
            (8, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            title[:18].upper(),
            (8, src_h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (180, 255, 255),
            1,
            cv2.LINE_AA,
        )
        writer.write(frame)

    writer.release()
    return path


def build_static_assets_sync(
    item_dir: str,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
    poster_url: Optional[str],
    marquee_url: Optional[str],
    backdrop_url: Optional[str],
    width: int = 128,
    height: int = 32,
    color_boost: bool = True,
) -> Dict[str, str]:
    """
    Build static DMD assets (Poster 32x32, Marquee WxH, Pan-Backdrop Wx72) in < 150 ms.
    """
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    processor = DmdMediaProcessor()

    # 1. Poster (height x height square, e.g. 32x32)
    raw_poster = out_dir / "src_poster.img"
    poster_bytes = _download_bytes(poster_url, timeout=3.0)
    if poster_bytes:
        raw_poster.write_bytes(poster_bytes)
    else:
        _generate_fallback_artwork(raw_poster, 96, 96, title, badge, palette)

    poster_dim = min(width, height)
    try:
        processor.process_poster(
            raw_poster,
            out_dir,
            width=poster_dim,
            height=poster_dim,
            smart_color_boost=color_boost,
            stem="poster",
        )
    except Exception:
        _generate_fallback_artwork(raw_poster, 96, 96, title, badge, palette)
        processor.process_poster(
            raw_poster,
            out_dir,
            width=poster_dim,
            height=poster_dim,
            smart_color_boost=color_boost,
            stem="poster",
        )

    # 2. Marquee / Banner (width x height, e.g. 128x32)
    raw_marquee = out_dir / "src_marquee.img"
    marquee_bytes = _download_bytes(marquee_url, timeout=3.0)
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

    # 3. Pan-Scan Backdrop / Snapshot (width x (height * 2 + 8), e.g. 128x72)
    pan_h = height * 2 + 8
    raw_snap = out_dir / "src_snap.img"
    snap_bytes = _download_bytes(backdrop_url, timeout=3.0)
    if snap_bytes:
        raw_snap.write_bytes(snap_bytes)
    else:
        _generate_fallback_artwork(raw_snap, width * 2, pan_h * 2, title, badge, palette)

    try:
        processor.process_pan_backdrop(
            raw_snap,
            out_dir,
            width=width,
            height=pan_h,
            smart_color_boost=color_boost,
            stem="snap",
        )
    except Exception:
        _generate_fallback_artwork(raw_snap, width * 2, pan_h * 2, title, badge, palette)
        processor.process_pan_backdrop(
            raw_snap,
            out_dir,
            width=width,
            height=pan_h,
            smart_color_boost=color_boost,
            stem="snap",
        )

    # Clean up raw source downloads
    for tmp_f in (raw_poster, raw_marquee, raw_snap):
        tmp_f.unlink(missing_ok=True)

    return {
        "poster_png": f"poster_{poster_dim}x{poster_dim}.png",
        "poster_rgb565": f"poster_{poster_dim}x{poster_dim}.rgb565",
        "marquee_png": f"marquee_{width}x{height}.png",
        "marquee_rgb565": f"marquee_{width}x{height}.rgb565",
        "snapshot_pan_png": f"snap_{width}x{pan_h}.png",
        "snapshot_pan_rgb565": f"snap_{width}x{pan_h}.rgb565",
    }


def build_video_assets_worker_task(
    item_dir: str,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
    video_url: Optional[str],
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
    Downloads or synthesizes the source trailer and runs `DmdMediaProcessor`
    to produce both `video_{w}x{h}.gif` and `video_{w}x{h}.rgb565.stream`.
    """
    _apply_worker_nice(nice_inc)
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    src_video_path = out_dir / "src_trailer.mp4"
    vid_bytes = _download_bytes(video_url, timeout=6.0)
    if vid_bytes and len(vid_bytes) > 4096:
        src_video_path.write_bytes(vid_bytes)
    else:
        _generate_synthetic_trailer_mp4(
            src_video_path,
            width=width,
            height=height,
            fps=fps,
            duration_s=min(max_duration_s, 8.0),
            title=title,
            badge=badge,
            palette=palette,
        )

    use_auto_action = video_mode == "auto_action" and bool(vid_bytes)
    preset_mode = video_mode if video_mode in ("pixel_art", "anime", "cinema") else "pixel_art"

    clip_cfg = DmdClipConfig(
        width=width,
        height=height,
        fps=fps,
        max_duration_s=max_duration_s,
        mode=preset_mode,
        use_ai_moments=bool(vid_bytes),
        use_auto_action=use_auto_action,
        use_smart_color_boost=color_boost,
        scroll_enabled=False,
    )

    processor = DmdMediaProcessor()
    stem = f"video_{width}x{height}"
    try:
        res = processor.process_video_trailer(
            input_video=src_video_path,
            output_dir=out_dir,
            config=clip_cfg,
            export_gif=True,
            export_rgb565_stream=True,
            stem=stem,
        )
    except Exception:
        # Fallback if remote video had a corrupt container
        _generate_synthetic_trailer_mp4(
            src_video_path,
            width=width,
            height=height,
            fps=fps,
            duration_s=min(max_duration_s, 6.0),
            title=title,
            badge=badge,
            palette=palette,
        )
        fallback_cfg = DmdClipConfig(
            width=width,
            height=height,
            fps=fps,
            max_duration_s=max_duration_s,
            use_ai_moments=False,
            use_auto_action=False,
            use_smart_color_boost=color_boost,
        )
        res = processor.process_video_trailer(
            input_video=src_video_path,
            output_dir=out_dir,
            config=fallback_cfg,
            export_gif=True,
            export_rgb565_stream=True,
            stem=stem,
        )
    finally:
        src_video_path.unlink(missing_ok=True)

    return {
        "frame_count": res.frame_count,
        "fps": res.fps,
        "duration_s": res.duration_s,
        "quality_score": res.quality_score,
        "gif_file": f"{stem}.gif",
        "stream_file": f"{stem}.rgb565.stream",
    }
