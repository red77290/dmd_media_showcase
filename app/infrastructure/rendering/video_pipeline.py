"""
Video Clip Extraction & `dmd_gif_converter` Transcoding Worker (`app/infrastructure/rendering/video_pipeline.py`).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from app.domain.taxonomy.resolver import resolve_canonical_genre
from app.infrastructure.rendering.dmdz_codec import stabilize_rgb565_stream_temporal_dither
from app.infrastructure.rendering.imaging_primitives import (
    apply_worker_nice,
    resolve_active_downloader,
    resolve_framing_and_color_params,
)
from app.infrastructure.scrapers.youtube_resolver import (
    extract_hd_youtube_clip_ytdlp,
    slice_hd_remote_video_ffmpeg,
)
from dmd_gif_converter import DmdClipConfig, DmdMediaProcessor

logger = logging.getLogger("dmd_showcase.converter")


def resolve_domain_scene_type_hint(domain: str = "", genre_hint: str = "") -> str:
    """
    Map known Showcase domain + game genre metadata to a deterministic `dmd_gif_converter`
    `scene_type` so 2D platformers, VS fighters, beat 'em ups, and run & gun games always
    use the unified floor/tall-sprite framing model instead of cinema dialogue profiles.
    """
    if (domain or "").strip().lower() != "games":
        return ""
    raw_g = (genre_hint or "").split("•")[0].strip()
    canon_g = resolve_canonical_genre("games", raw_g) if raw_g else ""
    if canon_g in ("platform", "fighting", "beatemup", "runandgun", "action"):
        return "platformer"
    if canon_g == "shootemup":
        return "action_horizontal"
    if canon_g == "maze":
        return "top_down_isometric"
    return ""


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
    domain: str = "",
    genre_hint: str = "",
) -> Dict[str, object]:
    """
    Top-level worker function executed in `ProcessPoolExecutor` / `ThreadPoolExecutor`:
    1. Slices remote `.webm`/`.m4v`/Archive.org/AnimeThemes/iTunes streams over HTTP via FFmpeg.
    2. Otherwise downloads `video_url` directly, or extracts `youtube_url` via `yt-dlp --force-ipv4`.
    3. Returns `has_video=False` cleanly if no real video exists (never generates synthetic placeholders).
    """
    apply_worker_nice(nice_inc, ffmpeg_threads=ffmpeg_threads)
    download_fn = resolve_active_downloader()
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
            have_real_video = slice_hd_remote_video_ffmpeg(
                video_url=video_url,
                out_mp4=src_video_path,
                max_duration_s=max_duration_s,
                start_offset_s=offset_s,
                ffmpeg_threads=ffmpeg_threads,
            )
        if not have_real_video:
            vid_bytes = download_fn(video_url, timeout=12.0)
            if vid_bytes and len(vid_bytes) > 8192:
                src_video_path.write_bytes(vid_bytes)
                have_real_video = True

    if not have_real_video and youtube_url:
        have_real_video = extract_hd_youtube_clip_ytdlp(
            video_page_url=youtube_url,
            out_mp4=src_video_path,
            max_duration_s=max_duration_s,
            max_filesize_mb=ytdlp_max_filesize_mb,
        )
        if not have_real_video and not youtube_url.startswith("ytsearch") and (title or "").strip():
            yt_fallback = f"ytsearch1:{title.strip()} {(badge or '').strip()} gameplay trailer".strip()
            logger.info(
                "[CONVERTER] Upstream youtube_url unavailable for '%s' (%s) -> retrying search fallback '%s'",
                title,
                youtube_url,
                yt_fallback,
            )
            have_real_video = extract_hd_youtube_clip_ytdlp(
                video_page_url=yt_fallback,
                out_mp4=src_video_path,
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
        resolve_framing_and_color_params(video_mode=video_mode, color_boost=color_boost, domain=domain)
    )

    scene_hint = resolve_domain_scene_type_hint(domain=domain, genre_hint=genre_hint)
    clip_ctx_kwargs: Dict[str, object] = {}
    if scene_hint and "scene_type" in getattr(DmdClipConfig, "__dataclass_fields__", {}):
        clip_ctx_kwargs["scene_type"] = scene_hint

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
        **clip_ctx_kwargs,
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
        if res.frame_count > 1:
            stabilize_rgb565_stream_temporal_dither(out_dir / f"{stem}.rgb565.stream")
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
