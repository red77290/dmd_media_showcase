"""
Clean HD (`720p`/`1080p`) YouTube & remote video stream resolver for `dmd_gif_converter`.

Why 720p/1080p master sources are mandatory:
- Starting from a clean, un-stripped HD stream ensures `Auto-Action` (YOLO / motion saliency),
  `Smart Color Boost` (`analyze_and_compensate`), and Lanczos + `unsharp` downscaling in
  `dmd_gif_converter` operate on artifact-free frames rather than macroblock-damaged 360p video.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from app.portable_env import get_ffmpeg_binary

logger = logging.getLogger("dmd_showcase.scrapers.youtube")

# Always prefer clean 720p/1080p H.264/MP4 (`avc1`) streams and strictly exclude AV1 (`av01`)
# so ARM64 / Docker containers without hardware AV1 decoders never fail on OpenCV/FFmpeg decode.
HD_YTDLP_FORMAT = (
    "bestvideo[vcodec^=avc1][ext=mp4][height<=1080][height>=720]+bestaudio[ext=m4a]/"
    "best[vcodec^=avc1][ext=mp4][height<=1080][height>=720]/"
    "bestvideo[vcodec!*=av01][ext=mp4][height<=1080][height>=720]+bestaudio[ext=m4a]/"
    "best[vcodec!*=av01][ext=mp4][height<=1080][height>=720]/"
    "best[vcodec!*=av01][ext=mp4][height<=1080]/"
    "best[vcodec!*=av01]"
)


def slice_hd_remote_video_ffmpeg(
    video_url: Optional[str],
    out_mp4: Path,
    max_duration_s: float = 10.0,
    start_offset_s: float = 10.0,
) -> bool:
    """
    Slice a clean HD (`720p`) segment directly over HTTP using FFmpeg (`-ss ... -t ... -i <url>`)
    with near-lossless CRF 18 so `dmd_gif_converter` receives an un-degraded master clip.
    """
    if not video_url or not video_url.startswith(("http://", "https://")):
        return False
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    out_mp4.unlink(missing_ok=True)
    dur = max(2.0, min(120.0, float(max_duration_s)))
    ffmpeg_bin = get_ffmpeg_binary()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_offset_s:.1f}",
        "-t",
        f"{dur:.1f}",
        "-i",
        video_url,
        "-vf",
        "scale='min(1280,iw)':-2",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        str(out_mp4),
    ]
    t0 = time.monotonic()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=max(20, int(dur) + 15))
        if res.returncode == 0 and out_mp4.exists() and out_mp4.stat().st_size > 4096:
            logger.info(
                "[VIDEO HD] FFmpeg sliced clean HD clip (%.1f KB) in %.2fs from %s",
                out_mp4.stat().st_size / 1024.0,
                time.monotonic() - t0,
                video_url,
            )
            return True
    except Exception as exc:
        logger.debug("[VIDEO HD] FFmpeg HTTP slice skipped for %s: %s", video_url, exc)
    return False


def extract_hd_youtube_clip_ytdlp(
    video_page_url: Optional[str],
    out_mp4: Path,
    max_duration_s: float = 10.0,
) -> bool:
    """
    Extract a clean 720p/1080p MP4 clip from a YouTube URL (or `ytsearch1:` query) using `yt-dlp`.
    """
    if not video_page_url:
        return False
    if not video_page_url.startswith(("http://", "https://", "ytsearch")):
        return False

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    out_mp4.unlink(missing_ok=True)

    clip_dur = max(2.0, min(120.0, float(max_duration_s)))
    sec_start = 8
    sec_end = int(sec_start + round(clip_dur))
    ffmpeg_bin = get_ffmpeg_binary()
    cmd = [
        "yt-dlp",
        "--force-ipv4",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "--socket-timeout",
        "7",
        "--max-filesize",
        "60M",
        "--ffmpeg-location",
        ffmpeg_bin,
        "-S",
        "vcodec:h264,res:720,ext:mp4:m4a",
        "-f",
        HD_YTDLP_FORMAT,
        "--merge-output-format",
        "mp4",
        "--download-sections",
        f"*{sec_start}-{sec_end}",
        "-o",
        str(out_mp4),
        video_page_url,
    ]
    t0 = time.monotonic()
    try:
        logger.info("[VIDEO HD] Extracting 720p/1080p clip via yt-dlp: %s", video_page_url)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=max(24, int(clip_dur) + 15))
        if res.returncode == 0 and out_mp4.exists() and out_mp4.stat().st_size > 4096:
            logger.info(
                "[VIDEO HD] yt-dlp extracted clean HD clip (%.1f KB) in %.2fs from %s",
                out_mp4.stat().st_size / 1024.0,
                time.monotonic() - t0,
                video_page_url,
            )
            return True

        # Second attempt without --download-sections if ffmpeg section muxing is unsupported by CDN
        cmd_full = [
            "yt-dlp",
            "--force-ipv4",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--socket-timeout",
            "7",
            "--max-filesize",
            "45M",
            "--ffmpeg-location",
            ffmpeg_bin,
            "-S",
            "vcodec:h264,res:720,ext:mp4:m4a",
            "-f",
            "best[vcodec^=avc1][ext=mp4][height<=1080][height>=720]/best[vcodec!*=av01][ext=mp4][height<=720]/best[vcodec!*=av01][ext=mp4]/best[vcodec!*=av01]",
            "-o",
            str(out_mp4),
            video_page_url,
        ]
        res2 = subprocess.run(cmd_full, capture_output=True, text=True, timeout=18)
        if res2.returncode == 0 and out_mp4.exists() and out_mp4.stat().st_size > 4096:
            logger.info(
                "[VIDEO HD] yt-dlp extracted HD clip (%.1f KB) in %.2fs from %s",
                out_mp4.stat().st_size / 1024.0,
                time.monotonic() - t0,
                video_page_url,
            )
            return True
    except Exception as exc:
        logger.info("[VIDEO HD] yt-dlp extraction skipped for %s: %s", video_page_url, exc)
    return False
