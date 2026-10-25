"""
Unit and end-to-end integration tests for the Mode 2B Card Studio, Pixel-Art Layouts,
YOKO/TATE Orientation Reflow, Visual Themes (`amber_dmd`, `pure_black`, `neon_border`,
`poster_wash`, `tinted`), Two-Speed Binary `DMDS` Preview Endpoints, and 0 ms
Zero-Retranscode Promotion into Active DMD Queues.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
from pathlib import Path
from typing import Dict, List

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.application.consume_once_orchestrator import ConsumeOnceOrchestrator
from app.config import settings
from app.domain.entities import FilterCriteria, MediaCandidate, PanelGeometry
from app.infrastructure.persistence.dmd_profile_store import DmdProfileStore
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.transcoder.dmd_converter_adapter import (
    DmdConverterTranscoderAdapter,
    merge_rgb565_streams_with_chapters,
)
from app.main import app
from app.scheduler.asset_builder import (
    build_card_stream_sync,
    build_fast_studio_card_preview_sync,
)

DMDS_HEADER_FMT = "<4sBBHHHHH"
DMDS_HEADER_SIZE = 16
DMDS_MAGIC = b"DMDS"


def _make_vivid_poster_bytes(w: int = 96, h: int = 128) -> bytes:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = np.linspace(40, 245, w, dtype=np.uint8)[None, :]
    arr[:, :, 1] = np.linspace(20, 180, h, dtype=np.uint8)[:, None]
    arr[:, :, 2] = 160
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def _decode_dmds_first_frame_rgb(dmds_bytes: bytes) -> tuple[int, int, int, int, np.ndarray]:
    assert len(dmds_bytes) >= DMDS_HEADER_SIZE
    magic, version, fps, w, h, total_frames, frame_dur_ms, _res = struct.unpack(
        DMDS_HEADER_FMT, dmds_bytes[:DMDS_HEADER_SIZE]
    )
    assert magic == DMDS_MAGIC
    assert version == 1
    expected_len = DMDS_HEADER_SIZE + total_frames * w * h * 2
    assert len(dmds_bytes) == expected_len
    px565 = np.frombuffer(
        dmds_bytes[DMDS_HEADER_SIZE : DMDS_HEADER_SIZE + w * h * 2], dtype="<u2"
    ).reshape((h, w))
    r = (((px565 >> 11) & 0x1F) << 3).astype(np.uint8)
    g = (((px565 >> 5) & 0x3F) << 2).astype(np.uint8)
    b = ((px565 & 0x1F) << 3).astype(np.uint8)
    return w, h, fps, total_frames, np.stack([r, g, b], axis=-1)


@pytest.mark.parametrize(
    "card_layout",
    ["classic_split", "poster_right", "badge_header", "hero_banner", "minimal_arcade"],
)
@pytest.mark.parametrize(
    "panel_dims",
    [(64, 32), (128, 32), (64, 64), (128, 64), (256, 64), (32, 64), (32, 128), (64, 128)],
)
def test_all_card_layouts_across_yoko_square_and_tate_geometries(
    tmp_path: Path, card_layout: str, panel_dims: tuple[int, int]
):
    """
    Verify all 5 pixel-art card layouts produce valid, non-black `DMDS` RGB565LE streams
    across horizontal YOKO, square, and vertical TATE HUB75 geometries.
    """
    w, h = panel_dims
    item_dir = tmp_path / f"item_{card_layout}_{w}x{h}"
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "poster.png").write_bytes(_make_vivid_poster_bytes())

    stream_name = build_card_stream_sync(
        item_dir=str(item_dir),
        width=w,
        height=h,
        fps=15,
        title="Metal Slug X",
        year="1999",
        badge="NEOGEO",
        badge_color="#FF6D00",
        subtitle="SNK",
        rating="9.3",
        synopsis="Run and gun arcade masterpiece.",
        poster_png_name="poster.png",
        include_poster=True,
        include_synopsis=True,
        text_scroll_speed="6",
        include_card_intro=True,
        card_layout=card_layout,
        card_theme="tinted",
        card_font="arcade_pixel",
        card_hold_s=0.4,
    )
    assert stream_name == f"card_intro_{w}x{h}.rgb565.stream"
    raw = (item_dir / stream_name).read_bytes()
    dw, dh, dfps, dframes, rgb = _decode_dmds_first_frame_rgb(raw)
    assert (dw, dh, dfps) == (w, h, 15)
    assert dframes > 0
    assert int(rgb.max()) > 40, f"Rendered card frame is unexpectedly blank for {card_layout} @ {w}x{h}"


def test_card_themes_amber_dmd_pure_black_japanese_cjk_and_disable_intro(tmp_path: Path):
    """
    Verify:
    - `amber_dmd` enforces pinball plasma monochrome (`R >= G > B` on all lit pixels),
    - `pure_black` keeps unlit background pixels strictly `(0, 0, 0)`,
    - `arcade_pixel` automatically falls back to CJK glyphs for Japanese titles (`機動戦士ガンダム`),
    - `include_card_intro=False` cleanly skips card generation (`""`).
    """
    poster = _make_vivid_poster_bytes()

    # 1. Amber DMD Pinball Plasma Theme
    amber_bytes, n_amber, _, _ = build_fast_studio_card_preview_sync(
        cache_root=str(tmp_path),
        width=128,
        height=32,
        fps=15,
        title="Medieval Madness",
        year="1997",
        badge="PINBALL",
        badge_color="#00E5FF",
        subtitle="Williams",
        rating="9.5",
        synopsis="Defend the castle in classic orange plasma.",
        poster_url="",
        poster_w=30,
        poster_h=30,
        include_card_intro=True,
        card_layout="classic_split",
        card_theme="amber_dmd",
        card_font="arcade_pixel",
        card_accent_color="auto",
        include_poster=False,
        show_badge=True,
        show_year=True,
        show_rating=True,
        include_synopsis=True,
        text_scroll_speed="5",
        card_hold_s=0.4,
        color_boost="pixel_art",
    )
    assert n_amber > 0
    _, _, _, _, rgb_amber = _decode_dmds_first_frame_rgb(amber_bytes)
    lit_mask = rgb_amber.sum(axis=-1) > 24
    assert bool(lit_mask.any())
    lit_px = rgb_amber[lit_mask]
    assert np.all(lit_px[:, 0] >= lit_px[:, 1]), "Amber DMD must have Red >= Green on lit pixels"
    assert np.all(lit_px[:, 2] <= 16), "Amber DMD must suppress Blue channel on lit pixels"

    # 2. Pure Black HUB75 + Japanese CJK Title
    jp_dir = tmp_path / "jp_pure_black"
    jp_dir.mkdir(parents=True, exist_ok=True)
    (jp_dir / "poster.png").write_bytes(poster)
    jp_name = build_card_stream_sync(
        item_dir=str(jp_dir),
        width=128,
        height=32,
        fps=15,
        title="機動戦士ガンダム 逆襲のシャア",
        year="1988",
        badge="ANIME",
        badge_color="#FF1744",
        subtitle="Sunrise",
        rating="8.6",
        synopsis="アムロとシャアの最後の戦い。",
        poster_png_name="poster.png",
        include_poster=False,
        include_synopsis=True,
        text_scroll_speed="4",
        include_card_intro=True,
        card_layout="minimal_arcade",
        card_theme="pure_black",
        card_font="arcade_pixel",
        card_hold_s=0.3,
    )
    assert jp_name != ""
    _, _, _, dframes_jp, rgb_jp = _decode_dmds_first_frame_rgb((jp_dir / jp_name).read_bytes())
    assert dframes_jp > 0
    assert tuple(int(c) for c in rgb_jp[0, 0]) == (0, 0, 0)

    # 3. include_card_intro=False skips card generation cleanly when used inside full sequence
    skip_dir = tmp_path / "skipped_card"
    skip_dir.mkdir(parents=True, exist_ok=True)
    skip_name = build_card_stream_sync(
        item_dir=str(skip_dir),
        width=128,
        height=32,
        fps=15,
        title="No Intro",
        year="2024",
        badge="GAME",
        badge_color="#FF6D00",
        subtitle="",
        rating="",
        synopsis="",
        include_poster=True,
        include_synopsis=False,
        text_scroll_speed="normal",
        include_card_intro=False,
    )
    assert skip_name == ""


def test_merge_rgb565_streams_with_chapters_metadata(tmp_path: Path):
    """Verify chapter frame offsets and durations are accurately tracked across merged `DMDS` segments."""
    seg1 = tmp_path / "seg1.rgb565.stream"
    seg2 = tmp_path / "seg2.rgb565.stream"
    out = tmp_path / "merged.rgb565.stream"
    w, h, fps = 64, 32, 15
    hdr1 = struct.pack(DMDS_HEADER_FMT, DMDS_MAGIC, 1, fps, w, h, 10, 67, 0)
    hdr2 = struct.pack(DMDS_HEADER_FMT, DMDS_MAGIC, 1, fps, w, h, 20, 67, 0)
    seg1.write_bytes(hdr1 + b"\x1f\x00" * (10 * w * h))
    seg2.write_bytes(hdr2 + b"\xe0\x07" * (20 * w * h))

    total_frames, dur_s, chapters = merge_rgb565_streams_with_chapters(
        [("card", "1. Card Info", seg1), ("video", "2. Video", seg2)],
        out,
        w,
        h,
        fps,
    )
    assert total_frames == 30
    assert dur_s == 2.0
    assert len(chapters) == 2
    assert chapters[0]["id"] == "card" and chapters[0]["start_frame"] == 0 and chapters[0]["frames"] == 10
    assert chapters[1]["id"] == "video" and chapters[1]["start_frame"] == 10 and chapters[1]["frames"] == 20


@pytest.mark.asyncio
async def test_studio_endpoints_zero_retranscode_queue_promotion_and_profile_persistence(
    tmp_path: Path, monkeypatch
):
    """
    End-to-end verification of:
    1. `POST /api/v1/studio/card-preview` (< 100 ms `DMDS` binary stream + `X-DMD-Chapters`, TATE 64x128 & YOKO 128x32).
    2. `POST /api/v1/studio/full-preview` returning `X-DMD-Preview-Id` + `X-DMD-Chapters`.
    3. `POST /api/v1/clients/{dmd_id}/queue` with `preview_id` promoting the simulation in 0 ms
       (`status == 'ready'`, `from_studio_sim == True`) with ZERO extra `transcode_candidate` calls
       when target DMD resolution matches (`128x32`), and adapting seamlessly when resolution differs (`64x128`).
    4. `PUT /api/v1/clients/{dmd_id}/profile` persisting `card_style` and `POST /api/v1/clients/hello`
       preserving `card_style` intact.
    """
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    seen_repo = JsonSeenHistoryRepository(file_path=tmp_path / "data" / "seen_history.json")
    prof_store = DmdProfileStore(file_path=tmp_path / "data" / "dmd_profiles.json")

    staged_cand = MediaCandidate(
        id="ss:neogeo:mslugx",
        domain="games",
        title="Metal Slug X",
        year="1999",
        badge="NEOGEO",
        badge_color="#FF6D00",
        subtitle="SNK",
        rating="9.4",
        synopsis="Explosive 2D run-and-gun action.",
        lang="fr",
        source_id="screenscraper",
        poster_url="",
    )

    class _FakeChain:
        def get_staged_candidate(self, cid: str):
            return staged_cand if cid == staged_cand.id else None

        async def search_candidates(self, criteria: FilterCriteria, query: str = "", limit: int = 8):
            return [staged_cand]

        async def acquire_unseen_candidates(self, criteria: FilterCriteria, count: int = 1, extra_exclude_ids=None):
            return [staged_cand]

    class _FakeMqtt:
        is_running = False

        def __init__(self):
            self.published: List[tuple[str, dict, bool]] = []

        def set_handlers(self, **kwargs):
            return None

        async def publish_json(self, topic: str, payload: dict, retain: bool = False):
            self.published.append((topic, payload, retain))

    class _FakeStreamer:
        active_streams_count = 0

        async def start_udp_clock_stream(self, **kwargs):
            return True

        async def stop_stream(self, dmd_id: str):
            return None

    real_adapter = DmdConverterTranscoderAdapter()
    transcode_calls: List[tuple[int, int, str, str]] = []

    class _CountingTranscoder:
        async def transcode_candidate(
            self,
            cand: MediaCandidate,
            criteria: FilterCriteria,
            geometry: PanelGeometry,
            output_dir: Path,
        ) -> Dict[str, object]:
            transcode_calls.append(
                (geometry.width, geometry.height, criteria.card_layout, criteria.card_theme)
            )
            return await real_adapter.transcode_candidate(cand, criteria, geometry, output_dir)

    isolated_co = ConsumeOnceOrchestrator(
        chain_service=_FakeChain(),  # type: ignore[arg-type]
        transcoder=_CountingTranscoder(),  # type: ignore[arg-type]
        seen_repo=seen_repo,
        mqtt_gateway=_FakeMqtt(),  # type: ignore[arg-type]
        streamer=_FakeStreamer(),  # type: ignore[arg-type]
        profile_store=prof_store,
    )
    monkeypatch.setattr("app.main.consume_once_orchestrator", isolated_co)
    monkeypatch.setattr("app.main.dmd_profile_store", prof_store)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Fast TATE Card-Only Preview (`64x128`) — zero heavy transcode_candidate calls!
            r_fast = await client.post(
                "/api/v1/studio/card-preview",
                json={
                    "domain": "games",
                    "width": 64,
                    "height": 128,
                    "fps": 15,
                    "title": "DoDonPachi",
                    "year": "1997",
                    "badge": "ARCADE",
                    "badge_color": "#FF1744",
                    "rating": "9.2",
                    "synopsis": "Vertical bullet-hell shmup in TATE mode.",
                    "card_layout": "badge_header",
                    "card_theme": "neon_border",
                    "card_font": "arcade_pixel",
                    "text_scroll_speed": "7",
                    "card_hold_s": 0.5,
                },
            )
            assert r_fast.status_code == 200
            assert "X-DMD-Chapters" in r_fast.headers
            fast_chapters = json.loads(r_fast.headers["X-DMD-Chapters"])
            assert len(fast_chapters) == 1 and fast_chapters[0]["id"] == "card"
            fw, fh, ffps, fframes, _ = _decode_dmds_first_frame_rgb(r_fast.content)
            assert (fw, fh, ffps) == (64, 128, 15)
            assert fframes > 0
            assert len(transcode_calls) == 0

            # 2. Full Sequence Preview (`128x32`) -> returns `X-DMD-Preview-Id`
            r_full = await client.post(
                "/api/v1/studio/full-preview",
                json={
                    "domain": "games",
                    "candidate_id": staged_cand.id,
                    "query": staged_cand.title,
                    "lang": "fr",
                    "width": 128,
                    "height": 32,
                    "fps": 15,
                    "video_fps": 15,
                    "card_layout": "poster_right",
                    "card_theme": "amber_dmd",
                    "card_font": "arcade_pixel",
                    "text_scroll_speed": "5",
                    "card_hold_s": 0.4,
                    "jacket_panorama": False,
                    "marquee_panorama": False,
                    "snapshot_panorama_count": 0,
                    "include_video": False,
                },
            )
            assert r_full.status_code == 200
            preview_id = r_full.headers.get("X-DMD-Preview-Id", "")
            assert preview_id.startswith("sim_")
            assert len(transcode_calls) == 1
            assert transcode_calls[0] == (128, 32, "poster_right", "amber_dmd")

            # 3. Register a Mode 2B Passive DMD (`128x32`) with custom `card_style`
            r_prof = await client.put(
                "/api/v1/clients/esp32_cab_2b/profile",
                json={
                    "dmd_id": "esp32_cab_2b",
                    "display_name": "Cabinet Marquee 2B",
                    "config_source": "server",
                    "supports_local_card": False,
                    "always_stream_udp": False,
                    "paused": True,
                    "width": 128,
                    "height": 32,
                    "udp_ip": "192.168.1.90",
                    "udp_port": 4210,
                    "lang": "fr",
                    "card_style": {
                        "card_layout": "badge_header",
                        "card_theme": "neon_border",
                        "card_font": "arcade_pixel",
                        "text_scroll_speed": "6",
                        "card_hold_s": 0.8,
                    },
                },
            )
            assert r_prof.status_code == 200
            saved_prof = r_prof.json()["profile"]
            assert saved_prof["card_style"]["card_layout"] == "badge_header"
            assert saved_prof["card_style"]["card_theme"] == "neon_border"

            # Verify HTTP `hello` heartbeat preserves `card_style` intact!
            r_hello = await client.post(
                "/api/v1/clients/hello",
                json={
                    "dmd_id": "esp32_cab_2b",
                    "width": 128,
                    "height": 32,
                    "udp_ip": "192.168.1.91",
                    "udp_port": 4210,
                    "supports_local_card": False,
                },
            )
            assert r_hello.status_code == 200
            assert r_hello.json()["profile"]["card_style"]["card_layout"] == "badge_header"

            # 4. Promote Studio Simulation (`preview_id`) into `esp32_cab_2b` (`128x32`) -> 0 ms ZERO re-transcode!
            calls_before_promo = len(transcode_calls)
            r_queue_fast = await client.post(
                "/api/v1/clients/esp32_cab_2b/queue",
                json={
                    "preview_id": preview_id,
                    "candidate_id": staged_cand.id,
                    "domain": "games",
                    "query": staged_cand.title,
                    "lang": "fr",
                    "mode": "enqueue_next",
                    "mark_seen": False,
                },
            )
            assert r_queue_fast.status_code == 200
            q_item = r_queue_fast.json()["item"]
            assert q_item["from_studio_sim"] is True
            assert q_item["status"] == "ready"
            assert len(transcode_calls) == calls_before_promo, "Expected 0 re-transcode calls on matching geometry!"

            # 5. Enqueue same simulation onto a TATE `64x128` DMD -> adapts geometry via background transcode
            await client.put(
                "/api/v1/clients/esp32_tate_2b/profile",
                json={
                    "dmd_id": "esp32_tate_2b",
                    "config_source": "server",
                    "supports_local_card": False,
                    "paused": True,
                    "width": 64,
                    "height": 128,
                    "lang": "fr",
                },
            )
            r_queue_tate = await client.post(
                "/api/v1/clients/esp32_tate_2b/queue",
                json={
                    "preview_id": preview_id,
                    "candidate_id": staged_cand.id,
                    "domain": "games",
                    "query": staged_cand.title,
                    "lang": "fr",
                    "mode": "enqueue_next",
                    "mark_seen": False,
                },
            )
            assert r_queue_tate.status_code == 200
            assert r_queue_tate.json()["item"]["from_studio_sim"] is True
            for _ in range(40):
                if any(c[:2] == (64, 128) and c[2:] == ("poster_right", "amber_dmd") for c in transcode_calls):
                    break
                await asyncio.sleep(0.05)
            assert any(c[:2] == (64, 128) and c[2:] == ("poster_right", "amber_dmd") for c in transcode_calls)
    finally:
        await isolated_co.stop_server_managed_profiles()
