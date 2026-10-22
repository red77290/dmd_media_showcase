"""
Infrastructure Adapter: `DmdConverterTranscoderAdapter` implementing `IMediaTranscoderPort`.

Delegates 100% of image panorama scrolling, Smart Color Boost (`analyze_and_compensate`),
and YOLO `Auto-Action` video transcoding to `dmd_gif_converter`, and builds a unified
sequence (`Jacket Panorama -> Marquee Panorama -> Snapshots -> Video`) for both:
- `ffmpeg -re` real-time UDP `DMDF` streaming, and
- HTTP `DMDS` `.rgb565.stream` polling.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import struct
from pathlib import Path
from typing import Dict, List, Optional

from dmd_gif_converter import read_rgb565_stream_header

from app.config import settings
from app.domain.entities import FilterCriteria, MediaCandidate, PanelGeometry
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.scheduler.asset_builder import build_static_assets_sync, build_video_assets_worker_task

logger = logging.getLogger("dmd_showcase.transcoder")


def merge_rgb565_streams(
    stream_paths: List[Path],
    out_stream_path: Path,
    width: int,
    height: int,
    fps: int,
) -> Tuple[int, float]:
    """
    Concatenate multiple `.rgb565.stream` (`DMDS`) files (e.g. Jacket Panorama + Marquee
    Panorama + Snapshots + Video Trailer) into a single continuous `DMDS` stream file.
    Returns `(total_frames, duration_s)`.
    """
    frame_bytes = width * height * 2
    payload_chunks: List[bytes] = []
    total_frames = 0
    detected_fps: Optional[int] = None

    for sp in stream_paths:
        if not sp or not sp.exists():
            continue
        raw = sp.read_bytes()
        if len(raw) <= 16:
            continue
        hdr = read_rgb565_stream_header(raw[:16])
        if hdr["width"] != width or hdr["height"] != height:
            continue
        if int(hdr.get("fps") or 0) > 0:
            detected_fps = int(hdr["fps"])
        body = raw[16:]
        n_frames = len(body) // frame_bytes
        if n_frames > 0:
            payload_chunks.append(body[: n_frames * frame_bytes])
            total_frames += n_frames

    eff_fps = max(1, min(60, int(detected_fps or fps)))
    frame_dur_ms = max(16, int(round(1000.0 / eff_fps)))
    header = struct.pack(
        "<4sBBHHHHH",
        b"DMDS",
        1,
        eff_fps,
        width,
        height,
        min(65535, total_frames),
        frame_dur_ms,
        0,
    )
    out_stream_path.parent.mkdir(parents=True, exist_ok=True)
    with out_stream_path.open("wb") as f:
        f.write(header)
        for chunk in payload_chunks:
            f.write(chunk)

    duration_s = round(total_frames / float(eff_fps), 2) if total_frames > 0 else 0.0
    return total_frames, duration_s


class DmdConverterTranscoderAdapter(IMediaTranscoderPort):
    """
    Adapter bridging `MediaCandidate` entities to `dmd_gif_converter`.
    """

    async def transcode_candidate(
        self,
        candidate: MediaCandidate,
        criteria: FilterCriteria,
        geometry: PanelGeometry,
        out_dir: Path,
    ) -> Dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        poster_w, poster_h = geometry.resolved_poster_dims()
        loop = asyncio.get_running_loop()

        files_map = await loop.run_in_executor(
            None,
            build_static_assets_sync,
            str(out_dir),
            candidate.title,
            candidate.badge,
            candidate.theme_palette,
            candidate.poster_url,
            candidate.marquee_url,
            candidate.backdrop_url,
            candidate.snapshot_urls,
            criteria.snapshot_panorama_count,
            geometry.width,
            geometry.height,
            criteria.video_fps,
            criteria.color_boost,
            poster_w,
            poster_h,
            candidate.resolved_jacket_url(),
            criteria.jacket_panorama,
            criteria.marquee_panorama,
        )

        has_video = False
        video_stream_name = ""
        video_gif_name = ""
        if criteria.include_video and (candidate.video_url or candidate.youtube_url):
            vid_res = await loop.run_in_executor(
                None,
                build_video_assets_worker_task,
                str(out_dir),
                candidate.title,
                candidate.badge,
                candidate.theme_palette,
                candidate.video_url,
                candidate.youtube_url,
                geometry.width,
                geometry.height,
                criteria.video_fps,
                criteria.video_max_duration_s,
                criteria.video_mode,
                criteria.color_boost,
                settings.worker_nice_increment,
            )
            has_video = bool(vid_res.get("has_video", False))
            if has_video:
                video_stream_name = str(vid_res.get("stream_file") or "")
                video_gif_name = str(vid_res.get("gif_file") or "")

        # Base64-encode the RGB565 mini-jacket for the JSON Card payload
        poster_rgb_path = out_dir / str(files_map["poster_rgb565"])
        poster_b64 = ""
        if poster_rgb_path.exists():
            poster_b64 = base64.b64encode(poster_rgb_path.read_bytes()).decode("ascii")

        # Build the unified stream sequence: [Jacket Panorama -> Marquee Panorama -> Snapshots -> Video]
        ordered_streams: List[Path] = []
        for pname in files_map.get("panorama_streams_rgb565") or []:  # type: ignore[union-attr]
            p_path = out_dir / str(pname)
            if p_path.exists():
                ordered_streams.append(p_path)

        if has_video and video_stream_name:
            v_path = out_dir / video_stream_name
            if v_path.exists():
                ordered_streams.append(v_path)

        unified_stream_path = out_dir / f"unified_{geometry.width}x{geometry.height}.rgb565.stream"
        total_frames, total_dur_s = await loop.run_in_executor(
            None,
            merge_rgb565_streams,
            ordered_streams,
            unified_stream_path,
            geometry.width,
            geometry.height,
            criteria.video_fps,
        )

        actual_fps = criteria.video_fps
        if unified_stream_path.exists():
            raw_hdr = unified_stream_path.read_bytes()[:16]
            if len(raw_hdr) == 16:
                parsed_hdr = read_rgb565_stream_header(raw_hdr)
                if int(parsed_hdr.get("fps") or 0) > 0:
                    actual_fps = int(parsed_hdr["fps"])

        files_map["poster_rgb565_b64"] = poster_b64
        files_map["has_panoramas"] = bool(files_map.get("panorama_streams_rgb565"))
        files_map["has_video"] = has_video
        files_map["video_gif"] = video_gif_name
        files_map["video_stream_rgb565"] = video_stream_name
        files_map["unified_stream_path"] = str(unified_stream_path)
        files_map["stream_frames"] = total_frames
        files_map["stream_fps"] = actual_fps
        files_map["stream_duration_s"] = total_dur_s
        return files_map


dmd_transcoder_adapter = DmdConverterTranscoderAdapter()
