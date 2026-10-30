"""
Regression Tests for True 16-bit RGB565 Video Transcoding, Mode-Calibrated LED Colorimetry,
Cache Pipeline Invalidation (`true565_dmdz_v2`), WebSocket 50 ms Lead Pacing, and Dockerfile Parity.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import List, Tuple

import cv2
import dmd_gif_converter
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app.domain.entities import (
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.domain.on_demand_cache_key import (
    STREAM_PIPELINE_VERSION,
    build_on_demand_request_cache_key,
    compute_criteria_render_signature,
)
from app.main import app, consume_once_orchestrator
from app.infrastructure.http.routers import consume_once
from app.infrastructure.persistence.on_demand_cache_store import FileOnDemandCacheStore
from app.infrastructure.persistence.server_config_store import ServerConfigStore
from app.infrastructure.rendering import video_pipeline
from app.infrastructure.rendering.dmdz_codec import (
    HEADER_FMT,
    decode_dmdz_bytes,
    encode_dmdz_bytes,
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


def _write_skin_tone_mp4(path: Path, width: int = 320, height: int = 180, frames: int = 30, fps: int = 15) -> None:
    """Create a natural warm live-action skin-tone & midtone MP4 (`RGB=(198, 148, 122)` center)."""
    rng = np.random.default_rng(101)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, float(fps), (width, height))
    assert writer.isOpened()
    try:
        y_grad = np.linspace(-18.0, 18.0, height, dtype=np.float32)[:, None]
        x_grad = np.linspace(-22.0, 22.0, width, dtype=np.float32)[None, :]
        for idx in range(frames):
            grain = rng.integers(-3, 4, size=(height, width), dtype=np.int16).astype(np.float32)
            bgr = np.zeros((height, width, 3), dtype=np.uint8)
            # Warm background studio lighting (>8 KB MP4 size with natural film grain)
            bgr[:, :, 2] = np.clip(110.0 + x_grad + grain, 0, 255).astype(np.uint8)
            bgr[:, :, 1] = np.clip(92.0 + y_grad + grain, 0, 255).astype(np.uint8)
            bgr[:, :, 0] = np.clip(82.0 - 0.5 * x_grad + grain, 0, 255).astype(np.uint8)
            # Natural warm skin-tone portrait region in the center (`RGB ~ (198, 148, 122)`)
            sx = 110 + (idx % 5) * 2
            bgr[35:145, sx : sx + 96, 2] = 198
            bgr[35:145, sx : sx + 96, 1] = 148
            bgr[35:145, sx : sx + 96, 0] = 122
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


@pytest.mark.parametrize("native_direct_flag", [True, False])
def test_video_pipeline_produces_true_16bit_rgb565_stream_and_compact_dmdz_on_both_wheel_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_direct_flag: bool,
) -> None:
    """
    Verify `build_video_assets_worker_task` emits true >500-color 16-bit RGB565 frames (never
    256-color GIF-quantized frames) and shrinks `DMDZ` delta frames compared to 256-color dithered
    GIF round-trips, both with `SUPPORTS_DIRECT_RGB565_STREAM=True` and `False` (legacy wheel).
    """
    src_mp4 = tmp_path / "gradient_clip.mp4"
    _write_smooth_gradient_mp4(src_mp4, width=320, height=180, frames=24, fps=15)
    mp4_bytes = src_mp4.read_bytes()

    monkeypatch.setattr(dmd_gif_converter, "SUPPORTS_DIRECT_RGB565_STREAM", native_direct_flag, raising=False)
    monkeypatch.setattr(
        video_pipeline,
        "resolve_active_downloader",
        lambda: (lambda _url, timeout=12.0: mp4_bytes),
    )

    out_dir = tmp_path / f"pkg_native_{int(native_direct_flag)}"
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

    # Confirm a 256-color Floyd-Steinberg GIF round-trip of the same frames caps colors <= 256
    # AND bloats DMDZ delta frames due to inter-frame palette dithering boil.
    true_dmdz = encode_dmdz_bytes(raw_stream)
    true_deltas = _parse_dmdz_delta_sizes(true_dmdz)

    # Simulate legacy 256-color dithered per-frame GIF quantization round-trip on the same frames
    dithered_gif_path = tmp_path / f"legacy_256c_{int(native_direct_flag)}.gif"
    pil_dithered: List[Image.Image] = []
    for idx in range(n_frames):
        f565 = frames_565[idx]
        r8 = (((f565 >> 11) & 0x1F) << 3).astype(np.uint8)
        g8 = (((f565 >> 5) & 0x3F) << 2).astype(np.uint8)
        b8 = ((f565 & 0x1F) << 3).astype(np.uint8)
        rgb_img = Image.fromarray(np.stack([r8, g8, b8], axis=-1), mode="RGB")
        # Slight per-frame palette shift + Floyd-Steinberg dithering (matches legacy paletteuse)
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
    legacy_stream_path = tmp_path / f"legacy_256c_{int(native_direct_flag)}.rgb565.stream"
    write_rgb565_stream(legacy_frames_rgb, legacy_stream_path, fps=15)
    legacy_raw = legacy_stream_path.read_bytes()
    legacy_565 = np.frombuffer(legacy_raw[16 : 16 + n_frames * w * h * 2], dtype="<u2").reshape(n_frames, h, w)
    max_legacy_colors = max(int(np.unique(legacy_565[i]).size) for i in range(n_frames))
    assert max_legacy_colors <= 256

    legacy_dmdz = encode_dmdz_bytes(legacy_raw)
    legacy_deltas = _parse_dmdz_delta_sizes(legacy_dmdz)
    assert sum(true_deltas) < sum(legacy_deltas) * 0.65, (
        f"Expected true 16-bit stabilized DMDZ deltas ({sum(true_deltas)} B) to be much smaller "
        f"than 256c dithered GIF deltas ({sum(legacy_deltas)} B)"
    )


def test_calibrated_domain_colorimetry_preserves_skin_tones_without_neon_clipping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify `movies`/`series` (`cinema`), `anime`, and `games` (`pixel_art`) resolve to calibrated
    LED saturation ceilings (`<= 1.38`, `<= 1.60`, `<= 1.85`) and never clip warm skin tones into
    neon red/green (`2.5-3.3x` saturation bug).
    """
    skin_mp4 = tmp_path / "skin_tones.mp4"
    _write_skin_tone_mp4(skin_mp4)
    mp4_bytes = skin_mp4.read_bytes()

    expected_bounds: List[Tuple[str, str, float, float]] = [
        ("movies", "cinema", 1.05, 1.38),
        ("series", "cinema", 1.05, 1.38),
        ("anime", "anime", 1.15, 1.60),
        ("games", "pixel_art", 1.30, 1.85),
    ]
    for dom, exp_mode, sat_lo, sat_hi in expected_bounds:
        _aa, _scroll, smart_cb, mode = resolve_framing_and_color_params(
            video_mode="auto_action",
            color_boost="auto",
            domain=dom,
        )
        assert smart_cb is True
        assert mode == exp_mode
        cal = video_pipeline.compute_calibrated_led_colorimetry(
            src_video_path=skin_mp4,
            preset_mode=mode,
            use_smart_color_boost=True,
        )
        assert sat_lo <= cal["saturation"] <= sat_hi

    # End-to-end transcode a live-action movie clip and verify center skin-tone pixels stay natural
    monkeypatch.setattr(
        video_pipeline,
        "resolve_active_downloader",
        lambda: (lambda _url, timeout=12.0: mp4_bytes),
    )
    out_dir = tmp_path / "movie_skin_check"
    res = video_pipeline.build_video_assets_worker_task(
        item_dir=str(out_dir),
        title="Dune Part Two",
        badge="MOVIE",
        palette=(220, 160, 90),
        video_url="http://example.invalid/skin.mp4",
        width=128,
        height=32,
        fps=15,
        max_duration_s=1.5,
        video_mode="center_crop",
        color_boost="auto",
        domain="movies",
    )
    assert res["has_video"] is True
    raw = (out_dir / str(res["stream_file"])).read_bytes()
    f0 = np.frombuffer(raw[16 : 16 + 128 * 32 * 2], dtype="<u2").reshape(32, 128)
    patch = f0[10:22, 52:76]
    mean_r5 = float(np.mean((patch >> 11) & 0x1F))
    mean_g6 = float(np.mean((patch >> 5) & 0x3F))
    mean_b5 = float(np.mean(patch & 0x1F))
    # Natural warm skin tone (`RGB ~ 198, 148, 122` -> `R5~24, G6~37, B5~15`):
    # Red must NOT clip to 31 and Green/Blue must NOT collapse toward 0!
    assert 18.0 <= mean_r5 <= 29.5
    assert 26.0 <= mean_g6 <= 48.0
    assert 9.0 <= mean_b5 <= 22.0


def test_on_demand_cache_store_invalidates_stale_pre_true565_v2_packages(tmp_path: Path) -> None:
    """
    Verify `STREAM_PIPELINE_VERSION == 'true565_dmdz_v2'` participates in render signatures AND
    `FileOnDemandCacheStore.checkout_to_dir` evicts legacy pre-v2 cached packages on disk.
    """
    assert STREAM_PIPELINE_VERSION == "true565_dmdz_v2"
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

    # Fresh v2 checkout succeeds
    checkout_v2 = store.checkout_to_dir(cache_key, tmp_path / "dst_v2", expected_signature=sig)
    assert checkout_v2 is not None

    # Downgrade `cache_meta.json` on disk to simulate a stale 256-color pre-v2 cached entry
    meta_files = list((tmp_path / "od_cache").rglob("cache_meta.json"))
    assert len(meta_files) == 1
    stale_meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    stale_meta.pop("stream_pipeline_version", None)
    meta_files[0].write_text(json.dumps(stale_meta), encoding="utf-8")

    checkout_stale = store.checkout_to_dir(cache_key, tmp_path / "dst_stale", expected_signature=sig)
    assert checkout_stale is None
    assert not meta_files[0].exists()


def test_ws_pacing_lead_50ms_credit_clamping_and_dockerfile_deflate_parity(tmp_path: Path) -> None:
    """
    Verify:
    1. `DMDZ_WS_SEND_LEAD_S == 0.05`, `DMDZ_SUBPACKET_BYTES == 1024`, `DMDZ_WS_INITIAL_CREDITS == 2`.
    2. Out-of-range `?credits=0` and `?credits=99` query params are clamped safely without 1008/422.
    3. `Dockerfile` `CMD` passes `--ws-per-message-deflate false` matching `app/main.py:run()`.
    """
    assert consume_once.DMDZ_WS_SEND_LEAD_S == 0.05
    assert consume_once.DMDZ_SUBPACKET_BYTES == 1024
    assert consume_once.DMDZ_WS_INITIAL_CREDITS == 2

    dockerfile_text = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")
    assert '"--ws-per-message-deflate", "false"' in dockerfile_text

    item_id = "ws_clamp_credits_item"
    item_dir = tmp_path / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    stream_path = item_dir / f"{item_id}.rgb565.stream"
    frames_rgb = [
        np.full((32, 128, 3), fill_value=30 + k * 20, dtype=np.uint8)
        for k in range(4)
    ]
    write_rgb565_stream(frames_rgb, stream_path, fps=30)
    expected_565 = np.frombuffer(stream_path.read_bytes()[16:], dtype="<u2").reshape(4, 32, 128)

    cand = MediaCandidate(
        id="tmdb_ws",
        domain="movies",
        source_id="tmdb",
        title="Tron",
        year="1982",
        badge="MOVIE",
        badge_color="#00E5FF",
        subtitle="Sci-Fi",
        rating="7.6",
        synopsis="Grid.",
    )
    card = ShowcaseCard(
        item_id=item_id,
        domain="movies",
        title="Tron",
        year="1982",
        badge="MOVIE",
        badge_color="#00E5FF",
        subtitle="Sci-Fi",
        rating="7.6",
        synopsis="Grid.",
        lang="en",
        source_used="tmdb",
        poster_width=32,
        poster_height=32,
        poster_rgb565_b64="",
        has_video=True,
        stream_frames=4,
        stream_fps=30,
        stream_duration_s=0.13,
    )
    consume_once_orchestrator.active_packages[item_id] = PreparedShowcasePackage(
        item_id=item_id,
        dmd_id="esp32_clamp_dmd",
        domain="movies",
        candidate=cand,
        geometry=PanelGeometry(width=128, height=32),
        card=card,
        item_dir=item_dir,
        unified_stream_path=stream_path,
        consumed=True,
    )
    try:
        with TestClient(app) as tc:
            for raw_credits in (0, 99):
                rx_chunks: List[bytes] = []
                with tc.websocket_connect(
                    f"/api/v1/movies/ws/{item_id}.dmdz?purge=false&client_id=esp32_clamp_dmd&credits={raw_credits}"
                ) as ws:
                    while True:
                        try:
                            chunk = ws.receive_bytes()
                        except WebSocketDisconnect:
                            break
                        assert 0 < len(chunk) <= 1024
                        rx_chunks.append(chunk)
                        ws.send_bytes(b"A")
                decoded = decode_dmdz_bytes(b"".join(rx_chunks))
                assert np.array_equal(decoded, expected_565)
    finally:
        consume_once_orchestrator.active_packages.pop(item_id, None)


def test_vectorized_dmdz_encoder_lossless_edge_cases_and_1522_frame_throughput() -> None:
    """
    Verify vectorized `encode_frame_ops` / `encode_dmdz_bytes`:
    1. Reconstructs 100% bit-exact RGB565 pixels across keyframes, `dy > 0`/`dy < 0` scroll frames,
       `RUN` crossing `cur == ref` indices, `DIFF`, `INDEX`, `LIT`, and `LONG SKIP` (`> 64` & full-frame).
    2. Encodes a 61 s (`1,522`-frame) `128x32` movie stream rapidly (`< 1.2s`).
    """
    import time

    from app.infrastructure.rendering.dmdz_codec import scrolled_reference

    rng = np.random.default_rng(2026)
    h, w = 32, 128

    # Craft adversarial sequence: keyframe -> identical frame (full-frame LONG SKIP) ->
    # RUN crossing ref match -> vertical scroll (+3 and -2 with noisy HUD row) -> random delta
    f0 = rng.integers(0, 65536, size=(h, w), dtype="<u2")
    f1 = f0.copy()
    f2 = f0.copy()
    # Force a RUN of `last` starting at a `cur != ref` pixel and crossing over `cur == ref` pixels
    f2[0, 10] = np.uint16(0x1234)
    f0[0, 9] = np.uint16(0x1234)
    f0[0, 11:18] = np.uint16(0x1234)
    f2[0, 11:25] = np.uint16(0x1234)
    f3 = scrolled_reference(f2, 3)
    f3[0, :20] ^= np.uint16(0x0021)  # HUD noise on top row
    f4 = scrolled_reference(f3, -2)
    f4[-1, 40:70] = np.uint16(0xF800)

    adv_frames = np.stack([f0, f1, f2, f3, f4]).astype("<u2")
    raw_adv = struct.pack(HEADER_FMT, b"DMDS", 1, 25, w, h, adv_frames.shape[0], 40, 0) + adv_frames.tobytes()
    dmdz_adv = encode_dmdz_bytes(raw_adv)
    dec_adv = decode_dmdz_bytes(dmdz_adv)
    assert np.array_equal(dec_adv, adv_frames)

    # Throughput benchmark on realistic 1,522-frame (61 s @ 25 FPS) panorama + video sequence
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
    assert elapsed_s < 1.2, f"Expected 1,522-frame DMDZ encode < 1.2s, took {elapsed_s:.3f}s"
    assert np.array_equal(decode_dmdz_bytes(dmdz_movie[:16] + dmdz_movie[16:]), movie_frames)


@pytest.mark.asyncio
async def test_transcode_candidate_and_ws_resolver_isolate_dmdz_encoding_from_main_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify `DmdConverterTranscoderAdapter.transcode_candidate` NEVER calls `loop.run_in_executor(None, ...)`
    when `batch_manager.shared_executor()` is active, and `_resolve_dmdz_sibling_nonblocking` returns
    pre-encoded `.dmdz.stream` siblings in O(1) without spawning executor work.
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
        assert all(ex is fake_pool for ex, _fn in executor_targets), (
            f"Expected all executor calls to target shared_executor (never None), got {executor_targets}"
        )
        assert any(fn == finalize_unified_stream_worker_task.__name__ for _ex, fn in executor_targets)

        unified = Path(str(res["unified_stream_path"]))
        executor_targets.clear()
        resolved = await consume_once._resolve_dmdz_sibling_nonblocking(unified)
        assert resolved is not None and resolved.exists()
        assert executor_targets == [], "Up-to-date DMDZ sibling must resolve via O(1) stat with zero executor hop"
    finally:
        fake_pool.shutdown(wait=True)
