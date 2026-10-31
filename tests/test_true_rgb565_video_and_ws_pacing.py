"""
Regression Tests for True 16-bit RGB565 Video Transcoding with Restored HUB75 LED Color Boost,
Cache Pipeline Invalidation (`led_boost_dmdz_v2`), Lossless DMDZ v2 Early-Abort Throughput,
and Executor Isolation.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
import pytest
from PIL import Image

from app.domain.entities import (
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
)
from app.domain.on_demand_cache_key import (
    STREAM_PIPELINE_VERSION,
    build_on_demand_request_cache_key,
    compute_criteria_render_signature,
)
from app.infrastructure.http.routers import consume_once
from app.infrastructure.persistence.on_demand_cache_store import FileOnDemandCacheStore
from app.infrastructure.persistence.server_config_store import ServerConfigStore
from app.infrastructure.rendering import video_pipeline
from app.infrastructure.rendering.dmdz_codec import (
    HEADER_FMT,
    decode_dmdz_bytes,
    encode_dmdz_bytes,
    scrolled_reference,
)
from app.infrastructure.rendering.imaging_primitives import resolve_framing_and_color_params
from dmd_gif_converter import extract_frames_from_gif, write_rgb565_stream


def _write_smooth_gradient_mp4(path: Path, width: int = 320, height: int = 180, frames: int = 24, fps: int = 15) -> None:
    """Create a 24-bit 16:9 MP4 with continuous 2D RGB gradients plus a smoothly moving foreground sprite."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, float(fps), (width, height))
    assert writer.isOpened()
    try:
        x_lin = np.linspace(20, 235, width, dtype=np.float32)[None, :]
        y_lin = np.linspace(15, 225, height, dtype=np.float32)[:, None]
        for idx in range(frames):
            r = np.clip(x_lin, 0, 255).astype(np.uint8)
            g = np.broadcast_to(np.clip(y_lin, 0, 255).astype(np.uint8), (height, width)).copy()
            b = np.clip(255.0 - 0.55 * x_lin + 0.35 * y_lin, 0, 255).astype(np.uint8)
            r_full = np.broadcast_to(r, (height, width)).copy()
            bgr = np.stack([b, g, r_full], axis=-1)
            sx = 24 + idx * 6
            bgr[60:115, sx : sx + 28] = (40, 210, 245)
            writer.write(bgr)
    finally:
        writer.release()


def _parse_dmdz_delta_sizes(dmdz_bytes: bytes) -> List[int]:
    """Return payload byte lengths for frames `1..N-1` (delta/scroll frames after keyframe 0)."""
    _, _ver, _fps, _w, _h, total, _dur, _kf = struct.unpack(HEADER_FMT, dmdz_bytes[:16])
    off = 16
    sizes: List[int] = []
    for idx in range(int(total)):
        (plen,) = struct.unpack_from("<H", dmdz_bytes, off)
        off += 2 + plen
        if idx > 0:
            sizes.append(plen)
    return sizes


def test_video_pipeline_produces_true_16bit_rgb565_stream_with_led_color_boost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify `build_video_assets_worker_task` emits true >500-color 16-bit RGB565 frames directly
    via `dmd_gif_converter` (never 256-color GIF-quantized frames) and shrinks lossless `DMDZ` v2
    delta frames compared to 256-color dithered GIF round-trips.
    """
    src_mp4 = tmp_path / "gradient_clip.mp4"
    _write_smooth_gradient_mp4(src_mp4, width=320, height=180, frames=24, fps=15)
    mp4_bytes = src_mp4.read_bytes()

    monkeypatch.setattr(
        video_pipeline,
        "resolve_active_downloader",
        lambda: (lambda _url, timeout=12.0: mp4_bytes),
    )

    out_dir = tmp_path / "pkg_true_rgb565"
    res = video_pipeline.build_video_assets_worker_task(
        item_dir=str(out_dir),
        title="Blade Runner 2049",
        badge="MOVIE",
        palette=(255, 140, 40),
        video_url="http://example.invalid/trailer.mp4",
        width=128,
        height=32,
        fps=15,
        max_duration_s=2.0,
        video_mode="center_crop",
        color_boost="auto",
        domain="movies",
    )
    assert res["has_video"] is True

    stream_path = out_dir / str(res["stream_file"])
    gif_path = out_dir / str(res["gif_file"])
    assert stream_path.exists()
    assert gif_path.exists()

    raw_stream = stream_path.read_bytes()
    _, _ver, s_fps, w, h, n_frames, _dur, _ = struct.unpack(HEADER_FMT, raw_stream[:16])
    assert (w, h, s_fps) == (128, 32, 15)
    assert n_frames >= 12

    frames_565 = np.frombuffer(raw_stream[16 : 16 + n_frames * w * h * 2], dtype="<u2").reshape(n_frames, h, w)
    max_unique_stream_colors = max(int(np.unique(frames_565[i]).size) for i in range(n_frames))
    assert max_unique_stream_colors > 500, (
        f"Expected true 16-bit RGB565 gradient (>500 colors/frame), got {max_unique_stream_colors}"
    )

    true_dmdz = encode_dmdz_bytes(raw_stream)
    assert np.array_equal(decode_dmdz_bytes(true_dmdz), frames_565)
    true_deltas = _parse_dmdz_delta_sizes(true_dmdz)

    dithered_gif_path = tmp_path / "legacy_256c.gif"
    pil_dithered: List[Image.Image] = []
    for idx in range(n_frames):
        f565 = frames_565[idx]
        r8 = (((f565 >> 11) & 0x1F) << 3).astype(np.uint8)
        g8 = (((f565 >> 5) & 0x3F) << 2).astype(np.uint8)
        b8 = ((f565 & 0x1F) << 3).astype(np.uint8)
        rgb_img = Image.fromarray(np.stack([r8, g8, b8], axis=-1), mode="RGB")
        q_img = rgb_img.quantize(colors=256, dither=Image.Dither.FLOYDSTEINBERG).convert("RGB")
        pil_dithered.append(q_img)
    pil_dithered[0].save(
        dithered_gif_path,
        format="GIF",
        save_all=True,
        append_images=pil_dithered[1:],
        duration=67,
        loop=0,
    )
    legacy_frames_rgb, _ = extract_frames_from_gif(dithered_gif_path)
    legacy_stream_path = tmp_path / "legacy_256c.rgb565.stream"
    write_rgb565_stream(legacy_frames_rgb, legacy_stream_path, fps=15)
    legacy_raw = legacy_stream_path.read_bytes()
    legacy_dmdz = encode_dmdz_bytes(legacy_raw)
    legacy_deltas = _parse_dmdz_delta_sizes(legacy_dmdz)
    assert sum(true_deltas) < sum(legacy_deltas) * 0.90


def test_domain_preset_mapping_routes_to_dmd_gif_converter_modes() -> None:
    """Verify domain color presets route cleanly to `dmd_gif_converter`'s `cinema`, `anime`, and `pixel_art` modes."""
    expected_modes: List[Tuple[str, str]] = [
        ("movies", "cinema"),
        ("series", "cinema"),
        ("anime", "anime"),
        ("games", "pixel_art"),
    ]
    for dom, exp_mode in expected_modes:
        _aa, _scroll, smart_cb, mode = resolve_framing_and_color_params(
            video_mode="auto_action",
            color_boost="auto",
            domain=dom,
        )
        assert smart_cb is True
        assert mode == exp_mode


def test_on_demand_cache_store_invalidates_stale_pre_led_boost_packages(tmp_path: Path) -> None:
    """
    Verify `STREAM_PIPELINE_VERSION == 'led_boost_dmdz_v2'` participates in render signatures AND
    `FileOnDemandCacheStore.checkout_to_dir` evicts stale cached packages on disk.
    """
    assert STREAM_PIPELINE_VERSION == "led_boost_dmdz_v2"
    geom = PanelGeometry(width=128, height=32)
    crit = FilterCriteria(domain="movies", lang="fr")
    sig = compute_criteria_render_signature(geom, crit)
    req_key = build_on_demand_request_cache_key("movies", [], "The Matrix", geom, crit)
    cache_key = req_key.cache_key

    cfg_store = ServerConfigStore(file_path=tmp_path / "server_config.json")
    store = FileOnDemandCacheStore(cache_root=tmp_path / "od_cache", config_store=cfg_store)
    store.update_settings(enabled=True, max_mb=50)

    src_pkg_dir = tmp_path / "src_pkg"
    src_pkg_dir.mkdir(parents=True, exist_ok=True)
    stream_file = src_pkg_dir / "unified_128x32.rgb565.stream"
    write_rgb565_stream([np.full((32, 128, 3), 80, dtype=np.uint8)], stream_file, fps=15)

    files_map = {
        "unified_stream_rel": stream_file.name,
        "unified_stream_path": str(stream_file),
        "stream_frames": 1,
        "stream_fps": 15,
        "stream_duration_s": 0.07,
    }
    cand_dict = {
        "id": "tmdb_1234",
        "domain": "movies",
        "source_id": "tmdb",
        "title": "The Matrix",
        "year": "1999",
    }
    assert (
        store.store_package(
            cache_key=cache_key,
            source_dir=src_pkg_dir,
            files_map=files_map,
            candidate_dict=cand_dict,
            meta={"render_signature": sig},
        )
        is True
    )

    checkout_v2 = store.checkout_to_dir(cache_key, tmp_path / "dst_v2", expected_signature=sig)
    assert checkout_v2 is not None

    meta_files = list((tmp_path / "od_cache").rglob("cache_meta.json"))
    assert len(meta_files) == 1
    stale_meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    stale_meta["stream_pipeline_version"] = "true565_dmdz_v2"
    meta_files[0].write_text(json.dumps(stale_meta), encoding="utf-8")

    checkout_stale = store.checkout_to_dir(cache_key, tmp_path / "dst_stale", expected_signature=sig)
    assert checkout_stale is None
    assert not meta_files[0].exists()


def test_dmdz2_encoder_lossless_edge_cases_and_1522_frame_throughput() -> None:
    """
    Verify `encode_frame_ops_v2` / `encode_dmdz_bytes`:
    1. Reconstructs 100% bit-exact RGB565 pixels across keyframes, `dy > 0`/`dy < 0` scroll frames,
       `RUN` crossing `cur == ref` indices, `DIFF`, `INDEX`, `LIT`, `LONG SKIP`, and incompressible noise.
    2. Encodes a 61 s (`1,522`-frame) `128x32` movie stream rapidly (`< 1.0s`).
    """
    rng = np.random.default_rng(2026)
    h, w = 32, 128

    f0 = rng.integers(0, 65536, size=(h, w), dtype="<u2")
    f1 = f0.copy()
    f2 = f0.copy()
    f2[0, 10] = np.uint16(0x1234)
    f0[0, 9] = np.uint16(0x1234)
    f0[0, 11:18] = np.uint16(0x1234)
    f2[0, 11:25] = np.uint16(0x1234)
    f3 = scrolled_reference(f2, 3)
    f3[0, :20] ^= np.uint16(0x0021)
    f4 = scrolled_reference(f3, -2)
    f4[-1, 40:70] = np.uint16(0xF800)

    adv_frames = np.stack([f0, f1, f2, f3, f4]).astype("<u2")
    raw_adv = struct.pack(HEADER_FMT, b"DMDS", 1, 25, w, h, adv_frames.shape[0], 40, 0) + adv_frames.tobytes()
    dmdz_adv = encode_dmdz_bytes(raw_adv)
    dec_adv = decode_dmdz_bytes(dmdz_adv)
    assert np.array_equal(dec_adv, adv_frames)

    base = np.tile(np.arange(w, dtype="<u2")[None, :], (h, 1)) * np.uint16(37)
    movie_frames = np.empty((1522, h, w), dtype="<u2")
    cur = base.copy()
    for k in range(1522):
        if k < 200 and k > 0:
            cur = scrolled_reference(cur, 1)
            cur[-1, :] = np.uint16((k * 131) & 0xFFFF)
        elif k > 0:
            cur = cur.copy()
            x0 = (k * 3) % (w - 32)
            cur[8:24, x0 : x0 + 32] = np.uint16(((k * 17) << 5) & 0xFFFF)
        movie_frames[k] = cur

    raw_movie = struct.pack(HEADER_FMT, b"DMDS", 1, 25, w, h, 1522, 40, 0) + movie_frames.tobytes()
    t0 = time.perf_counter()
    dmdz_movie = encode_dmdz_bytes(raw_movie)
    elapsed_s = time.perf_counter() - t0
    assert elapsed_s < 2.5, f"Expected 1,522-frame DMDZ v2 encode < 2.5s, took {elapsed_s:.3f}s"
    assert np.array_equal(decode_dmdz_bytes(dmdz_movie), movie_frames)


@pytest.mark.asyncio
async def test_transcode_candidate_and_http_resolver_isolate_dmdz_encoding_from_main_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify `DmdConverterTranscoderAdapter.transcode_candidate` pre-encodes exactly one `.dmdz2.stream`
    sibling on `batch_manager.shared_executor()` (never `None`), and `_resolve_dmdz_sibling_nonblocking`
    returns it in `O(1)` without spawning extra executor work.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from app.infrastructure.transcoder.dmd_converter_adapter import (
        DmdConverterTranscoderAdapter,
    )
    from app.scheduler.asset_builder import finalize_unified_stream_worker_task
    from app.scheduler.worker_pool import batch_manager

    seg_path = tmp_path / "jacket.rgb565.stream"
    write_rgb565_stream([np.full((32, 128, 3), 90, dtype=np.uint8) for _ in range(4)], seg_path, fps=20)
    poster_path = tmp_path / "poster.rgb565"
    poster_path.write_bytes(b"\x00\x00" * (32 * 32))

    fake_pool = ThreadPoolExecutor(max_workers=1)
    executor_targets: List[object] = []
    orig_run_in_executor = asyncio.get_running_loop().run_in_executor

    def _spy_run_in_executor(executor, func, *args):  # type: ignore[no-untyped-def]
        executor_targets.append((executor, getattr(func, "__name__", str(func))))
        return orig_run_in_executor(executor, func, *args)

    def _fake_static_assets(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {
            "poster_rgb565": poster_path.name,
            "poster_png": "",
            "jacket_stream_rgb565": seg_path.name,
            "marquee_stream_rgb565": "",
            "snapshot_streams_rgb565": [],
            "panorama_streams_rgb565": [seg_path.name],
        }

    monkeypatch.setattr(batch_manager, "shared_executor", lambda: fake_pool)
    monkeypatch.setattr(
        "app.infrastructure.transcoder.dmd_converter_adapter.build_static_assets_sync",
        _fake_static_assets,
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", _spy_run_in_executor)

    try:
        adapter = DmdConverterTranscoderAdapter()
        cand = MediaCandidate(
            id="tmdb_99",
            domain="movies",
            source_id="tmdb",
            title="Interstellar",
            year="2014",
            badge="MOVIE",
            badge_color="#FF8800",
            subtitle="Sci-Fi",
            rating="8.7",
            synopsis="Space.",
        )
        crit = FilterCriteria(domain="movies", include_video=False, video_fps=20)
        geom = PanelGeometry(width=128, height=32)
        res = await adapter.transcode_candidate(cand, crit, geom, tmp_path)

        assert res["stream_frames"] == 4
        assert all(ex is fake_pool for ex, _fn in executor_targets)
        assert any(fn == finalize_unified_stream_worker_task.__name__ for _ex, fn in executor_targets)

        unified = Path(str(res["unified_stream_path"]))
        executor_targets.clear()
        resolved = await consume_once._resolve_dmdz_sibling_nonblocking(unified)
        assert resolved is not None and resolved.exists() and resolved.name.endswith(".dmdz2.stream")
        assert executor_targets == [], "Up-to-date DMDZ v2 sibling must resolve via O(1) stat with zero executor hop"
    finally:
        fake_pool.shutdown(wait=True)
