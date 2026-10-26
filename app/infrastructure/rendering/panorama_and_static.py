"""
Static Poster, Marquee & Scrolling Panorama Asset Builder (`app/infrastructure/rendering/panorama_and_static.py`).
"""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

from app.infrastructure.rendering.imaging_primitives import (
    fit_poster_preserving_cover,
    generate_fallback_artwork,
    resolve_active_downloader,
)
from dmd_gif_converter import DmdMediaProcessor, rgb888_to_rgb565_le

logger = logging.getLogger("dmd_showcase.converter")


def convert_image_to_panorama_gif_and_stream(
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
    Convert a static image (jacket, marquee, or snapshot) into:
    1. Smooth sub-pixel top-to-bottom scrolling `.gif`
    2. Binary `.rgb565.stream`
    3. Static tall `.rgb565` pan surface
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
    color_boost: Union[str, bool] = "auto",
    poster_w: int = 32,
    poster_h: int = 32,
    jacket_url: Optional[str] = None,
    jacket_panorama: bool = True,
    marquee_panorama: bool = False,
    panorama_duration_s: float = 4.0,
) -> Dict[str, object]:
    """
    Build Mini-Jacket (`poster_{poster_w}x{poster_h}.png`/`.rgb565`), Static Marquee, and
    granular top-to-bottom scrolling panoramas (`Jacket` -> `Marquee` -> `Snapshots`).
    """
    t0 = time.monotonic()
    download_fn = resolve_active_downloader()
    out_dir = Path(item_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    processor = DmdMediaProcessor()
    color_boost_bool = not (
        color_boost is False
        or str(color_boost).strip().lower() in ("false", "0", "no", "off", "none")
    )

    poster_png_name = f"poster_{poster_w}x{poster_h}.png"
    poster_rgb_name = f"poster_{poster_w}x{poster_h}.rgb565"
    poster_bytes = download_fn(poster_url or jacket_url, timeout=4.5)
    if not poster_bytes and marquee_url and marquee_url != poster_url:
        poster_bytes = download_fn(marquee_url, timeout=4.5)
    if not poster_bytes and image_urls:
        poster_bytes = download_fn(image_urls[0], timeout=4.5)

    poster_img: Optional[Image.Image] = None
    if poster_bytes:
        try:
            with Image.open(io.BytesIO(poster_bytes)) as src_p:
                poster_img = fit_poster_preserving_cover(
                    src_p, poster_w, poster_h, palette, color_boost=color_boost_bool
                )
        except Exception as exc:
            logger.info("[CONVERTER] Poster decode failed for '%s': %s", title, exc)

    if poster_img is None:
        raw_poster = out_dir / "src_poster.png"
        generate_fallback_artwork(raw_poster, poster_w, poster_h, title, badge, palette)
        with Image.open(raw_poster) as src_p:
            poster_img = src_p.convert("RGB")
        raw_poster.unlink(missing_ok=True)

    poster_img.save(out_dir / poster_png_name, format="PNG", optimize=True)
    (out_dir / poster_rgb_name).write_bytes(
        rgb888_to_rgb565_le(np.asarray(poster_img, dtype=np.uint8))
    )

    raw_marquee = out_dir / "src_marquee.img"
    marquee_bytes = download_fn(marquee_url or backdrop_url, timeout=4.0)
    if marquee_bytes:
        raw_marquee.write_bytes(marquee_bytes)
    else:
        generate_fallback_artwork(raw_marquee, width * 2, height * 2, title, badge, palette)

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
        generate_fallback_artwork(raw_marquee, width * 2, height * 2, title, badge, palette)
        processor.process_poster(
            raw_marquee,
            out_dir,
            width=width,
            height=height,
            smart_color_boost=color_boost_bool,
            stem="marquee",
        )
    raw_marquee.unlink(missing_ok=True)

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

    if jacket_panorama:
        j_bytes = poster_bytes or download_fn(jacket_url or poster_url, timeout=4.0)
        if j_bytes:
            j_res = convert_image_to_panorama_gif_and_stream(
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

    if marquee_panorama:
        m_bytes = marquee_bytes or download_fn(marquee_url or backdrop_url, timeout=4.0)
        if m_bytes:
            m_res = convert_image_to_panorama_gif_and_stream(
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

    target_snap_count = max(0, min(5, int(image_count)))
    if target_snap_count > 0:
        candidate_urls: List[str] = []
        for u in image_urls or []:
            if u and u not in candidate_urls and u != poster_url:
                candidate_urls.append(u)
        if backdrop_url and backdrop_url not in candidate_urls and backdrop_url != poster_url:
            candidate_urls.insert(0, backdrop_url)

        for url in candidate_urls:
            if len(snapshot_stream_names) >= target_snap_count:
                break
            img_bytes = download_fn(url, timeout=4.0)
            if not img_bytes:
                continue
            idx = len(snapshot_stream_names)
            pan_res = convert_image_to_panorama_gif_and_stream(
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
        generate_fallback_artwork(raw_snap, width, pan_h, title, badge, palette)
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
