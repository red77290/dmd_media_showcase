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
from typing import Dict, List, Optional, Tuple

from dmd_gif_converter import read_rgb565_stream_header

from app.config import settings
from app.domain.entities import FilterCriteria, MediaCandidate, PanelGeometry
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.scheduler.asset_builder import (
    build_card_stream_sync,
    build_static_assets_sync,
    build_video_assets_worker_task,
)

logger = logging.getLogger("dmd_showcase.transcoder")


def merge_rgb565_streams_with_chapters(
    labeled_streams: List[Tuple[str, str, Path]],
    out_stream_path: Path,
    width: int,
    height: int,
    fps: int,
) -> Tuple[int, float, List[Dict[str, object]]]:
    """
    Concatenate labeled `.rgb565.stream` (`DMDS`) segments into a single continuous `DMDS` file
    while recording frame-accurate chapter offsets (`start_frame`, `frames`, `duration_s`).
    """
    frame_bytes = width * height * 2
    payload_chunks: List[bytes] = []
    chapters: List[Dict[str, object]] = []
    total_frames = 0
    detected_fps: Optional[int] = None

    for ch_id, ch_label, sp in labeled_streams:
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
            eff_seg_fps = max(1, min(60, int(detected_fps or fps)))
            chapters.append(
                {
                    "id": ch_id,
                    "label": ch_label,
                    "start_frame": total_frames,
                    "frames": n_frames,
                    "duration_s": round(n_frames / float(eff_seg_fps), 2),
                }
            )
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
    return total_frames, duration_s, chapters


def merge_rgb565_streams(
    stream_paths: List[Path],
    out_stream_path: Path,
    width: int,
    height: int,
    fps: int,
) -> Tuple[int, float]:
    """
    Concatenate multiple `.rgb565.stream` (`DMDS`) files into a single continuous `DMDS` stream file.
    Returns `(total_frames, duration_s)`.
    """
    labeled = [(f"seg_{idx}", sp.stem, sp) for idx, sp in enumerate(stream_paths)]
    total_frames, duration_s, _ = merge_rgb565_streams_with_chapters(
        labeled, out_stream_path, width, height, fps
    )
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
        from app.scheduler.worker_pool import batch_manager

        exec_pool = batch_manager.shared_executor()

        eff_fps = max(6, min(int(settings.video_max_fps_limit), int(criteria.video_fps)))
        eff_max_dur = max(
            1.5,
            min(float(settings.video_max_duration_limit_s), float(criteria.video_max_duration_s)),
        )

        eff_pan_dur = float(criteria.panorama_duration_s or settings.panorama_duration_s or 4.0)
        files_map = await loop.run_in_executor(
            exec_pool,
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
            eff_fps,
            criteria.color_boost,
            poster_w,
            poster_h,
            candidate.resolved_jacket_url(),
            criteria.jacket_panorama,
            criteria.marquee_panorama,
            eff_pan_dur,
        )

        has_video = False
        video_stream_name = ""
        video_gif_name = ""
        if criteria.include_video and (candidate.video_url or candidate.youtube_url):
            vid_res = await loop.run_in_executor(
                exec_pool,
                build_video_assets_worker_task,
                str(out_dir),
                candidate.title,
                candidate.badge,
                candidate.theme_palette,
                candidate.video_url,
                candidate.youtube_url,
                geometry.width,
                geometry.height,
                eff_fps,
                eff_max_dur,
                criteria.video_mode,
                criteria.color_boost,
                settings.worker_nice_increment,
                settings.ffmpeg_threads_per_job,
                settings.ytdlp_max_filesize_mb,
                candidate.domain,
                str((candidate.extra_meta or {}).get("canonical_genre") or candidate.subtitle or ""),
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

        # Optional Phase 1 server-rendered Info Card stream for Mode 2B passive UDP receivers & Studio
        card_stream_name = ""
        if criteria.render_card_in_stream and criteria.include_card_intro:
            card_stream_name = await loop.run_in_executor(
                exec_pool,
                build_card_stream_sync,
                str(out_dir),
                geometry.width,
                geometry.height,
                eff_fps,
                candidate.title,
                candidate.year,
                candidate.badge,
                candidate.badge_color,
                candidate.subtitle,
                candidate.rating,
                candidate.synopsis,
                str(files_map.get("poster_png") or ""),
                criteria.include_poster,
                criteria.include_synopsis,
                criteria.text_scroll_speed,
                criteria.include_card_intro,
                criteria.card_layout,
                criteria.card_theme,
                criteria.card_font,
                criteria.card_accent_color,
                criteria.show_badge,
                criteria.show_year,
                criteria.show_rating,
                criteria.card_hold_s,
            )

        # Build the unified stream sequence with frame-accurate chapter offsets
        labeled_streams: List[Tuple[str, str, Path]] = []
        if card_stream_name:
            c_path = out_dir / card_stream_name
            if c_path.exists():
                labeled_streams.append(("card", "1. Card Info", c_path))

        jacket_sname = str(files_map.get("jacket_stream_rgb565") or "")
        marquee_sname = str(files_map.get("marquee_stream_rgb565") or "")
        snap_snames = list(files_map.get("snapshot_streams_rgb565") or [])  # type: ignore[arg-type]
        accounted_panoramas = set()
        if jacket_sname and (out_dir / jacket_sname).exists():
            labeled_streams.append(("jacket", "2. Jacket", out_dir / jacket_sname))
            accounted_panoramas.add(jacket_sname)
        if marquee_sname and (out_dir / marquee_sname).exists():
            labeled_streams.append(("marquee", "3. Marquee", out_dir / marquee_sname))
            accounted_panoramas.add(marquee_sname)
        for s_idx, sname in enumerate(snap_snames):
            sp = out_dir / str(sname)
            if sp.exists():
                labeled_streams.append((f"snap_{s_idx + 1}", f"4. Snap #{s_idx + 1}", sp))
                accounted_panoramas.add(str(sname))
        for pname in files_map.get("panorama_streams_rgb565") or []:  # type: ignore[union-attr]
            ps = str(pname)
            if ps not in accounted_panoramas and (out_dir / ps).exists():
                labeled_streams.append(("panorama", "Panorama", out_dir / ps))

        if has_video and video_stream_name:
            v_path = out_dir / video_stream_name
            if v_path.exists():
                labeled_streams.append(("video", "5. Video", v_path))

        unified_stream_path = out_dir / f"unified_{geometry.width}x{geometry.height}.rgb565.stream"
        total_frames, total_dur_s, chapters = await loop.run_in_executor(
            None,
            merge_rgb565_streams_with_chapters,
            labeled_streams,
            unified_stream_path,
            geometry.width,
            geometry.height,
            eff_fps,
        )

        actual_fps = eff_fps
        if unified_stream_path.exists():
            raw_hdr = unified_stream_path.read_bytes()[:16]
            if len(raw_hdr) == 16:
                parsed_hdr = read_rgb565_stream_header(raw_hdr)
                if int(parsed_hdr.get("fps") or 0) > 0:
                    actual_fps = int(parsed_hdr["fps"])

        files_map["poster_rgb565_b64"] = poster_b64
        files_map["card_stream_rgb565"] = card_stream_name
        files_map["has_panoramas"] = bool(files_map.get("panorama_streams_rgb565"))
        files_map["has_video"] = has_video
        files_map["video_gif"] = video_gif_name
        files_map["video_stream_rgb565"] = video_stream_name
        files_map["unified_stream_path"] = str(unified_stream_path)
        files_map["stream_frames"] = total_frames
        files_map["stream_fps"] = actual_fps
        files_map["stream_duration_s"] = total_dur_s
        files_map["stream_chapters"] = chapters
        return files_map


dmd_transcoder_adapter = DmdConverterTranscoderAdapter()
