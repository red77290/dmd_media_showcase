"""
Video Clip Extraction & `dmd_gif_converter` Transcoding Worker (`app/infrastructure/rendering/video_pipeline.py`).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import cv2
import dmd_gif_converter
import numpy as np
from PIL import Image

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
from dmd_gif_converter import DmdClipConfig, DmdMediaProcessor, write_rgb565_stream

logger = logging.getLogger("dmd_showcase.converter")

_SHOWCASE_LED_COLOR_PROFILES: Dict[str, Dict[str, float]] = {
    "cinema": {
        "base_contrast": 1.12,
        "contrast_lo": 1.05,
        "contrast_hi": 1.25,
        "contrast_scale": 0.22,
        "base_saturation": 1.18,
        "saturation_lo": 1.05,
        "saturation_hi": 1.38,
        "saturation_scale": 0.22,
        "base_gamma": 0.98,
        "gamma_lo": 0.82,
        "gamma_hi": 1.35,
        "gamma_scale": 0.60,
        "base_brightness": 0.00,
        "brightness_lo": -0.06,
        "brightness_hi": 0.06,
        "brightness_scale": 0.55,
        "sharpen_lum": 0.45,
        "sharpen_chr": 0.15,
        "dark_contrast_hi": 1.16,
        "dark_contrast_lo": 1.05,
    },
    "anime": {
        "base_contrast": 1.18,
        "contrast_lo": 1.08,
        "contrast_hi": 1.32,
        "contrast_scale": 0.25,
        "base_saturation": 1.35,
        "saturation_lo": 1.15,
        "saturation_hi": 1.60,
        "saturation_scale": 0.30,
        "base_gamma": 0.93,
        "gamma_lo": 0.78,
        "gamma_hi": 1.45,
        "gamma_scale": 0.75,
        "base_brightness": -0.01,
        "brightness_lo": -0.08,
        "brightness_hi": 0.08,
        "brightness_scale": 0.70,
        "sharpen_lum": 0.75,
        "sharpen_chr": 0.25,
        "dark_contrast_hi": 1.22,
        "dark_contrast_lo": 1.08,
    },
    "pixel_art": {
        "base_contrast": 1.25,
        "contrast_lo": 1.15,
        "contrast_hi": 1.40,
        "contrast_scale": 0.35,
        "base_saturation": 1.55,
        "saturation_lo": 1.30,
        "saturation_hi": 1.85,
        "saturation_scale": 0.40,
        "base_gamma": 0.90,
        "gamma_lo": 0.65,
        "gamma_hi": 1.55,
        "gamma_scale": 0.85,
        "base_brightness": -0.02,
        "brightness_lo": -0.10,
        "brightness_hi": 0.10,
        "brightness_scale": 0.80,
        "sharpen_lum": 1.00,
        "sharpen_chr": 0.30,
        "dark_contrast_hi": 1.30,
        "dark_contrast_lo": 1.15,
    },
}


def _interp_1d(x: float, xp: List[float], fp: List[float]) -> float:
    if x <= xp[0]:
        return float(fp[0])
    if x >= xp[-1]:
        return float(fp[-1])
    for idx in range(len(xp) - 1):
        if xp[idx] <= x < xp[idx + 1]:
            t = (x - xp[idx]) / (xp[idx + 1] - xp[idx])
            return float(fp[idx]) + t * float(fp[idx + 1] - fp[idx])
    return float(fp[-1])


def compute_calibrated_led_colorimetry(
    src_video_path: Path,
    preset_mode: str = "pixel_art",
    use_smart_color_boost: bool = True,
) -> Dict[str, float]:
    """
    Compute mode-aware LED colorimetry (`cinema`, `anime`, `pixel_art`) directly in Showcase.

    Guarantees natural live-action skin tones (`cinema` saturation `<= 1.38`), crisp cel-animation
    palettes (`anime` saturation `<= 1.60`), and vibrant arcade punch (`pixel_art` `<= 1.85`)
    without ever triggering legacy `2.5-3.3x` oversaturation even on older converter wheels.
    """
    norm_mode = str(preset_mode or "pixel_art").strip().lower()
    if norm_mode not in _SHOWCASE_LED_COLOR_PROFILES:
        norm_mode = "pixel_art"
    prof = _SHOWCASE_LED_COLOR_PROFILES[norm_mode]

    if not use_smart_color_boost or not src_video_path.exists():
        return {
            "contrast": round(prof["base_contrast"], 2),
            "saturation": round(prof["base_saturation"], 2),
            "brightness": round(prof["base_brightness"], 3),
            "gamma": round(prof["base_gamma"], 2),
            "sharpen_lum": round(prof["sharpen_lum"], 2),
            "sharpen_chr": round(prof["sharpen_chr"], 2),
        }

    frames_bgr: List[np.ndarray] = []
    cap = cv2.VideoCapture(str(src_video_path))
    if cap.isOpened():
        try:
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps_src = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
            dur_ms = (
                (total_frames / fps_src) * 1000.0
                if (total_frames > 2 and fps_src > 0)
                else 0.0
            )
            for pct in (0.25, 0.50, 0.75):
                if dur_ms > 0:
                    cap.set(cv2.CAP_PROP_POS_MSEC, dur_ms * pct)
                ok_f, frm = cap.read()
                if ok_f and frm is not None and frm.size > 0:
                    frames_bgr.append(frm)
        finally:
            cap.release()

    if not frames_bgr:
        return {
            "contrast": round(prof["base_contrast"], 2),
            "saturation": round(prof["base_saturation"], 2),
            "brightness": round(prof["base_brightness"], 3),
            "gamma": round(prof["base_gamma"], 2),
            "sharpen_lum": round(prof["sharpen_lum"], 2),
            "sharpen_chr": round(prof["sharpen_chr"], 2),
        }

    lums: List[float] = []
    stds: List[float] = []
    sats: List[float] = []
    for frm in frames_bgr:
        gray = cv2.cvtColor(frm, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hsv = cv2.cvtColor(frm, cv2.COLOR_BGR2HSV).astype(np.float32)
        lums.append(float(np.mean(gray)))
        stds.append(float(np.std(gray)))
        sats.append(float(np.mean(hsv[:, :, 1])))

    mean_lum = sum(lums) / len(lums)
    std_lum = sum(stds) / len(stds)
    mean_sat = sum(sats) / len(sats)

    d_gamma = _interp_1d(
        mean_lum,
        [0, 40, 75, 100, 140, 175, 210, 255],
        [+0.55, +0.55, +0.30, +0.10, 0.00, -0.10, -0.20, -0.30],
    )
    d_bri = _interp_1d(
        mean_lum,
        [0, 40, 80, 110, 180, 255],
        [+0.10, +0.10, +0.05, 0.00, -0.05, -0.10],
    )
    d_cont = _interp_1d(
        std_lum,
        [0, 20, 35, 50, 70, 255],
        [+0.70, +0.70, +0.45, +0.20, 0.00, -0.15],
    )
    d_sat = _interp_1d(
        mean_sat,
        [0, 10, 40, 80, 130, 180, 255],
        [+1.10, +1.10, +0.70, +0.30, 0.00, -0.30, -0.60],
    )

    gamma = max(prof["gamma_lo"], min(prof["gamma_hi"], prof["base_gamma"] + d_gamma * prof["gamma_scale"]))
    brightness = max(
        prof["brightness_lo"],
        min(prof["brightness_hi"], prof["base_brightness"] + d_bri * prof["brightness_scale"]),
    )
    contrast = max(
        prof["contrast_lo"],
        min(prof["contrast_hi"], prof["base_contrast"] + d_cont * prof["contrast_scale"]),
    )
    saturation = max(
        prof["saturation_lo"],
        min(prof["saturation_hi"], prof["base_saturation"] + d_sat * prof["saturation_scale"]),
    )

    if mean_lum < 80.0:
        dark_ratio = (80.0 - mean_lum) / 80.0
        contrast_cap = prof["dark_contrast_hi"] - dark_ratio * (
            prof["dark_contrast_hi"] - prof["dark_contrast_lo"]
        )
        contrast = min(contrast, contrast_cap)

    return {
        "contrast": round(contrast, 2),
        "saturation": round(saturation, 2),
        "brightness": round(brightness, 3),
        "gamma": round(gamma, 2),
        "sharpen_lum": round(prof["sharpen_lum"], 2),
        "sharpen_chr": round(prof["sharpen_chr"], 2),
    }


def extract_direct_rgb565_video_stream(
    src_video_path: Path,
    out_stream_path: Path,
    width: int,
    height: int,
    fps: int,
    start_s: float = 0.0,
    end_s: float = 8.0,
    use_auto_action: bool = True,
    scroll_enabled: bool = False,
    scene_type: str = "",
    color_params: Optional[Dict[str, float]] = None,
    ffmpeg_threads: int = 1,
    out_gif_path: Optional[Path] = None,
) -> Tuple[int, int, float]:
    """
    Self-contained 24-bit RGB -> 16-bit RGB565 (`DMDS`) video extractor (`0` GIF 256c quantization).

    Used automatically whenever an older `dmd_gif_converter` wheel without native direct RGB24
    stream export is installed, and directly callable for deterministic 16-bit video tests.
    """
    eff_w = max(16, int(width))
    eff_h = max(16, int(height))
    eff_fps = max(1, min(60, int(fps)))
    cal = color_params or compute_calibrated_led_colorimetry(src_video_path, "cinema", False)

    active_input = str(src_video_path)
    temp_pre_dir: Optional[str] = None
    trim_start = max(0.0, float(start_s or 0.0))
    clip_dur = max(0.2, float(end_s - trim_start)) if end_s and end_s > trim_start else 8.0
    used_auto_action_mp4 = False

    try:
        if use_auto_action and src_video_path.exists():
            try:
                from src.engine.auto_action import AutoActionConfig
                from src.engine.auto_action.pipeline import preprocess_video_for_dmd
                from src.engine.conversion.core import DEFAULT_PARAMS, LET_ME_HANDLE_PARAMS
                from src.engine.conversion.ffmpeg_utils import get_metadata

                src_w, src_h, _fps_s, _dur = get_metadata(active_input)
                ratio_matches = bool(
                    src_w
                    and src_h
                    and eff_h > 0
                    and abs((float(src_w) / float(src_h)) - (float(eff_w) / float(eff_h))) < 0.05
                )
                if src_w and src_h and not ratio_matches:
                    aa_params = {
                        **DEFAULT_PARAMS,
                        **LET_ME_HANDLE_PARAMS,
                        "target_width": eff_w,
                        "target_height": eff_h,
                        "action_smart_auto_crop": True,
                        "action_auto_detector_fallback": True,
                        **({"action_scene_type": scene_type} if scene_type else {}),
                    }
                    aa_cfg = AutoActionConfig.from_params(
                        aa_params,
                        start_s=trim_start,
                        end_s=trim_start + clip_dur,
                    )
                    ok_pre, pre_src, _msg = preprocess_video_for_dmd(active_input, aa_cfg)
                    if ok_pre and pre_src and os.path.exists(pre_src):
                        active_input = pre_src
                        temp_pre_dir = os.path.dirname(pre_src)
                        used_auto_action_mp4 = True
            except Exception as exc:
                logger.debug("[CONVERTER] Auto-action fallback to direct Lanczos crop: %s", exc)

        eq_chain = (
            f"eq=contrast={cal['contrast']}:saturation={cal['saturation']}:"
            f"brightness={cal['brightness']}:gamma={cal['gamma']},"
            f"unsharp=5:5:{cal['sharpen_lum']}:3:3:{cal['sharpen_chr']}"
        )

        if used_auto_action_mp4:
            vf = f"setpts=PTS-STARTPTS,fps={eff_fps},scale={eff_w}:{eff_h}:flags=lanczos,format=rgb24,{eq_chain}"
        elif scroll_enabled:
            if eff_h > eff_w:
                pan_w = eff_w + max(16, eff_w // 2)
                vf = (
                    f"setpts=PTS-STARTPTS,fps={eff_fps},"
                    f"scale={pan_w}:{eff_h}:flags=lanczos:force_original_aspect_ratio=increase,"
                    f"crop={pan_w}:{eff_h},"
                    f"crop={eff_w}:{eff_h}:'min(in_w-out_w,n)':0,format=rgb24,{eq_chain}"
                )
            else:
                pan_h = eff_h + max(16, eff_h // 2)
                vf = (
                    f"setpts=PTS-STARTPTS,fps={eff_fps},"
                    f"scale={eff_w}:{pan_h}:flags=lanczos:force_original_aspect_ratio=increase,"
                    f"crop={eff_w}:{pan_h},"
                    f"crop={eff_w}:{eff_h}:0:'min(in_h-out_h,n)',format=rgb24,{eq_chain}"
                )
        else:
            vf = (
                f"setpts=PTS-STARTPTS,fps={eff_fps},"
                f"scale={eff_w}:{eff_h}:flags=lanczos:force_original_aspect_ratio=increase,"
                f"crop={eff_w}:{eff_h},format=rgb24,{eq_chain}"
            )

        cmd: List[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", str(max(1, int(ffmpeg_threads)))]
        if not used_auto_action_mp4:
            if trim_start > 0.0:
                cmd += ["-ss", f"{trim_start:.3f}"]
            cmd += ["-t", f"{clip_dur:.3f}"]
        cmd += ["-i", active_input, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

        proc = subprocess.run(cmd, capture_output=True, timeout=90)
        if proc.returncode != 0 or not proc.stdout:
            return 0, eff_fps, 0.0

        frame_rgb24_bytes = eff_w * eff_h * 3
        n_frames = len(proc.stdout) // frame_rgb24_bytes
        if n_frames <= 0:
            return 0, eff_fps, 0.0

        frames_arr = np.frombuffer(
            proc.stdout[: n_frames * frame_rgb24_bytes],
            dtype=np.uint8,
        ).reshape((n_frames, eff_h, eff_w, 3))
        frame_list = [frames_arr[idx] for idx in range(n_frames)]
        written = write_rgb565_stream(
            frame_list,
            out_stream_path,
            fps=eff_fps,
        )
        if out_gif_path is not None and frame_list:
            pil_frames = [Image.fromarray(f, mode="RGB") for f in frame_list]
            frame_dur_ms = max(20, int(round(1000.0 / float(eff_fps))))
            out_gif_path.parent.mkdir(parents=True, exist_ok=True)
            pil_frames[0].save(
                out_gif_path,
                format="GIF",
                save_all=True,
                append_images=pil_frames[1:],
                duration=frame_dur_ms,
                loop=0,
                disposal=2,
            )
        return written, eff_fps, round(written / float(max(1, eff_fps)), 2)
    finally:
        if temp_pre_dir and os.path.isdir(temp_pre_dir):
            shutil.rmtree(temp_pre_dir, ignore_errors=True)


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

    supports_native_direct_stream = bool(
        getattr(dmd_gif_converter, "SUPPORTS_DIRECT_RGB565_STREAM", False)
    )
    cal_color: Optional[Dict[str, float]] = None
    if supports_native_direct_stream:
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
    else:
        cal_color = compute_calibrated_led_colorimetry(
            src_video_path=src_video_path,
            preset_mode=preset_mode,
            use_smart_color_boost=use_smart_color_boost,
        )
        clip_cfg = DmdClipConfig(
            width=width,
            height=height,
            fps=fps,
            max_duration_s=max_duration_s,
            mode="custom",
            use_ai_moments=True,
            use_auto_action=use_auto_action,
            use_smart_color_boost=False,
            scroll_enabled=scroll_on_video,
            brightness=cal_color["brightness"],
            contrast=cal_color["contrast"],
            saturation=cal_color["saturation"],
            gamma=cal_color["gamma"],
            **clip_ctx_kwargs,
        )

    processor = DmdMediaProcessor()
    stem = f"video_{width}x{height}"
    stream_out_path = out_dir / f"{stem}.rgb565.stream"
    gif_out_path = out_dir / f"{stem}.gif"
    t0 = time.monotonic()
    try:
        res = processor.process_video_trailer(
            input_video=src_video_path,
            output_dir=out_dir,
            config=clip_cfg,
            export_gif=True,
            export_rgb565_stream=supports_native_direct_stream,
            stem=stem,
        )
        final_frames = int(res.frame_count)
        final_fps = int(res.fps)
        final_duration_s = float(res.duration_s)

        if not supports_native_direct_stream and final_frames > 0:
            sel_start = float(getattr(res, "selected_start_s", 0.0) or 0.0)
            sel_end = float(
                getattr(res, "selected_end_s", 0.0) or (sel_start + max_duration_s)
            )
            direct_frames, direct_fps, direct_dur = extract_direct_rgb565_video_stream(
                src_video_path=src_video_path,
                out_stream_path=stream_out_path,
                width=width,
                height=height,
                fps=fps,
                start_s=sel_start,
                end_s=sel_end,
                use_auto_action=use_auto_action,
                scroll_enabled=scroll_on_video,
                scene_type=scene_hint,
                color_params=cal_color,
                ffmpeg_threads=ffmpeg_threads,
                out_gif_path=gif_out_path,
            )
            if direct_frames > 0:
                final_frames = direct_frames
                final_fps = direct_fps
                final_duration_s = direct_dur

        if final_frames > 1:
            stabilize_rgb565_stream_temporal_dither(
                stream_out_path,
                r_tol=1,
                g_tol=2,
                b_tol=1,
            )
        logger.info(
            "[CONVERTER] Encoded real video for '%s' -> %dx%d @ %dfps | frames=%d (%.1fs) | score=%d | took %.2fs",
            title,
            width,
            height,
            final_fps,
            final_frames,
            final_duration_s,
            res.quality_score,
            time.monotonic() - t0,
        )
        return {
            "has_video": final_frames > 0,
            "frame_count": final_frames,
            "fps": final_fps,
            "duration_s": final_duration_s,
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
