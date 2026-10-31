"""
Tests for the 3-Tier Adaptive Stream (`profile=sram|psram|psram_hd`), `FRAME_PAL8` (`0x05`) Hybrid
Codec (`<= 1,400 B` ceiling, zero drift), Unpaced HTTP `dmdz2` PSRAM Bulk Preload (`24–30 FPS` HD),
and WebSocket Header+Frame0 Coalescing & Inter-Chunk Pacing.
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import List

import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.domain.entities import (
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.infrastructure.rendering.dmdz_codec import (
    DMDZ_MAGIC,
    DMDZ_V2_VERSION,
    DMDS_MAGIC,
    FRAME_DELTA,
    FRAME_PAL8,
    FRAME_SCROLL,
    FRAME_SCROLL_2D,
    HEADER_FMT,
    decode_dmdz_bytes,
    dmdz_sibling_path,
    encode_dmdz_bytes,
    encode_dmdz_frames,
    scrolled_reference_2d,
)
from app.infrastructure.rendering.dmdz_pal8 import (
    encode_adaptive_pal8_sram,
    encode_frame_pal8_payload,
    quantize_rgb565_palette,
)
from app.main import app, consume_once_orchestrator
from dmd_gif_converter import write_rgb565_stream


def test_frame_pal8_wire_roundtrip_all_opcodes_and_2d_scroll() -> None:
    """
    Verify `FRAME_PAL8` (`0x05`) wire format byte-for-byte:
    - `pal_hdr` (`0x80` 2D scroll flag + `(pal_count - 1)` `1..128` entries),
    - optional `<bb` `(dx, dy)` 2D scroll vector,
    - `PAL_INDEX` (`0x00..0x7F`), `PAL_SKIP` (`0x80..0xBF`), `PAL_RUN` (`0xC0..0xFE`),
      and `PAL_LONG_SKIP` (`0xFF` + `<H`),
    - exact zero-drift parity between encoder `recon` and `decode_dmdz_bytes`.
    """
    h, w = 32, 128

    # Frame 0: 96 distinct palette colors arranged with horizontal runs (`PAL_INDEX` + `PAL_RUN`)
    f0 = np.zeros((h, w), dtype="<u2")
    for y in range(h):
        for b_idx in range(16):
            c_idx = (y * 3 + b_idx) % 96
            r5 = (c_idx * 5) & 0x1F
            g6 = (c_idx * 9) & 0x3F
            b5 = (c_idx * 7) & 0x1F
            f0[y, b_idx * 8 : (b_idx + 1) * 8] = np.uint16((r5 << 11) | (g6 << 5) | b5)
    # Break a few 8-pixel runs into singletons to exercise pure `PAL_INDEX` transitions
    for x in range(0, 64, 3):
        f0[0, x] = np.uint16(0xF81F if (x % 2 == 0) else 0x07E0)

    pal0, idx0 = quantize_rgb565_palette(f0.ravel(), max_colors=128)
    recon0 = pal0[idx0].reshape(h, w).astype("<u2")
    p0 = encode_frame_pal8_payload(recon0, pal0, idx0, ref_2d=None, dx=0, dy=0)
    assert p0[0] == FRAME_PAL8
    assert (p0[1] & 0x80) == 0
    assert ((p0[1] & 0x7F) + 1) == len(pal0)

    # Frame 1: 2D scroll `(dx=5, dy=-2)` + short `PAL_SKIP` + long `PAL_LONG_SKIP` (> 200 unchanged px)
    ref1 = scrolled_reference_2d(recon0, 5, -2)
    f1 = ref1.copy()
    f1[0:2, :] = np.uint16(0xFFE0)  # newly revealed top edge (`PAL_RUN`)
    f1[10, 20] = np.uint16(0xF800)  # isolated pixel after >1,000 unchanged px -> `PAL_LONG_SKIP`
    f1[10, 45] = np.uint16(0x001F)  # isolated pixel after 24 unchanged px -> `PAL_SKIP`

    ch1 = f1 != ref1
    pal1, ch1_idx = quantize_rgb565_palette(f1[ch1], max_colors=64)
    idx1 = np.zeros(h * w, dtype=np.int16)
    idx1[ch1.ravel()] = ch1_idx
    p1 = encode_frame_pal8_payload(f1, pal1, idx1, ref_2d=ref1, dx=5, dy=-2)

    assert p1[0] == FRAME_PAL8
    assert (p1[1] & 0x80) == 0x80
    assert struct.unpack_from("<bb", p1, 2) == (5, -2)
    op_stream1 = p1[4 + len(pal1) * 2 :]
    assert any(0x80 <= b <= 0xBF for b in op_stream1), "Expected short PAL_SKIP op"
    assert 0xFF in op_stream1, "Expected PAL_LONG_SKIP (0xFF) op"
    assert any(0xC0 <= b <= 0xFE for b in op_stream1), "Expected PAL_RUN op"

    # Frame 2: adaptive SRAM encoder with negative dx / positive dy scroll
    f2_raw = scrolled_reference_2d(f1, -3, 3)
    f2_raw[:, :3] = np.uint16(0x07FF)
    p2, recon2 = encode_adaptive_pal8_sram(f2_raw, f1, is_key=False, frame_cap=1400)
    assert p2[0] == FRAME_PAL8
    assert len(p2) <= 1400

    stream = bytearray(struct.pack(HEADER_FMT, DMDZ_MAGIC, DMDZ_V2_VERSION, 20, w, h, 3, 50, 0))
    for pl in (p0, p1, p2):
        stream += struct.pack("<H", len(pl)) + pl

    decoded = decode_dmdz_bytes(bytes(stream))
    assert np.array_equal(decoded[0], recon0)
    assert np.array_equal(decoded[1], f1)
    assert np.array_equal(decoded[2], recon2)


def test_sram_profile_caps_noisy_h264_action_clip_under_1400b_mixes_types_and_keeps_panoramas_lossless() -> None:
    """
    Verify on `128x32` (`8,192 B/frame` raw):
    1. A 36-frame noisy H.264 action clip with scene cuts exceeds `3,000 B/frame` in standard `dmdz2`
       yet stays **strictly `<= 1,400 B` on every single frame** under `profile='sram'`.
    2. Mixed panning + cut sequences combine `FRAME_PAL8` + `FRAME_SCROLL_2D`/`FRAME_SCROLL`/`FRAME_DELTA`
       with zero reference drift.
    3. Clean exact-scrolling panoramas remain 100% bit-for-bit lossless even under `profile='sram'`.
    """
    rng = np.random.default_rng(202610)
    h, w, n = 32, 128, 36
    big_w = w + 160
    yy, xx = np.meshgrid(np.arange(h), np.arange(big_w), indexing="ij")
    bg_a = (
        ((np.clip((xx * 31) // big_w, 0, 31).astype(np.uint16)) << 11)
        | ((np.clip((yy * 63) // h, 0, 63).astype(np.uint16)) << 5)
        | np.clip(28 - (xx * 24) // big_w, 0, 31).astype(np.uint16)
    )

    noisy_frames: List[np.ndarray] = []
    for k in range(n):
        if k == 18:
            # Worst-case full-entropy scene cut
            noisy_frames.append(rng.integers(0, 65536, size=(h, w), dtype="<u2"))
            continue
        crop = bg_a[:, k * 2 : k * 2 + w].copy()
        r = np.clip(((crop >> 11) & 0x1F).astype(np.int16) + rng.integers(-2, 3, size=(h, w)), 0, 31)
        g = np.clip(((crop >> 5) & 0x3F).astype(np.int16) + rng.integers(-3, 4, size=(h, w)), 0, 63)
        b = np.clip((crop & 0x1F).astype(np.int16) + rng.integers(-2, 3, size=(h, w)), 0, 31)
        frm = ((r.astype(np.uint16) << 11) | (g.astype(np.uint16) << 5) | b.astype(np.uint16)).astype("<u2")
        sx = (k * 4) % (w - 12)
        frm[8:18, sx : sx + 12] = np.uint16(0xFD20)
        noisy_frames.append(frm)

    arr_noisy = np.stack(noisy_frames)
    raw_noisy = struct.pack(HEADER_FMT, DMDS_MAGIC, 1, 20, w, h, n, 50, 0) + arr_noisy.tobytes()

    std_payloads = encode_dmdz_frames(arr_noisy, version=2, profile="")
    assert max(len(p) for p in std_payloads) > 3000

    sram_payloads = encode_dmdz_frames(arr_noisy, version=2, profile="sram")
    assert len(sram_payloads) == n
    assert max(len(p) for p in sram_payloads) <= 1400

    sram_stream = encode_dmdz_bytes(raw_noisy, version=2, profile="sram")
    dec_noisy = decode_dmdz_bytes(sram_stream)
    assert dec_noisy.shape == (n, h, w)

    # Mixed sequence: high-entropy cuts (`FRAME_PAL8`) + low-noise camera pans (`FRAME_SCROLL_2D` / `FRAME_DELTA`)
    mixed_frames: List[np.ndarray] = []
    stage = np.zeros((48, 220), dtype="<u2")
    for y in range(48):
        for x in range(220):
            stage[y, x] = np.uint16((((x // 6 + y // 4) & 0x1F) << 11) | (((y + x // 8) & 0x3F) << 5) | 12)
    for k in range(32):
        if k in (0, 16):
            mixed_frames.append(rng.integers(0, 65536, size=(h, w), dtype="<u2"))
        else:
            ox = (k * 2) % 72
            oy = (k // 3) % 10
            f = stage[oy : oy + h, ox : ox + w].copy()
            f[12:18, (k * 3) % 108 : ((k * 3) % 108) + 10] = np.uint16(0xFFE0)
            mixed_frames.append(f)

    mixed_arr = np.stack(mixed_frames)
    mixed_raw = struct.pack(HEADER_FMT, DMDS_MAGIC, 1, 20, w, h, len(mixed_frames), 50, 0) + mixed_arr.tobytes()
    mixed_sram = encode_dmdz_bytes(mixed_raw, version=2, profile="sram")
    mixed_dec = decode_dmdz_bytes(mixed_sram)
    assert mixed_dec.shape == mixed_arr.shape

    off = 16
    m_types = set()
    for _ in range(len(mixed_frames)):
        (ln,) = struct.unpack_from("<H", mixed_sram, off)
        assert ln <= 1400
        m_types.add(mixed_sram[off + 2])
        off += 2 + ln
    assert FRAME_PAL8 in m_types
    assert bool(m_types & {FRAME_DELTA, FRAME_SCROLL, FRAME_SCROLL_2D})

    # Clean exact-scrolling panorama must stay 100% bit-exact lossless even under `profile='sram'`
    pano_canvas = np.zeros((64, 128), dtype="<u2")
    for y in range(64):
        pano_canvas[y, :] = np.uint16((((y // 3) * 2) << 5) | min(31, 4 + y // 2))
    pano_canvas[12:24, 20:60] = np.uint16((28 << 11) | (44 << 5) | 4)
    pano_canvas[30:44, 64:112] = np.uint16((6 << 11) | (52 << 5) | 26)
    pano_seq = np.stack([pano_canvas[y : y + 32] for y in range(20)]).astype("<u2")
    pano_raw = struct.pack(HEADER_FMT, DMDS_MAGIC, 1, 20, 128, 32, 20, 50, 0) + pano_seq.tobytes()
    pano_sram = encode_dmdz_bytes(pano_raw, version=2, profile="sram")
    assert np.array_equal(decode_dmdz_bytes(pano_sram), pano_seq)


def test_http_unpaced_dmdz2_psram_and_psram_hd_bulk_preload_and_rpi_raw_dmds_compat(tmp_path: Path) -> None:
    """
    Verify:
    1. `GET /api/v1/movies/stream/{item_id}.rgb565.stream?purge=false&paced=0&codec=dmdz2&profile=psram_hd`
       serves unpaced True-RGB565 `DMDZ` v2 at `256x64 @ 30 FPS` with accurate `Content-Length`,
       `X-DMD-Codec: dmdz2`, `X-DMD-Profile: psram_hd`, and keeps the package alive (`purge=false`).
    2. `profile=psram` echoes `X-DMD-Profile: psram` and shares the lossless `.dmdz2.stream` sibling.
    3. `ArcadeMatrix_RPi` (`?purge=false` without `codec`) receives untouched raw `DMDS` bytes.
    """
    item_id = "psram_hd_preload_item"
    item_dir = tmp_path / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    stream_path = item_dir / f"{item_id}.rgb565.stream"

    # 256x64 @ 30 FPS HD panel sequence
    frames_rgb = [
        np.full((64, 256, 3), fill_value=25 + k * 35, dtype=np.uint8)
        for k in range(5)
    ]
    write_rgb565_stream(frames_rgb, stream_path, fps=30)
    raw_dmds = stream_path.read_bytes()
    expected_565 = np.frombuffer(raw_dmds[16:], dtype="<u2").reshape(5, 64, 256)

    cand = MediaCandidate(
        id="tmdb_hd",
        domain="movies",
        source_id="tmdb",
        title="Spider-Verse",
        year="2023",
        badge="MOVIE",
        badge_color="#FF1744",
        subtitle="Animation",
        rating="8.6",
        synopsis="Multiverse.",
    )
    card = ShowcaseCard(
        item_id=item_id,
        domain="movies",
        title="Spider-Verse",
        year="2023",
        badge="MOVIE",
        badge_color="#FF1744",
        subtitle="Animation",
        rating="8.6",
        synopsis="Multiverse.",
        lang="en",
        source_used="tmdb",
        poster_width=64,
        poster_height=64,
        poster_rgb565_b64="",
        has_video=True,
        stream_frames=5,
        stream_fps=30,
        stream_duration_s=0.17,
    )
    consume_once_orchestrator.active_packages[item_id] = PreparedShowcasePackage(
        item_id=item_id,
        dmd_id="esp32s3_hd_dmd",
        domain="movies",
        candidate=cand,
        geometry=PanelGeometry(width=256, height=64),
        card=card,
        item_dir=item_dir,
        unified_stream_path=stream_path,
        consumed=True,
    )
    try:
        with TestClient(app) as tc:
            # 1. ESP32-S3 HD PSRAM unpaced bulk preload (`profile=psram_hd`, `256x64 @ 30 FPS`)
            r_hd = tc.get(
                f"/api/v1/movies/stream/{item_id}.rgb565.stream"
                "?purge=false&paced=0&codec=dmdz2&profile=psram_hd"
            )
            assert r_hd.status_code == 200
            assert r_hd.headers.get("X-DMD-Paced") is None
            assert r_hd.headers.get("X-DMD-Codec") == "dmdz2"
            assert r_hd.headers.get("X-DMD-Profile") == "psram_hd"
            assert int(r_hd.headers["Content-Length"]) == len(r_hd.content)
            magic, ver, fps, w, h, total, _, _ = struct.unpack(HEADER_FMT, r_hd.content[:16])
            assert (magic, ver, fps, w, h, total) == (DMDZ_MAGIC, DMDZ_V2_VERSION, 30, 256, 64, 5)
            assert np.array_equal(decode_dmdz_bytes(r_hd.content), expected_565)
            assert item_id in consume_once_orchestrator.active_packages

            # 2. Standard ESP32-S3 PSRAM bulk preload (`profile=psram`)
            r_ps = tc.get(
                f"/api/v1/movies/stream/{item_id}.rgb565.stream"
                "?purge=false&paced=0&codec=dmdz2&profile=psram"
            )
            assert r_ps.status_code == 200
            assert r_ps.headers.get("X-DMD-Codec") == "dmdz2"
            assert r_ps.headers.get("X-DMD-Profile") == "psram"
            assert r_ps.content == r_hd.content

            # 3. ArcadeMatrix_RPi raw DMDS preload (`?purge=false`, no `codec`)
            r_rpi = tc.get(f"/api/v1/movies/stream/{item_id}.rgb565.stream?purge=false")
            assert r_rpi.status_code == 200
            assert r_rpi.headers.get("X-DMD-Codec") is None
            assert r_rpi.content == raw_dmds
    finally:
        consume_once_orchestrator.active_packages.pop(item_id, None)


def test_ws_sram_profile_header_frame0_coalescing_interchunk_pacing_and_sibling_coexistence(
    tmp_path: Path,
) -> None:
    """
    Verify `WS /api/v1/games/ws/{item_id}.dmdz?v=2&credits=4&chunk=1360&profile=sram`:
    1. Coalesces the 16-byte `DMDZ` header + Frame `0` into the initial WebSocket message window
       (`len(rx[0]) > 16`, `rx[0][:4] == b'DMDZ'`).
    2. Generates `.dmdz2.sram.stream` (`<= 1,400 B/frame` with `FRAME_PAL8`) coexisting cleanly with
       lossless `.dmdz2.stream`.
    3. Applies `2 ms` inter-chunk pacing when splitting multi-chunk frames on `profile=sram`.
    """
    rng = np.random.default_rng(31337)
    item_id = "ws_sram_pal8_item"
    item_dir = tmp_path / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    stream_path = item_dir / f"{item_id}.rgb565.stream"

    # High-entropy noisy frames so `.dmdz2.stream` uses FRAME_RAW (~8 KB) while `.dmdz2.sram.stream` uses PAL8 (<= 1.4 KB)
    frames_rgb = [rng.integers(0, 256, size=(32, 128, 3), dtype=np.uint8) for _ in range(4)]
    write_rgb565_stream(frames_rgb, stream_path, fps=60)

    cand = MediaCandidate(
        id="ss_contra",
        domain="games",
        source_id="screenscraper",
        title="Contra III",
        year="1992",
        badge="SNES",
        badge_color="#AA00FF",
        subtitle="Action",
        rating="9.0",
        synopsis="Alien Wars.",
    )
    card = ShowcaseCard(
        item_id=item_id,
        domain="games",
        title="Contra III",
        year="1992",
        badge="SNES",
        badge_color="#AA00FF",
        subtitle="Action",
        rating="9.0",
        synopsis="Alien Wars.",
        lang="en",
        source_used="screenscraper",
        poster_width=32,
        poster_height=32,
        poster_rgb565_b64="",
        has_video=True,
        stream_frames=4,
        stream_fps=60,
        stream_duration_s=0.07,
    )
    consume_once_orchestrator.active_packages[item_id] = PreparedShowcasePackage(
        item_id=item_id,
        dmd_id="esp32_sram_dmd",
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
            # First request lossless `profile=psram` WS, then `profile=sram` WS to verify sibling coexistence
            psram_rx: List[bytes] = []
            with tc.websocket_connect(
                f"/api/v1/games/ws/{item_id}.dmdz?purge=false&client_id=esp32s3&v=2&credits=12&chunk=1400&profile=psram"
            ) as ws_psram:
                while True:
                    try:
                        msg = ws_psram.receive_bytes()
                    except WebSocketDisconnect:
                        break
                    psram_rx.append(msg)
                    ws_psram.send_bytes(b"A")

            sram_rx: List[bytes] = []
            with tc.websocket_connect(
                f"/api/v1/games/ws/{item_id}.dmdz?purge=false&client_id=esp32dev&v=2&credits=4&chunk=1360&profile=sram"
            ) as ws_sram:
                while True:
                    try:
                        msg = ws_sram.receive_bytes()
                    except WebSocketDisconnect:
                        break
                    assert 0 < len(msg) <= 1360
                    sram_rx.append(msg)
                    ws_sram.send_bytes(b"A")

            # Verify first WS message coalesced the 16-byte header + Frame 0
            assert len(sram_rx[0]) > 16
            assert sram_rx[0][:4] == DMDZ_MAGIC
            assert sram_rx[0][4] == DMDZ_V2_VERSION

            std_sib = dmdz_sibling_path(stream_path, version=2, profile="psram")
            sram_sib = dmdz_sibling_path(stream_path, version=2, profile="sram")
            assert std_sib.exists() and std_sib.name.endswith(".dmdz2.stream")
            assert sram_sib.exists() and sram_sib.name.endswith(".dmdz2.sram.stream")
            assert sram_sib.stat().st_size < std_sib.stat().st_size * 0.30

            sram_wire = b"".join(sram_rx)
            assert sram_wire == sram_sib.read_bytes()
            assert np.array_equal(decode_dmdz_bytes(sram_wire), decode_dmdz_bytes(sram_sib.read_bytes()))

            # Verify 2 ms inter-chunk pacing when a frame splits into multiple sub-packets (`chunk=300`)
            t0 = time.perf_counter()
            split_rx: List[bytes] = []
            with tc.websocket_connect(
                f"/api/v1/games/ws/{item_id}.dmdz?purge=false&client_id=esp32dev&v=2&credits=16&chunk=300&profile=sram"
            ) as ws_split:
                while True:
                    try:
                        msg = ws_split.receive_bytes()
                    except WebSocketDisconnect:
                        break
                    assert 0 < len(msg) <= 300
                    split_rx.append(msg)
                    ws_split.send_bytes(b"A")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert len(split_rx) >= 12
            # At least 8 intra-frame splits * 2 ms >= 14 ms wall time
            assert elapsed_ms >= 14.0
            assert b"".join(split_rx) == sram_wire
    finally:
        consume_once_orchestrator.active_packages.pop(item_id, None)
