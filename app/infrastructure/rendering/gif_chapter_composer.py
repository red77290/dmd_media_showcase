"""
Pure 24-bit `RGB888` (`rgb24`) Chapter-by-Chapter GIF Composer (`app/infrastructure/rendering/gif_chapter_composer.py`).

Implements `IBatchGifComposerPort`:
- Zero intermediate `RGB565` quantization: every chapter (`Card`, `Description`, `Jacket`, `Marquee`,
  `Snapshots`, `Video <= 120s`) is rendered in 24-bit `RGB888` (`rgb24`) and encoded directly into a
  single universal `.gif` (`-gifflags -offsetting-transdiff`) compatible 1:1 with both `ArcadeMatrix`
  (ESP32 / ESP32-S3) and `ArcadeMatrix_RPI` (Rust).
- Bounded worker RAM (`< 10 MB` peak even for `120s` `256x64` clips) via chunked `rgb24` file streaming.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import io
import logging
import os
from pathlib import Path
import subprocess
import threading
import uuid
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image, ImageOps

from app.config import settings
from app.domain.batch_scraper_specs import ChapterSelectionSpec, GifEncodingSpec
from app.domain.entities import MediaCandidate, PanelGeometry
from app.domain.ports.batch_scraper_ports import ComposedGifResult, IBatchGifComposerPort
from app.infrastructure.rendering.card_layout_engine import render_card_rgb888_frames_sync
from app.infrastructure.rendering.imaging_primitives import (
    apply_worker_nice,
    fit_poster_preserving_cover,
    generate_fallback_artwork,
    resolve_active_downloader,
    resolve_framing_and_color_params,
)
from app.infrastructure.rendering.panorama_and_static import (
    _composite_pil_to_opaque_rgb,
    _enhance_panorama_strip,
    resolve_showcase_panorama_axis,
)
from app.infrastructure.rendering.video_pipeline import resolve_domain_scene_type_hint
from app.infrastructure.scrapers.youtube_resolver import (
    extract_hd_youtube_clip_ytdlp,
    slice_hd_remote_video_ffmpeg,
)
from app.portable_env import get_ffmpeg_binary, subprocess_windowless_kwargs
from dmd_gif_converter import DmdClipConfig, DmdMediaProcessor
from src.engine.conversion.core import DEFAULT_PARAMS, LET_ME_HANDLE_PARAMS, process_file
from src.engine.conversion.quality import evaluate_gif_quality

logger = logging.getLogger("dmd_showcase.batch_composer")


def _write_rgb24_frames_chunked(out_f: Any, frames_rgb: List[np.ndarray]) -> int:
    written = 0
    for arr in frames_rgb:
        rgb = np.ascontiguousarray(arr[:, :, :3], dtype=np.uint8)
        out_f.write(rgb.tobytes())
        written += 1
    return written


def _append_image_chapter_rgb24(
    out_f: Any,
    processor: DmdMediaProcessor,
    raw_bytes: Optional[bytes],
    width: int,
    height: int,
    fps: int,
    duration_s: float,
    mode: str,
    color_boost: bool,
) -> int:
    if not raw_bytes:
        return 0
    try:
        with Image.open(io.BytesIO(raw_bytes)) as pil_src:
            rgb_src = _composite_pil_to_opaque_rgb(pil_src)
    except Exception:
        return 0

    src_w, src_h = rgb_src.size
    if src_w <= 0 or src_h <= 0:
        return 0

    eff_fps = max(6, min(30, int(fps)))
    req_dur_s = max(1.5, float(duration_s))

    if mode == "static":
        fitted = ImageOps.fit(
            rgb_src,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.45),
        )
        enhanced = _enhance_panorama_strip(processor, fitted, color_boost)
        frame_bytes = np.ascontiguousarray(np.asarray(enhanced, dtype=np.uint8)[:, :, :3]).tobytes()
        n_static = max(12, int(round(eff_fps * req_dur_s)))
        for _ in range(n_static):
            out_f.write(frame_bytes)
        return n_static

    axis = resolve_showcase_panorama_axis(src_w, src_h, width, height, "auto")
    scale = max(float(width) / float(src_w), float(height) / float(src_h))

    if axis == "horizontal":
        min_span_w = width + max(16, width // 4)
        if int(round(src_w * scale)) < min_span_w:
            scale = float(min_span_w) / float(src_w)
        scaled_w = max(width + 1, int(round(src_w * scale)))
        scaled_h = max(height, int(round(src_h * scale)))
        resized = rgb_src.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        if scaled_h > height:
            crop_y = (scaled_h - height) // 2
            resized = resized.crop((0, crop_y, scaled_w, crop_y + height))
        strip_pil = _enhance_panorama_strip(processor, resized, color_boost)
        strip_arr = np.asarray(strip_pil, dtype=np.uint8)
        scroll_dist = max(0, strip_arr.shape[1] - width)
    else:
        min_span_h = height + max(16, height // 2)
        if int(round(src_h * scale)) < min_span_h:
            scale = float(min_span_h) / float(src_h)
        scaled_w = max(width, int(round(src_w * scale)))
        scaled_h = max(height + 1, int(round(src_h * scale)))
        resized = rgb_src.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        if scaled_w > width:
            crop_x = (scaled_w - width) // 2
            resized = resized.crop((crop_x, 0, crop_x + width, scaled_h))
        strip_pil = _enhance_panorama_strip(processor, resized, color_boost)
        strip_arr = np.asarray(strip_pil, dtype=np.uint8)
        scroll_dist = max(0, strip_arr.shape[0] - height)

    max_1px_dist = max(220, int(round(eff_fps * max(8.5, req_dur_s * 2.25))))
    if scroll_dist <= max_1px_dist:
        step_px = 1
    else:
        target_frames = max(24, int(round(eff_fps * max(2.5, req_dur_s))))
        step_px = max(1, int(round(float(scroll_dist) / float(target_frames))))

    travel = list(range(0, scroll_dist + 1, step_px))
    if not travel:
        travel = [0]
    elif travel[-1] != scroll_dist:
        travel.append(scroll_dist)

    hold_frames = max(2, min(8, int(round(eff_fps * 0.20))))
    positions = ([0] * hold_frames) + travel + ([scroll_dist] * hold_frames)

    written = 0
    for pos in positions:
        if axis == "horizontal":
            sl = np.ascontiguousarray(strip_arr[:, pos : pos + width, :3], dtype=np.uint8)
        else:
            sl = np.ascontiguousarray(strip_arr[pos : pos + height, :, :3], dtype=np.uint8)
        out_f.write(sl.tobytes())
        written += 1
    return written


def _save_non_interlaced_marquee_png(
    raw_bytes: Optional[bytes],
    out_png_path: Path,
    max_dim: int = 256,
) -> bool:
    """Save a strictly non-interlaced (`interlace == 0`) RGB PNG `<= 512x512` for ESP32/RPi marquee sync."""
    if not raw_bytes:
        return False
    try:
        with Image.open(io.BytesIO(raw_bytes)) as src:
            rgb = _composite_pil_to_opaque_rgb(src)
            w, h = rgb.size
            if w <= 0 or h <= 0:
                return False
            if w > max_dim or h > max_dim:
                rgb.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out_png_path.parent.mkdir(parents=True, exist_ok=True)
            # Pillow's PNG writer defaults to non-interlaced (`interlace=0`) unless `interlace=1` is passed
            rgb.save(out_png_path, format="PNG", optimize=True)
        return out_png_path.exists() and out_png_path.stat().st_size > 100
    except Exception:
        out_png_path.unlink(missing_ok=True)
        return False


def _download_or_extract_source_video(
    video_url: Optional[str],
    youtube_url: Optional[str],
    title: str,
    badge: str,
    out_mp4: Path,
    max_duration_s: float,
    ffmpeg_threads: int,
    ytdlp_max_mb: int,
) -> bool:
    download_fn = resolve_active_downloader()
    eff_ytdlp_cap = max(int(ytdlp_max_mb), int(max_duration_s * 1.8))
    have_video = False
    if video_url:
        if (
            video_url.endswith((".webm", ".m4v"))
            or "animethemes.moe" in video_url
            or "itunes.apple.com" in video_url
            or "mzstatic.com" in video_url
            or "archive.org/download/" in video_url
        ):
            have_video = slice_hd_remote_video_ffmpeg(
                video_url=video_url,
                out_mp4=out_mp4,
                max_duration_s=max_duration_s,
                start_offset_s=0.0,
                ffmpeg_threads=ffmpeg_threads,
            )
        if not have_video:
            vid_bytes = download_fn(video_url, timeout=max(12.0, min(45.0, max_duration_s * 0.6)))
            if vid_bytes and len(vid_bytes) > 4096:
                out_mp4.write_bytes(vid_bytes)
                have_video = True

    if not have_video and youtube_url:
        have_video = extract_hd_youtube_clip_ytdlp(
            video_page_url=youtube_url,
            out_mp4=out_mp4,
            max_duration_s=max_duration_s,
            max_filesize_mb=eff_ytdlp_cap,
        )
        if not have_video and not youtube_url.startswith("ytsearch") and (title or "").strip():
            yt_fb = f"ytsearch1:{title.strip()} {(badge or '').strip()} gameplay trailer".strip()
            have_video = extract_hd_youtube_clip_ytdlp(
                video_page_url=yt_fb,
                out_mp4=out_mp4,
                max_duration_s=max_duration_s,
                max_filesize_mb=eff_ytdlp_cap,
            )
    return have_video and out_mp4.exists()


def _encode_rgb24_file_to_gif_ffmpeg(
    raw_rgb24_path: Path,
    out_gif_path: Path,
    width: int,
    height: int,
    fps: int,
    dither: str,
    per_scene_palette: bool,
    ffmpeg_threads: int = 1,
) -> bool:
    ffmpeg_bin = get_ffmpeg_binary()
    if per_scene_palette:
        vf_graph = (
            "split[v1][v2];"
            "[v1]palettegen=max_colors=256:reserve_transparent=0:stats_mode=single[pal];"
            f"[v2][pal]paletteuse=new=1:dither={dither}"
        )
    else:
        vf_graph = (
            "split[v1][v2];"
            "[v1]palettegen=max_colors=256:reserve_transparent=0[pal];"
            f"[v2][pal]paletteuse=dither={dither}"
        )

    cmd = [
        ffmpeg_bin,
        "-y",
        "-threads",
        str(max(1, int(ffmpeg_threads))),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(max(6, min(30, int(fps)))),
        "-i",
        str(raw_rgb24_path),
        "-lavfi",
        vf_graph,
        "-gifflags",
        "-offsetting-transdiff",
        "-loop",
        "0",
        "-f",
        "gif",
        str(out_gif_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **subprocess_windowless_kwargs(),
    )
    stderr_chunks: List[bytes] = []

    def _drain() -> None:
        if proc.stderr is None:
            return
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
            if len(stderr_chunks) < 8:
                stderr_chunks.append(chunk)

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()
    rc = proc.wait()
    drainer.join(timeout=3.0)
    if rc != 0 or not out_gif_path.exists():
        logger.warning(
            "[BATCH-GIF] FFmpeg RGB888 GIF encode failed (rc=%s): %s",
            rc,
            b"".join(stderr_chunks).decode(errors="replace")[-300:],
        )
        return False
    return out_gif_path.stat().st_size > 100


def compose_candidate_gif_worker_task(
    cand_dict: Dict[str, Any],
    chapters_dict: Dict[str, Any],
    encoding_dict: Dict[str, Any],
    work_dir_str: str,
    nice_inc: int = 10,
    ffmpeg_threads: int = 1,
    ytdlp_max_mb: int = 80,
    export_marquee_png: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Pickleable worker entrypoint building a pure 24-bit `RGB888` `.gif` (and optional marquee `.png`).
    """
    apply_worker_nice(nice_inc, ffmpeg_threads=ffmpeg_threads)
    download_fn = resolve_active_downloader()
    work_dir = Path(work_dir_str)
    work_dir.mkdir(parents=True, exist_ok=True)

    cand = MediaCandidate(**cand_dict)
    chapters = ChapterSelectionSpec(**chapters_dict)
    encoding = GifEncodingSpec(**encoding_dict).normalized()
    geom = PanelGeometry(width=encoding.width, height=encoding.height)
    poster_w, poster_h = geom.resolved_poster_dims()

    width, height, fps = encoding.width, encoding.height, encoding.fps
    use_auto_action, scroll_on_video, use_smart_color_boost, resolved_preset_mode = (
        resolve_framing_and_color_params(
            video_mode="auto_action" if encoding.let_me_handle_it else encoding.video_mode,
            color_boost="auto" if encoding.let_me_handle_it else encoding.color_boost,
            domain=cand.domain,
        )
    )
    eff_content_mode = (
        resolved_preset_mode if encoding.content_mode == "auto" else encoding.content_mode
    )
    if encoding.let_me_handle_it:
        use_auto_action = True
        use_smart_color_boost = True

    processor = DmdMediaProcessor()
    out_gif_path = work_dir / f"composed_{width}x{height}.gif"
    marquee_png_path = work_dir / "companion_marquee.png"
    has_marquee_companion = False

    if export_marquee_png:
        mq_src = download_fn(cand.marquee_url or cand.resolved_jacket_url(), timeout=6.0)
        has_marquee_companion = _save_non_interlaced_marquee_png(mq_src, marquee_png_path)

    is_video_only = (
        chapters.include_video
        and not chapters.include_card
        and not chapters.include_jacket
        and not chapters.include_marquee
        and int(chapters.snapshot_count) <= 0
    )

    scene_hint = resolve_domain_scene_type_hint(
        domain=cand.domain,
        genre_hint=str((cand.extra_meta or {}).get("canonical_genre") or cand.subtitle or ""),
    )

    # Fast single-pass native `dmd_gif_converter` RGB888 GIF when `video_only` is selected
    if is_video_only:
        src_mp4 = work_dir / "src_clip.mp4"
        try:
            if not _download_or_extract_source_video(
                cand.video_url,
                cand.youtube_url,
                cand.title,
                cand.badge,
                src_mp4,
                encoding.video_max_duration_s,
                ffmpeg_threads,
                ytdlp_max_mb,
            ):
                return None
            clip_cfg = DmdClipConfig(
                width=width,
                height=height,
                fps=fps,
                max_duration_s=encoding.video_max_duration_s,
                skip_intro_pct=0.0,
                skip_outro_pct=0.0,
                mode=eff_content_mode,  # type: ignore[arg-type]
                use_ai_moments=False,
                use_auto_action=use_auto_action,
                use_smart_color_boost=use_smart_color_boost,
                scroll_enabled=scroll_on_video,
                scene_type=scene_hint,
                brightness=encoding.brightness,
                contrast=encoding.contrast,
                saturation=encoding.saturation,
                gamma=encoding.gamma,
                dither=encoding.dither,
                from_start=True,
                evaluate_quality=bool(encoding.reject_threshold > 0),
            )
            res = processor.process_video_trailer(
                input_video=src_mp4,
                output_dir=work_dir,
                config=clip_cfg,
                export_gif=True,
                export_rgb565_stream=False,
                stem=f"composed_{width}x{height}",
            )
            if not res.gif_path or not res.gif_path.exists():
                return None
            if encoding.reject_threshold > 0 and res.quality_score < encoding.reject_threshold:
                res.gif_path.unlink(missing_ok=True)
                return None
            return {
                "gif_path": str(res.gif_path),
                "width": width,
                "height": height,
                "fps": int(res.fps),
                "frame_count": int(res.frame_count),
                "duration_s": float(res.duration_s),
                "size_bytes": res.gif_path.stat().st_size,
                "marquee_png_path": str(marquee_png_path) if has_marquee_companion else "",
                "marquee_size_bytes": marquee_png_path.stat().st_size if has_marquee_companion else 0,
            }
        finally:
            src_mp4.unlink(missing_ok=True)

    # Multi-chapter or non-video composition: stream pure 24-bit `RGB888` (`rgb24`) chapters on disk
    raw_concat_path = work_dir / f"_concat_rgb24_{os.getpid()}_{uuid.uuid4().hex[:6]}.raw"
    frame_rgb24_bytes = width * height * 3
    total_frames = 0
    chapter_segments_written = 0

    try:
        with raw_concat_path.open("wb") as out_f:
            # 1. Card + Description chapter (pure 24-bit RGB888 Pillow frames)
            poster_bytes: Optional[bytes] = None
            if chapters.include_card or chapters.include_jacket:
                poster_bytes = download_fn(cand.resolved_jacket_url(), timeout=5.0)
                if not poster_bytes and cand.marquee_url:
                    poster_bytes = download_fn(cand.marquee_url, timeout=5.0)
                if not poster_bytes and cand.snapshot_urls:
                    poster_bytes = download_fn(cand.snapshot_urls[0], timeout=5.0)

            if chapters.include_card:
                poster_png_name = ""
                if chapters.include_poster_in_card:
                    poster_pil: Optional[Image.Image] = None
                    if poster_bytes:
                        try:
                            with Image.open(io.BytesIO(poster_bytes)) as sp:
                                poster_pil = fit_poster_preserving_cover(
                                    sp, poster_w, poster_h, cand.theme_palette, color_boost=use_smart_color_boost
                                )
                        except Exception:
                            poster_pil = None
                    if poster_pil is None:
                        fb_path = work_dir / "fb_poster.png"
                        generate_fallback_artwork(fb_path, poster_w, poster_h, cand.title, cand.badge, cand.theme_palette)
                        with Image.open(fb_path) as sp:
                            poster_pil = sp.convert("RGB")
                        fb_path.unlink(missing_ok=True)
                    poster_png_name = f"card_poster_{poster_w}x{poster_h}.png"
                    poster_pil.save(work_dir / poster_png_name, format="PNG")

                card_frames, _ = render_card_rgb888_frames_sync(
                    item_dir=str(work_dir),
                    width=width,
                    height=height,
                    fps=fps,
                    title=cand.title,
                    year=cand.year,
                    badge=cand.badge,
                    badge_color=cand.badge_color,
                    subtitle=cand.subtitle,
                    rating=cand.rating,
                    synopsis=cand.synopsis,
                    poster_png_name=poster_png_name,
                    include_poster=chapters.include_poster_in_card,
                    include_synopsis=chapters.include_description,
                    text_scroll_speed=chapters.text_scroll_speed,
                    include_card_intro=True,
                    card_layout=chapters.card_layout,
                    card_theme=chapters.card_theme,
                    card_font=chapters.card_font,
                    card_accent_color=chapters.card_accent_color,
                    show_badge=chapters.show_badge,
                    show_year=chapters.show_year,
                    show_rating=chapters.show_rating,
                    card_hold_s=chapters.card_hold_s,
                )
                n_card = _write_rgb24_frames_chunked(out_f, card_frames)
                if n_card > 0:
                    total_frames += n_card
                    chapter_segments_written += 1

            # 2. Jacket / Poster chapter
            if chapters.include_jacket:
                if not poster_bytes:
                    fb_j = work_dir / "fb_jacket.png"
                    generate_fallback_artwork(fb_j, width * 2, height * 2, cand.title, cand.badge, cand.theme_palette)
                    poster_bytes = fb_j.read_bytes()
                    fb_j.unlink(missing_ok=True)
                n_j = _append_image_chapter_rgb24(
                    out_f,
                    processor,
                    poster_bytes,
                    width,
                    height,
                    fps,
                    chapters.panorama_duration_s,
                    chapters.jacket_mode,
                    use_smart_color_boost,
                )
                if n_j > 0:
                    total_frames += n_j
                    chapter_segments_written += 1

            # 3. Marquee chapter
            if chapters.include_marquee:
                mq_bytes = download_fn(cand.marquee_url or cand.backdrop_url or cand.resolved_jacket_url(), timeout=5.0)
                if not mq_bytes:
                    fb_m = work_dir / "fb_marquee.png"
                    generate_fallback_artwork(fb_m, width * 2, height * 2, cand.title, cand.badge, cand.theme_palette)
                    mq_bytes = fb_m.read_bytes()
                    fb_m.unlink(missing_ok=True)
                n_m = _append_image_chapter_rgb24(
                    out_f,
                    processor,
                    mq_bytes,
                    width,
                    height,
                    fps,
                    chapters.panorama_duration_s,
                    chapters.marquee_mode,
                    use_smart_color_boost,
                )
                if n_m > 0:
                    total_frames += n_m
                    chapter_segments_written += 1

            # 4. In-Game Screenshots / Backdrops (`0..5`)
            snap_limit = max(0, min(5, int(chapters.snapshot_count)))
            if snap_limit > 0:
                snap_candidates = list(cand.snapshot_urls or [])
                if not snap_candidates and cand.backdrop_url:
                    snap_candidates = [cand.backdrop_url]
                added_snaps = 0
                for s_url in snap_candidates:
                    if added_snaps >= snap_limit:
                        break
                    s_bytes = download_fn(s_url, timeout=5.0)
                    n_s = _append_image_chapter_rgb24(
                        out_f,
                        processor,
                        s_bytes,
                        width,
                        height,
                        fps,
                        chapters.panorama_duration_s,
                        "panorama",
                        use_smart_color_boost,
                    )
                    if n_s > 0:
                        total_frames += n_s
                        chapter_segments_written += 1
                        added_snaps += 1

            # 5. Video Gameplay / Trailer (`2..120s`, pure 24-bit `rgb24` stream from `dmd_gif_converter`)
            if chapters.include_video and (cand.video_url or cand.youtube_url):
                src_mp4 = work_dir / "src_multi_clip.mp4"
                vid_rgb24_tmp = work_dir / f"_vid_rgb24_{uuid.uuid4().hex[:6]}.raw"
                dummy_gif = work_dir / "_unused.gif"
                try:
                    if _download_or_extract_source_video(
                        cand.video_url,
                        cand.youtube_url,
                        cand.title,
                        cand.badge,
                        src_mp4,
                        encoding.video_max_duration_s,
                        ffmpeg_threads,
                        ytdlp_max_mb,
                    ):
                        start_s, end_s = processor.select_best_clip_window(
                            src_mp4,
                            DmdClipConfig(
                                width=width,
                                height=height,
                                fps=fps,
                                max_duration_s=encoding.video_max_duration_s,
                                from_start=True,
                                use_ai_moments=False,
                            ),
                        )
                        v_params = {
                            **DEFAULT_PARAMS,
                            **(LET_ME_HANDLE_PARAMS if use_auto_action else {}),
                            "target_width": width,
                            "target_height": height,
                            "mode": eff_content_mode,
                            "fps_min": fps,
                            "fps_max": fps,
                            "max_duration": encoding.video_max_duration_s,
                            "scroll_enabled": False if use_auto_action else scroll_on_video,
                            "force_panorama_scroll": bool(not use_auto_action and scroll_on_video),
                            "let_me_handle_it": use_auto_action,
                            "auto_action_enabled": use_auto_action,
                            "action_smart_auto_crop": use_auto_action,
                            "action_auto_detector_fallback": True,
                            **({"action_scene_type": scene_hint} if scene_hint else {}),
                            "auto_color_enabled": use_smart_color_boost,
                            "brightness": encoding.brightness,
                            "contrast": encoding.contrast,
                            "saturation": encoding.saturation,
                            "gamma": encoding.gamma,
                            "dither": encoding.dither,
                            "skip_quality_eval": True,
                            "raw_rgb24_out_path": str(vid_rgb24_tmp),
                            "stream_fps": fps,
                            "export_gif_output": False,
                        }
                        ok, _ = process_file(
                            src_path=str(src_mp4),
                            out_path=str(dummy_gif),
                            params=v_params,
                            start_s=start_s,
                            end_s=end_s,
                        )
                        if ok and vid_rgb24_tmp.exists():
                            v_size = vid_rgb24_tmp.stat().st_size
                            v_frames = v_size // frame_rgb24_bytes
                            if v_frames > 0:
                                exact_bytes = v_frames * frame_rgb24_bytes
                                with vid_rgb24_tmp.open("rb") as vf:
                                    rem = exact_bytes
                                    while rem > 0:
                                        buf = vf.read(min(262144, rem))
                                        if not buf:
                                            break
                                        out_f.write(buf)
                                        rem -= len(buf)
                                total_frames += v_frames
                                chapter_segments_written += 1
                finally:
                    src_mp4.unlink(missing_ok=True)
                    vid_rgb24_tmp.unlink(missing_ok=True)
                    dummy_gif.unlink(missing_ok=True)

        if total_frames <= 0 or not raw_concat_path.exists():
            return None

        use_per_scene = bool(
            encoding.palette_mode == "per_scene_rgb888" and chapter_segments_written > 1
        )
        ok_gif = _encode_rgb24_file_to_gif_ffmpeg(
            raw_rgb24_path=raw_concat_path,
            out_gif_path=out_gif_path,
            width=width,
            height=height,
            fps=fps,
            dither=encoding.dither,
            per_scene_palette=use_per_scene,
            ffmpeg_threads=ffmpeg_threads,
        )
        if not ok_gif:
            return None

        if encoding.reject_threshold > 0:
            q_info = evaluate_gif_quality(str(out_gif_path))
            if int(q_info.get("score", 75)) < encoding.reject_threshold:
                out_gif_path.unlink(missing_ok=True)
                return None

        return {
            "gif_path": str(out_gif_path),
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": total_frames,
            "duration_s": round(total_frames / float(max(1, fps)), 2),
            "size_bytes": out_gif_path.stat().st_size,
            "marquee_png_path": str(marquee_png_path) if has_marquee_companion else "",
            "marquee_size_bytes": marquee_png_path.stat().st_size if has_marquee_companion else 0,
        }
    finally:
        raw_concat_path.unlink(missing_ok=True)


class GifChapterComposerAdapter(IBatchGifComposerPort):
    """Hexagonal outbound adapter executing 24-bit RGB888 GIF composition in isolated worker processes."""

    async def compose_candidate_gif(
        self,
        candidate: MediaCandidate,
        chapters: ChapterSelectionSpec,
        encoding: GifEncodingSpec,
        work_dir: Path,
        job_id: str = "",
        export_marquee_png: bool = False,
    ) -> Optional[ComposedGifResult]:
        loop = asyncio.get_running_loop()
        from app.scheduler.dmd_process_registry import dmd_process_registry
        from app.scheduler.worker_pool import batch_manager

        sync_id = f"batch_{job_id}_sync" if job_id else ""
        start_epoch = dmd_process_registry.current_epoch(sync_id) if sync_id else 0
        if sync_id:
            dmd_process_registry.raise_if_aborted(sync_id, start_epoch)

        cand_dict = asdict(candidate)
        chapters_dict = asdict(chapters)
        encoding_dict = asdict(encoding)

        if sync_id and dmd_process_registry.should_use_isolated_process(batch_manager):
            handle = await dmd_process_registry.acquire_handle_async(loop, sync_id, start_epoch)
            with dmd_process_registry.session_blocking_init(sync_id, handle, start_epoch) as sess:
                raw_res = await sess.run_step(
                    loop,
                    compose_candidate_gif_worker_task,
                    cand_dict,
                    chapters_dict,
                    encoding_dict,
                    str(work_dir),
                    settings.worker_nice_increment,
                    settings.ffmpeg_threads_per_job,
                    settings.ytdlp_max_filesize_mb,
                    export_marquee_png,
                )
        else:
            pool = batch_manager.shared_executor()
            raw_res = await loop.run_in_executor(
                pool,
                compose_candidate_gif_worker_task,
                cand_dict,
                chapters_dict,
                encoding_dict,
                str(work_dir),
                settings.worker_nice_increment,
                settings.ffmpeg_threads_per_job,
                settings.ytdlp_max_filesize_mb,
                export_marquee_png,
            )

        if not raw_res:
            return None
        mq_str = str(raw_res.get("marquee_png_path") or "")
        return ComposedGifResult(
            gif_path=Path(str(raw_res["gif_path"])),
            width=int(raw_res["width"]),
            height=int(raw_res["height"]),
            fps=int(raw_res["fps"]),
            frame_count=int(raw_res["frame_count"]),
            duration_s=float(raw_res["duration_s"]),
            size_bytes=int(raw_res["size_bytes"]),
            marquee_png_path=Path(mq_str) if mq_str else None,
            marquee_size_bytes=int(raw_res.get("marquee_size_bytes") or 0),
        )


gif_chapter_composer_adapter = GifChapterComposerAdapter()
