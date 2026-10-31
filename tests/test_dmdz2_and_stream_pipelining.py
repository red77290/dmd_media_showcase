"""
Regression Tests for Lossless DMDZ v2 (`dmdz2`) Codec, Unchunked HTTP `Content-Length` Stream
& `DELETE` Purge Contract, and Non-Blocking Slot Preparation Pipelining.
"""
from __future__ import annotations

import asyncio
import struct
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.application.showcase_state import ShowcaseRuntimeState
from app.domain.entities import (
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.infrastructure.rendering.dmdz_codec import (
    DMDZ_VERSION,
    FRAME_RAW,
    FRAME_SCROLL_2D,
    HEADER_FMT,
    decode_dmdz_bytes,
    dmdz_sibling_path,
    encode_dmdz_bytes,
    encode_dmdz_frames,
    scrolled_reference_2d,
)
from app.main import app, consume_once_orchestrator
from dmd_gif_converter import write_rgb565_stream


def test_dmdz2_lossless_roundtrip_across_2d_scroll_lz77_luma_tdiff_and_raw_cap() -> None:
    """
    Verify `encode_dmdz_bytes` / `decode_dmdz_bytes`:
    1. Reconstructs 100% bit-exact RGB565 pixels across `FRAME_KEY`, `FRAME_SCROLL`, `FRAME_SCROLL_2D`
       (`dx > 0`, `dx < 0`, `dy > 0`, `dy < 0`), `OP_LZ` (`3..66` px, `1..2048` dist), `OP_LUMA`,
       `OP_TLUMA`, `OP_TDIFF`, and `FRAME_RAW`.
    2. Caps worst-case incompressible random frames at `1 + width * height * 2` bytes (`8,193 B` on `128x32`).
    """
    rng = np.random.default_rng(4242)
    h, w = 32, 128

    f0 = np.zeros((h, w), dtype="<u2")
    sprite = np.array([0xF800, 0xFD20, 0xFFE0, 0x07E0, 0x07FF, 0x001F, 0xF81F, 0xFFFF], dtype="<u2")
    for y in range(h):
        for x in range(0, w, 16):
            f0[y, x : x + 8] = sprite
            for k in range(8):
                r5 = min(31, 4 + k)
                g6 = min(63, 8 + k * 2)
                b5 = min(31, 6 + k)
                f0[y, x + 8 + k] = np.uint16((r5 << 11) | (g6 << 5) | b5)

    f1 = scrolled_reference_2d(f0, 5, 2)
    f1[:, -5:] = np.uint16(0x39E7)
    f1[-2:, :] = np.uint16(0x7BEF)

    f2 = scrolled_reference_2d(f1, -4, -3)
    f2[:3, :] = sprite[0]

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

    f4 = rng.integers(0, 65536, size=(h, w), dtype="<u2")

    seq = np.stack([f0, f1, f2, f3, f4]).astype("<u2")
    payloads = encode_dmdz_frames(seq)
    assert payloads[1][0] == FRAME_SCROLL_2D
    assert payloads[2][0] == FRAME_SCROLL_2D
    assert payloads[4][0] == FRAME_RAW
    assert len(payloads[4]) == 1 + h * w * 2

    raw_dmds = struct.pack(HEADER_FMT, b"DMDS", 1, 25, w, h, seq.shape[0], 40, 0) + seq.tobytes()
    encoded_v2 = encode_dmdz_bytes(raw_dmds)
    magic, ver, fps, dec_w, dec_h, total, _, _ = struct.unpack(HEADER_FMT, encoded_v2[:16])
    assert (magic, ver, fps, dec_w, dec_h, total) == (b"DMDZ", DMDZ_VERSION, 25, w, h, 5)

    decoded = decode_dmdz_bytes(encoded_v2)
    assert np.array_equal(decoded, seq)


def test_http_dmdz2_content_length_stream_and_delete_purge(tmp_path: Path) -> None:
    """
    Verify:
    1. `GET /api/v1/games/stream/{item_id}.rgb565.stream?purge=false&codec=dmdz2` serves lossless
       `DMDZ` v2 with exact `Content-Length` and `X-DMD-Codec: dmdz2` while keeping files on disk.
    2. `GET /api/v1/games/stream/{item_id}.rgb565.stream?purge=false` (no `codec`) serves raw `DMDS`.
    3. `DELETE /api/v1/games/stream/{item_id}.rgb565.stream` purges the package idempotently (`200 OK`).
    """
    item_id = "http_dmdz2_item"
    item_dir = tmp_path / item_id
    item_dir.mkdir(parents=True, exist_ok=True)
    stream_path = item_dir / f"{item_id}.rgb565.stream"

    rng = np.random.default_rng(99)
    frames_rgb = [rng.integers(0, 256, size=(32, 128, 3), dtype=np.uint8) for _ in range(3)]
    write_rgb565_stream(frames_rgb, stream_path, fps=25)
    raw_bytes = stream_path.read_bytes()
    expected_565 = np.frombuffer(raw_bytes[16:], dtype="<u2").reshape(3, 32, 128)

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
        dmd_id="esp32_sd_dmd",
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
            resp = tc.get(
                f"/api/v1/games/stream/{item_id}.rgb565.stream?purge=false&codec=dmdz2"
            )
            assert resp.status_code == 200
            assert resp.headers.get("X-DMD-Codec") == "dmdz2"
            assert int(resp.headers["Content-Length"]) == len(resp.content)
            assert np.array_equal(decode_dmdz_bytes(resp.content), expected_565)
            assert dmdz_sibling_path(stream_path).exists()
            assert item_id in consume_once_orchestrator.active_packages

            raw_resp = tc.get(
                f"/api/v1/games/stream/{item_id}.rgb565.stream?purge=false"
            )
            assert raw_resp.status_code == 200
            assert raw_resp.headers.get("X-DMD-Codec") is None
            assert raw_resp.content == raw_bytes

            del_resp = tc.delete(f"/api/v1/games/stream/{item_id}.rgb565.stream")
            assert del_resp.status_code == 200
            assert del_resp.json()["status"] == "purged"
            assert item_id not in consume_once_orchestrator.active_packages
            assert not item_dir.exists()

            # Second DELETE remains idempotent 200 OK
            del_again = tc.delete(f"/api/v1/games/stream/{item_id}.rgb565.stream")
            assert del_again.status_code == 200
    finally:
        consume_once_orchestrator.active_packages.pop(item_id, None)


@pytest.mark.asyncio
async def test_showcase_runtime_state_never_freezes_empty_n_plus_1_slots() -> None:
    """
    Verify `ShowcaseRuntimeState.yield_standby_if_busy`:
    - Empty `N+1` slots (`key not in ready_packages`) NEVER defer (`return False, None`).
    - `N+2` standby slots yield ONLY while a sibling `N+1` slot has a live prep task and no ready package.
    """
    state = ShowcaseRuntimeState()
    geom = PanelGeometry(width=128, height=32)
    k_movies = ("esp32_1", "movies")
    k_games = ("esp32_1", "games")
    state.client_configs[k_movies] = (geom, FilterCriteria(domain="movies"))
    state.client_configs[k_games] = (geom, FilterCriteria(domain="games"))

    # Empty N+1 slot never defers
    deferred, started = await state.yield_standby_if_busy(k_movies, None)
    assert deferred is False
    assert started is None

    # Standby N+2 slot (`k_movies` ready) yields while sibling `k_games` is actively preparing its N+1 ready slot
    state.ready_packages[k_movies] = object()  # type: ignore[assignment]
    live_task = asyncio.create_task(asyncio.sleep(5.0))
    state.prep_tasks[k_games] = live_task
    try:
        deferred_sb, _ = await state.yield_standby_if_busy(k_movies, None)
        assert deferred_sb is True

        # Once `k_games` also has a ready package, standby pre-warming proceeds immediately
        state.ready_packages[k_games] = object()  # type: ignore[assignment]
        deferred_after, _ = await state.yield_standby_if_busy(k_movies, None)
        assert deferred_after is False
    finally:
        live_task.cancel()
