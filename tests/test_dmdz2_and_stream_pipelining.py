"""
Regression Tests for DMDZ v2 (`dmdz2`) Lossless Codec, 2D Motion-Compensated CIE-1931 Stabilization,
Full-MSS WebSocket Sliding-Window Chunking (`?v=2&credits=4&chunk=1360`), and Live-Stream Worker Deferral.
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import List

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.application.showcase_state import ShowcaseRuntimeState
from app.domain.entities import (
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.infrastructure.http.routers import consume_once
from app.infrastructure.rendering.dmdz_codec import (
    DMDZ_V2_VERSION,
    FRAME_RAW,
    FRAME_SCROLL_2D,
    HEADER_FMT,
    decode_dmdz_bytes,
    dmdz_sibling_path,
    encode_dmdz_bytes,
    encode_dmdz_frames,
    scrolled_reference_2d,
    stabilize_rgb565_stream_temporal_dither,
)
from app.main import app, consume_once_orchestrator
from dmd_gif_converter import write_rgb565_stream


def test_dmdz2_lossless_roundtrip_across_2d_scroll_lz77_luma_tdiff_and_raw_cap() -> None:
    """
    Verify `encode_dmdz_bytes(..., version=2)` / `decode_dmdz_bytes`:
    1. Reconstructs 100% bit-exact RGB565 pixels across `FRAME_KEY`, `FRAME_SCROLL`, `FRAME_SCROLL_2D`
       (`dx > 0`, `dx < 0`, `dy > 0`, `dy < 0`), `OP_LZ` (`3..66` px, `1..2048` dist), `OP_LUMA`,
       `OP_TLUMA`, `OP_TDIFF`, and `FRAME_RAW`.
    2. Caps worst-case incompressible random frames at `1 + width * height * 2` bytes (`8,193 B` on `128x32`).
    """
    rng = np.random.default_rng(4242)
    h, w = 32, 128

    # Frame 0: structured tilemap with repeated 8-pixel sprites -> triggers OP_LZ + OP_LUMA
    f0 = np.zeros((h, w), dtype="<u2")
    sprite = np.array([0xF800, 0xFD20, 0xFFE0, 0x07E0, 0x07FF, 0x001F, 0xF81F, 0xFFFF], dtype="<u2")
    for y in range(h):
        for x in range(0, w, 16):
            f0[y, x : x + 8] = sprite
            # Smooth luma ramp across next 8 cols -> triggers spatial OP_LUMA (`dg in [-8..7]`)
            for k in range(8):
                r5 = min(31, 4 + k)
                g6 = min(63, 8 + k * 2)
                b5 = min(31, 6 + k)
                f0[y, x + 8 + k] = np.uint16((r5 << 11) | (g6 << 5) | b5)

    # Frame 1: 2D scroll (+5, +2) with newly revealed right/bottom edges -> triggers FRAME_SCROLL_2D
    f1 = scrolled_reference_2d(f0, 5, 2)
    f1[:, -5:] = np.uint16(0x39E7)
    f1[-2:, :] = np.uint16(0x7BEF)

    # Frame 2: reverse 2D scroll (-4, -3)
    f2 = scrolled_reference_2d(f1, -4, -3)
    f2[:3, :] = sprite[0]

    # Frame 3: subtle temporal fades (`TDIFF` + `OP_TLUMA`) without spatial match
    f3 = f2.copy()
    for x in range(0, w, 2):
        p = int(f3[10, x])
        r5 = min(31, max(0, ((p >> 11) & 0x1F) + 1))
        g6 = min(63, max(0, ((p >> 5) & 0x3F) + 2))
        b5 = min(31, max(0, (p & 0x1F) + 1))
        f3[10, x] = np.uint16((r5 << 11) | (g6 << 5) | b5)
    for x in range(1, w, 2):
        p = int(f3[12, x])
        r5 = min(31, max(0, ((p >> 11) & 0x1F) + 2))
        g6 = min(63, max(0, ((p >> 5) & 0x3F) + 5))
        b5 = min(31, max(0, (p & 0x1F) + 1))
        f3[12, x] = np.uint16((r5 << 11) | (g6 << 5) | b5)

    # Frame 4: pure cryptographic-like white noise -> must trigger `FRAME_RAW` cap (`<= 8,193 B`)
    f4 = rng.integers(0, 65536, size=(h, w), dtype="<u2")

    seq = np.stack([f0, f1, f2, f3, f4]).astype("<u2")
    payloads = encode_dmdz_frames(seq, version=2)
    assert payloads[1][0] == FRAME_SCROLL_2D
    assert payloads[2][0] == FRAME_SCROLL_2D
    assert payloads[4][0] == FRAME_RAW
    assert len(payloads[4]) == 1 + h * w * 2

    raw_dmds = struct.pack(HEADER_FMT, b"DMDS", 1, 25, w, h, seq.shape[0], 40, 0) + seq.tobytes()
    encoded_v2 = encode_dmdz_bytes(raw_dmds, version=2)
    magic, ver, fps, dec_w, dec_h, total, _, _ = struct.unpack(HEADER_FMT, encoded_v2[:16])
    assert (magic, ver, fps, dec_w, dec_h, total) == (b"DMDZ", DMDZ_V2_VERSION, 25, w, h, 5)

    decoded = decode_dmdz_bytes(encoded_v2)
    assert np.array_equal(decoded, seq)


def test_dmdz2_and_2d_cie1931_stabilizer_cut_sidescrolling_gameplay_bandwidth_over_40_percent(
    tmp_path: Path,
) -> None:
    """
    Verify `stabilize_rgb565_stream_temporal_dither` + `encode_dmdz_bytes(..., version=2)` shrinks
    horizontally panning 16-bit RGB565 arcade gameplay with 1-LSB H.264 shadow boil by >= 40% vs `dmdz1`.
    """
    rng = np.random.default_rng(777)
    h, w, n_frames = 32, 128, 36
    world_w = 512

    # Build a rich 16-bit arcade stage (sky shadow gradient + parallax platforms + detailed tile patterns)
    world = np.zeros((h, world_w), dtype="<u2")
    for y in range(h):
        for x in range(world_w):
            if y < 14:
                # Dark sky shadow (`g6 in 0..9`) + stars
                r5 = 1 + ((x // 24) % 3)
                g6 = (y // 2) % 9
                b5 = 4 + (y // 3)
            elif y < 24:
                # Midground castle/jungle pillars
                brick = ((x // 6) + (y // 4)) % 5
                r5 = 8 + brick * 3
                g6 = 18 + brick * 5
                b5 = 6 + brick * 2
            else:
                # Foreground floor tiles
                tile = ((x ^ (y * 3)) & 7)
                r5 = 12 + tile * 2
                g6 = 24 + tile * 3
                b5 = 5 + tile
            world[y, x] = np.uint16((min(31, r5) << 11) | (min(63, g6) << 5) | min(31, b5))

    frames = np.empty((n_frames, h, w), dtype="<u2")
    for k in range(n_frames):
        x0 = k * 3  # 3 px/frame horizontal camera pan (75 px/s)
        crop = world[:, x0 : x0 + w].copy()
        # Add realistic 1-LSB H.264 mosquito/Lanczos jitter across ~30% of pixels
        mask = rng.random((h, w)) < 0.30
        g_jit = rng.choice(np.array([-1, 1], dtype=np.int16), size=(h, w))
        g6 = np.clip(((crop >> 5) & 0x3F).astype(np.int16) + np.where(mask, g_jit, 0), 0, 63).astype("<u2")
        crop = (crop & np.uint16(0xF81F)) | (g6 << np.uint16(5))
        # Moving player & projectile sprites
        sx = 36 + (k % 7)
        crop[16:26, sx : sx + 10] = np.uint16(0xFBE4)
        frames[k] = crop

    raw_bytes = struct.pack(HEADER_FMT, b"DMDS", 1, 25, w, h, n_frames, 40, 0) + frames.tobytes()
    dmdz1_before = encode_dmdz_bytes(raw_bytes, version=1)

    stream_file = tmp_path / "arcade_pan.rgb565.stream"
    stream_file.write_bytes(raw_bytes)
    assert stabilize_rgb565_stream_temporal_dither(stream_file) is True

    dmdz2_after = encode_dmdz_bytes(stream_file.read_bytes(), version=2)
    decoded_v2 = decode_dmdz_bytes(dmdz2_after)
    assert decoded_v2.shape == (n_frames, h, w)
    assert len(dmdz2_after) < len(dmdz1_before) * 0.58, (
        f"Expected >= 42% bandwidth reduction on side-scrolling arcade video: "
        f"dmdz1={len(dmdz1_before)} B vs stabilized dmdz2={len(dmdz2_after)} B"
    )


def test_ws_dmdz2_full_mss_chunk_negotiation_and_http_dmdz2_codec(tmp_path: Path) -> None:
    """
    Verify:
    1. `WS /api/v1/games/ws/{item_id}.dmdz?v=2&credits=4&chunk=1360` streams valid `DMDZ` v2 frames
       using up to `1,360`-byte full-MSS binary frames (reducing Wi-Fi packet rate by ~25%).
    2. `GET /api/v1/games/stream/{item_id}.rgb565.stream?codec=dmdz2` serves `DMDZ` v2 with
       `X-DMD-Codec: dmdz2` and writes `.dmdz2.stream` alongside `.rgb565.stream`.
    """
    item_id = "ws_dmdz2_mss_item"
    item_dir = tmp_path / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    stream_path = item_dir / f"{item_id}.rgb565.stream"

    rng = np.random.default_rng(99)
    frames_rgb = [rng.integers(0, 256, size=(32, 128, 3), dtype=np.uint8) for _ in range(3)]
    write_rgb565_stream(frames_rgb, stream_path, fps=25)
    expected_565 = np.frombuffer(stream_path.read_bytes()[16:], dtype="<u2").reshape(3, 32, 128)

    cand = MediaCandidate(
        id="ss_sf2",
        domain="games",
        source_id="screenscraper",
        title="Street Fighter Alpha 3",
        year="1998",
        badge="CPS2",
        badge_color="#FF4400",
        subtitle="Fighting",
        rating="9.2",
        synopsis="Fight.",
    )
    card = ShowcaseCard(
        item_id=item_id,
        domain="games",
        title="Street Fighter Alpha 3",
        year="1998",
        badge="CPS2",
        badge_color="#FF4400",
        subtitle="Fighting",
        rating="9.2",
        synopsis="Fight.",
        lang="fr",
        source_used="screenscraper",
        poster_width=32,
        poster_height=32,
        poster_rgb565_b64="",
        has_video=True,
        stream_frames=3,
        stream_fps=25,
        stream_duration_s=0.12,
    )
    consume_once_orchestrator.active_packages[item_id] = PreparedShowcasePackage(
        item_id=item_id,
        dmd_id="esp32_mss_dmd",
        domain="games",
        candidate=cand,
        geometry=PanelGeometry(width=128, height=32),
        card=card,
        item_dir=item_dir,
        unified_stream_path=stream_path,
        consumed=True,
    )
    try:
        with TestClient(app) as tc:
            rx_chunks: List[bytes] = []
            with tc.websocket_connect(
                f"/api/v1/games/ws/{item_id}.dmdz?purge=false&client_id=esp32_mss_dmd&v=2&credits=4&chunk=1360"
            ) as ws:
                while True:
                    try:
                        chunk = ws.receive_bytes()
                    except WebSocketDisconnect:
                        break
                    assert 0 < len(chunk) <= 1360
                    rx_chunks.append(chunk)
                    ws.send_bytes(b"A")

            assert any(len(c) > 1024 for c in rx_chunks), "Expected full-MSS (>1024 B) WS chunks when chunk=1360"
            ws_payload = b"".join(rx_chunks)
            assert ws_payload[4] == DMDZ_V2_VERSION
            assert np.array_equal(decode_dmdz_bytes(ws_payload), expected_565)
            assert dmdz_sibling_path(stream_path, version=2).exists()

            resp = tc.get(
                f"/api/v1/games/stream/{item_id}.rgb565.stream?purge=false&client_id=esp32_mss_dmd&paced=1&codec=dmdz2"
            )
            assert resp.status_code == 200
            assert resp.headers.get("X-DMD-Paced") == "1"
            assert resp.headers.get("X-DMD-Codec") == "dmdz2"
            assert np.array_equal(decode_dmdz_bytes(resp.content), expected_565)
    finally:
        consume_once_orchestrator.active_packages.pop(item_id, None)


@pytest.mark.asyncio
async def test_showcase_runtime_state_defers_background_transcode_during_active_live_stream() -> None:
    """
    Verify `ShowcaseRuntimeState.yield_standby_if_busy` defers both empty N+1 sibling slots (when
    multiple client slots exist) and N+2 standby slots while `active_tcp_streams > 0`, then resumes
    immediately once `active_tcp_streams == 0` or the deferral cap expires.
    """
    state = ShowcaseRuntimeState()
    geom = PanelGeometry(width=128, height=32)
    crit_movies = FilterCriteria(domain="movies")
    crit_games = FilterCriteria(domain="games")
    k_movies = ("esp32_1", "movies")
    k_games = ("esp32_1", "games")
    state.client_configs[k_movies] = (geom, crit_movies)
    state.client_configs[k_games] = (geom, crit_games)

    # Single active live stream on `k_games`: empty sibling `k_movies` defers up to 45 s
    state.active_tcp_streams = 1
    deferred, started = await state.yield_standby_if_busy(k_movies, None)
    assert deferred is True
    assert started is not None

    # Expired deferral cap (>45 s) proceeds so stuck streams can never starve preparation forever
    deferred_expired, _ = await state.yield_standby_if_busy(k_movies, time.monotonic() - 46.0)
    assert deferred_expired is False

    # Standby N+2 slot (`k_movies` already in `ready_packages`) defers up to 65 s during live stream
    state.ready_packages[k_movies] = object()  # type: ignore[assignment]
    state.ready_packages[k_games] = object()  # type: ignore[assignment]
    deferred_standby, sb_started = await state.yield_standby_if_busy(k_movies, None)
    assert deferred_standby is True
    assert sb_started is not None

    # Stream finished -> zero deferral
    state.active_tcp_streams = 0
    deferred_idle, idle_started = await state.yield_standby_if_busy(k_movies, sb_started)
    assert deferred_idle is False
    assert idle_started is None
