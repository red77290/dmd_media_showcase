"""
End-to-end unit and integration tests for the DMD Media Showcase Server (V2.1).

Validates:
- Interactive Swagger UI (`/docs`, `/swagger`, `/redoc`, `/openapi.json`, `/` redirect)
- Exhaustive live categories across all 4 domains (`games`, `movies`, `series`, `anime`)
- Adaptive multi-resolution (`128x32`, `128x64`, `256x64`, `64x64`) & TATE (`64x128`, `32x128`) poster dimensions
- Granular top-to-bottom scrolling panoramas (`jacket_panorama`, `marquee_panorama`, `snapshot_panorama_count`)
- Strict real-video-only policy (`has_video=False` when no video is available, zero synthetic grid+ball)
- Immediate `CurrentBatch -> NextBatch` rotation on subsequent polls + `Zero-404` session auto-recovery
- Dynamic O(1) stream duration truncation (`?max_duration_s=`)
- Future-ready `POST /api/v1/{domain}/on-demand` priority queue injection
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path
from typing import List, Optional, Set

import cv2
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from dmd_gif_converter import read_rgb565_stream_header

from app.config import settings
from app.main import app
from app.models import DomainType, SessionRegisterRequest
from app.providers.base import RawMediaCandidate
from app.scheduler.worker_pool import batch_manager


def _make_sample_png_bytes(width: int = 160, height: int = 220, color=(220, 60, 40)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_sample_mp4_bytes(width: int = 160, height: int = 120, fps: int = 10, frames: int = 30) -> bytes:
    tmp = Path("/tmp/_test_sample_clip.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp), fourcc, float(fps), (width, height))
    for i in range(frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, 0] = (i * 8) % 255
        frame[:, :, 1] = 120
        frame[:, :, 2] = 220
        writer.write(frame)
    writer.release()
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return data


@pytest.fixture(autouse=True)
async def _isolated_test_environment(tmp_path: Path, monkeypatch):
    """Isolate cache directory, use ThreadPoolExecutor, and mock live scraper HTTP in offline test sandbox."""
    orig_cache = settings.cache_dir
    orig_pool = settings.use_process_pool
    orig_master_max = settings.server_master_clip_max_s

    settings.cache_dir = tmp_path / "showcase_cache"
    settings.use_process_pool = False
    settings.server_master_clip_max_s = 3.5

    await batch_manager.stop()
    batch_manager.sessions.clear()
    batch_manager.asset_index.clear()
    batch_manager.start()

    call_seq = {"n": 0}

    def _make_mock_provider_methods(domain: DomainType):
        async def _mock_fetch_random(
            req: SessionRegisterRequest,
            exclude_ids: Optional[Set[str]] = None,
        ) -> List[RawMediaCandidate]:
            exclude = set(exclude_ids or set())
            out: List[RawMediaCandidate] = []
            while len(out) < req.count:
                call_seq["n"] += 1
                idx = call_seq["n"]
                cid = f"{domain}_live_{idx}"
                if cid in exclude:
                    continue
                cand = RawMediaCandidate(
                    id=cid,
                    domain=domain,
                    title=f"Live {domain.title()} Title #{idx}",
                    year="1996",
                    badge=domain.upper()[:6],
                    badge_color="#E53935",
                    subtitle=f"{domain.title()} • 1996",
                    rating="8.9",
                    synopsis=f"Complete localized ({req.lang}) synopsis for {domain} item #{idx} scrolling organically across the DMD.",
                    lang=req.lang,
                    poster_url=f"https://example.org/{cid}_poster.png",
                    jacket_url=f"https://example.org/{cid}_jacket.png",
                    marquee_url=f"https://example.org/{cid}_marquee.png",
                    backdrop_url=f"https://example.org/{cid}_backdrop.png",
                    snapshot_urls=[
                        f"https://example.org/{cid}_snap1.png",
                        f"https://example.org/{cid}_snap2.png",
                        f"https://example.org/{cid}_snap3.png",
                    ],
                    image_urls=[
                        f"https://example.org/{cid}_snap1.png",
                        f"https://example.org/{cid}_snap2.png",
                    ],
                    video_url=f"https://example.org/{cid}_shortplay.mp4" if (idx % 2 == 1) else None,
                    source_used="live_api",
                )
                out.append(cand)
            return out

        async def _mock_search_on_demand(query: str, lang: str = "fr") -> Optional[RawMediaCandidate]:
            return RawMediaCandidate(
                id=f"{domain}_ondemand_1",
                domain=domain,
                title=query,
                year="1982",
                badge=domain.upper()[:6],
                badge_color="#00ACC1",
                subtitle=f"{domain.title()} • 1982",
                rating="9.1",
                synopsis=f"On-demand synopsis for {query} ({lang}).",
                lang=lang,
                poster_url="https://example.org/od_poster.png",
                jacket_url="https://example.org/od_jacket.png",
                marquee_url="https://example.org/od_marquee.png",
                snapshot_urls=["https://example.org/od_snap1.png"],
                source_used="live_ondemand",
            )

        return _mock_fetch_random, _mock_search_on_demand

    for dom, prov in batch_manager.providers.items():
        fn_rand, fn_od = _make_mock_provider_methods(dom)
        monkeypatch.setattr(prov, "fetch_random_candidates", fn_rand)
        monkeypatch.setattr(prov, "search_on_demand", fn_od)

    yield

    await batch_manager.stop()
    settings.cache_dir = orig_cache
    settings.use_process_pool = orig_pool
    settings.server_master_clip_max_s = orig_master_max


@pytest.mark.asyncio
async def test_swagger_and_exhaustive_domain_filters():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Verify Swagger UI, ReDoc, and OpenAPI schema endpoints
        for doc_path in ("/docs", "/swagger", "/redoc"):
            r_doc = await client.get(doc_path)
            assert r_doc.status_code == 200
            assert "text/html" in r_doc.headers.get("content-type", "")

        r_openapi = await client.get("/openapi.json")
        assert r_openapi.status_code == 200
        schema = r_openapi.json()
        assert schema["info"]["title"] == "DMD Media Showcase Server API"
        assert "/api/v1/{domain}/session" in schema["paths"]
        assert "/api/v1/{domain}/batch" in schema["paths"]
        assert "/api/v1/asyncapi.yaml" in schema["paths"]

        # Verify AsyncAPI 2.6 MQTT + UDP DMDF specification endpoints and static doc sync
        for async_path in ("/asyncapi.yaml", "/api/v1/asyncapi.yaml"):
            r_async = await client.get(async_path)
            assert r_async.status_code == 200
            assert "yaml" in r_async.headers.get("content-type", "")
            assert "asyncapi: 2.6.0" in r_async.text
            assert "udp/dmdf_frame_chunk:" in r_async.text
            assert 'x-binary-header-format: "<4sHBBH"' in r_async.text

        docs_dir = Path(__file__).resolve().parent.parent / "docs"
        assert (docs_dir / "asyncapi.yaml").read_text(encoding="utf-8") == r_async.text

        # 2. Verify Health endpoint
        for path in ("/api/v1/health", "/health"):
            r = await client.get(path)
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"
            assert data["max_workers"] >= 1
            assert set(data["providers_configured"].keys()) == {"games", "movies", "series", "anime"}

        # 3. Verify exhaustive categories on all 4 domains
        expected_min_genres = {"games": 16, "movies": 20, "series": 20, "anime": 28}
        for domain, min_g in expected_min_genres.items():
            rf = await client.get(f"/api/v1/{domain}/filters")
            assert rf.status_code == 200
            fdata = rf.json()
            assert fdata["domain"] == domain
            assert "sources" in fdata
            assert len(fdata["genres"]) >= min_g
            if domain == "games":
                assert len(fdata["systems"]) >= 25


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["games", "movies", "series", "anime"])
@pytest.mark.parametrize(
    "width,height,expected_pw,expected_ph",
    [
        (128, 32, 32, 32),
        (128, 64, 48, 64),
        (256, 64, 48, 64),
        (64, 64, 32, 32),
        (64, 128, 40, 40),  # TATE vertical
        (32, 128, 24, 24),  # Slim TATE vertical
    ],
)
async def test_multi_format_tate_and_granular_panoramas(
    domain: str,
    width: int,
    height: int,
    expected_pw: int,
    expected_ph: int,
    monkeypatch,
):
    sample_png = _make_sample_png_bytes()
    monkeypatch.setattr(
        "app.scheduler.asset_builder._download_bytes",
        lambda url, timeout=5.0: sample_png if url else None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg_resp = await client.post(
            f"/api/v1/{domain}/session",
            json={
                "client_id": f"dmd-{domain}-{width}x{height}",
                "count": 1,
                "lang": "fr",
                "width": width,
                "height": height,
                "jacket_panorama": True,
                "marquee_panorama": True,
                "snapshot_panorama_count": 2,
                "primary_source": "auto",
                "include_video": False,
                "include_synopsis": True,
            },
        )
        assert reg_resp.status_code == 200
        session_id = reg_resp.json()["session_id"]

        batch_resp = await client.get(
            f"/api/v1/{domain}/batch",
            params={"session_id": session_id},
        )
        assert batch_resp.status_code == 200
        batch = batch_resp.json()
        assert batch["status"] == "ready"
        first = batch["items"][0]

        assert first["poster_width"] == expected_pw
        assert first["poster_height"] == expected_ph
        assert first["jacket_panorama"] is True
        assert first["marquee_panorama"] is True
        assert first["snapshot_panorama_count"] == 2
        assert first["assets"]["jacket_stream_rgb565"] != ""
        assert first["assets"]["marquee_stream_rgb565"] != ""
        assert len(first["assets"]["snapshot_streams_rgb565"]) == 2
        # Total ordered panorama streams = 1 jacket + 1 marquee + 2 snapshots = 4
        assert len(first["assets"]["panorama_streams_rgb565"]) == 4

        # Verify binary RGB565 mini-jacket has exact expected byte size (`expected_pw * expected_ph * 2`)
        poster_r = await client.get(first["assets"]["poster_rgb565"])
        assert poster_r.status_code == 200
        assert len(poster_r.content) == expected_pw * expected_ph * 2

        # Verify jacket panorama stream header matches `(width, height)`
        j_stream_r = await client.get(first["assets"]["jacket_stream_rgb565"])
        assert j_stream_r.status_code == 200
        j_hdr = read_rgb565_stream_header(j_stream_r.content[:16])
        assert j_hdr["width"] == width
        assert j_hdr["height"] == height
        assert j_hdr["total_frames"] >= 10


@pytest.mark.asyncio
async def test_video_batch_and_skip_when_no_video_available(monkeypatch):
    sample_png = _make_sample_png_bytes()
    sample_mp4 = _make_sample_mp4_bytes()

    def _fake_download(url: str | None, timeout: float = 5.0):
        if not url:
            return None
        if url.endswith(".mp4") or "shortplay" in url or "video" in url:
            return sample_mp4
        return sample_png

    monkeypatch.setattr("app.scheduler.asset_builder._download_bytes", _fake_download)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg_resp = await client.post(
            "/api/v1/games/session",
            json={
                "client_id": "esp32s3-arcade-room",
                "count": 2,
                "lang": "fr",
                "width": 128,
                "height": 32,
                "jacket_panorama": True,
                "marquee_panorama": False,
                "snapshot_panorama_count": 1,
                "primary_source": "arcade_italia",
                "include_video": True,
                "video_max_duration_s": 3.0,
                "video_fps": 10,
                "systems": ["neogeo", "mame"],
            },
        )
        assert reg_resp.status_code == 200
        session_id = reg_resp.json()["session_id"]

        state = batch_manager.sessions[session_id]
        keys = list(state.current_batch.candidates.keys())
        state.current_batch.candidates[keys[0]].video_url = "https://example.org/shortplay.mp4"
        state.current_batch.candidates[keys[1]].video_url = None
        state.current_batch.candidates[keys[1]].youtube_url = None

        batch = None
        for _ in range(80):
            r = await client.get("/api/v1/games/batch", params={"session_id": session_id})
            assert r.status_code == 200
            data = r.json()
            if data["status"] == "ready":
                batch = data
                break
            await asyncio.sleep(0.1)

        assert batch is not None, "Batch did not reach 'ready' state within timeout"
        assert batch["ready_count"] == 2
        item_with_vid = batch["items"][0]
        item_no_vid = batch["items"][1]

        assert item_with_vid["video_ready"] is True
        assert item_with_vid["has_video"] is True
        assert item_with_vid["assets"]["video_stream_rgb565"] != ""

        # Item without video MUST have has_video=False and empty video URLs (so DMD skips it!)
        assert item_no_vid["video_ready"] is True
        assert item_no_vid["has_video"] is False
        assert item_no_vid["assets"]["video_stream_rgb565"] == ""

        stream_url_base = item_with_vid["assets"]["video_stream_rgb565"].split("?")[0]
        r_full = await client.get(stream_url_base, params={"max_duration_s": 3.0})
        assert r_full.status_code == 200
        hdr_full = read_rgb565_stream_header(r_full.content[:16])
        assert hdr_full["width"] == 128
        assert hdr_full["height"] == 32
        assert hdr_full["total_frames"] >= 10

        r_short = await client.get(stream_url_base, params={"max_duration_s": 1.0})
        assert r_short.status_code == 200
        hdr_short = read_rgb565_stream_header(r_short.content[:16])
        assert hdr_short["total_frames"] <= 10
        assert len(r_short.content) < len(r_full.content)


@pytest.mark.asyncio
async def test_batch_rotation_and_zero_404_session_autorecovery(monkeypatch):
    monkeypatch.setattr("app.scheduler.asset_builder._download_bytes", lambda url, timeout=5.0: None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_rec = await client.get(
            "/api/v1/games/batch",
            params={"session_id": "ses_games_b32a6b80e723"},
        )
        assert r_rec.status_code == 200
        assert r_rec.json()["session_id"] == "ses_games_b32a6b80e723"

        reg = await client.post(
            "/api/v1/games/session",
            json={
                "client_id": "rpi_rotation_test",
                "count": 3,
                "lang": "fr",
                "include_video": False,
                "jacket_panorama": False,
                "snapshot_panorama_count": 0,
            },
        )
        ses_id = reg.json()["session_id"]

        b1 = (await client.get("/api/v1/games/batch", params={"session_id": ses_id})).json()
        titles_1 = [it["title"] for it in b1["items"]]

        state = batch_manager.sessions[ses_id]
        state.current_batch.last_served_at = time.monotonic() - 5.0

        b2 = (await client.get("/api/v1/games/batch", params={"session_id": ses_id})).json()
        titles_2 = [it["title"] for it in b2["items"]]
        assert titles_1 != titles_2, "Second rotation turn must serve NextBatch with new items!"


@pytest.mark.asyncio
async def test_on_demand_priority_injection_and_ttl_cleanup(monkeypatch):
    monkeypatch.setattr("app.scheduler.asset_builder._download_bytes", lambda url, timeout=5.0: None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg = await client.post(
            "/api/v1/movies/session",
            json={
                "client_id": "living-room-dmd",
                "count": 2,
                "lang": "fr",
                "include_video": False,
                "jacket_panorama": False,
                "snapshot_panorama_count": 0,
            },
        )
        session_id = reg.json()["session_id"]

        od_resp = await client.post(
            "/api/v1/movies/on-demand",
            json={
                "query": "Blade Runner",
                "domain": "movies",
                "lang": "fr",
                "width": 128,
                "height": 32,
                "jacket_panorama": False,
                "snapshot_panorama_count": 0,
                "video_max_duration_s": 2.0,
                "video_fps": 10,
                "target_session_id": session_id,
            },
        )
        assert od_resp.status_code == 200
        od_item = od_resp.json()
        assert od_item["on_demand"] is True
        assert "Blade Runner" in od_item["title"]

        for _ in range(40):
            if batch_manager.active_workers == 0 and batch_manager.queue_depth == 0:
                break
            await asyncio.sleep(0.05)

        batch_r = await client.get("/api/v1/movies/batch", params={"session_id": session_id})
        items = batch_r.json()["items"]
        assert items[0]["on_demand"] is True
        assert "Blade Runner" in items[0]["title"]

        batch_manager.sessions[session_id].last_accessed = time.monotonic() - (
            settings.session_ttl_seconds + 10
        )
        purged = batch_manager.cleanup_expired_sessions()
        assert purged >= 1
        assert session_id not in batch_manager.sessions


def test_mdns_responder_packet_encoding_and_reply():
    import socket
    import struct
    from app.infrastructure.mdns_responder import (
        DNS_CLASS_IN,
        DNS_TYPE_A,
        DNS_TYPE_PTR,
        decode_dns_name,
        encode_dns_name,
        parse_and_reply_mdns,
    )

    wire = encode_dns_name("dmdmediashowcase.local.")
    decoded, next_off = decode_dns_name(wire, 0)
    assert decoded == "dmdmediashowcase.local."
    assert next_off == len(wire)

    # Build a standard mDNS A query for dmdmediashowcase.local.
    query_hdr = struct.pack("!HHHHHH", 0x1234, 0x0000, 1, 0, 0, 0)
    query_pkt = query_hdr + wire + struct.pack("!HH", DNS_TYPE_A, DNS_CLASS_IN)
    reply = parse_and_reply_mdns(query_pkt, hostname="dmdmediashowcase", ipv4="192.168.1.77", port=8890)
    assert reply is not None
    assert reply.endswith(socket.inet_aton("192.168.1.77"))

    # Build a PTR query for _dmd-showcase._tcp.local.
    ptr_pkt = (
        query_hdr
        + encode_dns_name("_dmd-showcase._tcp.local.")
        + struct.pack("!HH", DNS_TYPE_PTR, DNS_CLASS_IN)
    )
    ptr_reply = parse_and_reply_mdns(ptr_pkt, hostname="dmdmediashowcase", ipv4="192.168.1.77", port=8890)
    assert ptr_reply is not None
    assert socket.inet_aton("192.168.1.77") in ptr_reply


def test_tate_marquee_and_screenshot_panoramas_scroll_left_to_right_without_distortion(
    tmp_path: Path, monkeypatch
):
    """
    Verify Showcase panoramas on TATE (`64x128` / `32x128`) and square (`64x64`) matrices:
    - Pan smoothly from Left -> Right (`Red -> Green -> Blue`) for wide marquees and screenshots
      with strict zero aspect-ratio distortion (both via native `scroll_direction` AND via the
      rotation fallback when running against an older `dmd_gif_converter` wheel).
    - Preserve Top -> Bottom scrolling on portrait jackets (`src_w < src_h`) and `128x32` matrices.
    """
    from dmd_gif_converter import DmdMediaProcessor, rgb565_le_to_rgb888
    from app.infrastructure.rendering.panorama_and_static import (
        convert_image_to_panorama_gif_and_stream,
        resolve_showcase_panorama_axis,
    )

    # TATE (H > W) ALWAYS scrolls left-to-right; YOKO (W > H) ALWAYS scrolls top-to-bottom;
    # Square (W == H) scrolls left-to-right when src_w > src_h else top-to-bottom.
    assert resolve_showcase_panorama_axis(800, 220, 64, 128) == "horizontal"
    assert resolve_showcase_panorama_axis(1920, 1080, 64, 128) == "horizontal"
    assert resolve_showcase_panorama_axis(320, 224, 32, 128) == "horizontal"
    assert resolve_showcase_panorama_axis(400, 600, 64, 128) == "horizontal"
    assert resolve_showcase_panorama_axis(800, 220, 128, 64) == "vertical"
    assert resolve_showcase_panorama_axis(800, 220, 128, 32) == "vertical"
    assert resolve_showcase_panorama_axis(1920, 1080, 128, 32) == "vertical"
    assert resolve_showcase_panorama_axis(800, 220, 64, 64) == "horizontal"
    assert resolve_showcase_panorama_axis(400, 600, 64, 64) == "vertical"

    # Build a synthetic 360x120 (3:1) RGBA marquee with transparent margin + Left=Red, Mid=Green, Right=Blue
    marquee_rgba = np.zeros((120, 360, 4), dtype=np.uint8)
    marquee_rgba[:, :, 3] = 255
    marquee_rgba[:10, :, :] = (255, 255, 255, 0)  # Transparent white alpha band -> must composite to black
    marquee_rgba[10:, :120, :3] = (240, 0, 0)
    marquee_rgba[10:, 120:240, :3] = (0, 240, 0)
    marquee_rgba[10:, 240:, :3] = (0, 0, 240)
    marquee_rgba[40:80, 160:200, :3] = (255, 255, 255)  # 40x40 centered 1:1 square
    buf = io.BytesIO()
    Image.fromarray(marquee_rgba, mode="RGBA").save(buf, format="PNG")
    marquee_bytes = buf.getvalue()

    def _assert_dmds_ltr(stream_path: Path, exp_w: int, exp_h: int) -> None:
        raw = stream_path.read_bytes()
        hdr = read_rgb565_stream_header(raw[:16])
        assert hdr["width"] == exp_w
        assert hdr["height"] == exp_h
        assert hdr["total_frames"] >= 20
        fbytes = exp_w * exp_h * 2
        first_rgb = rgb565_le_to_rgb888(raw[16 : 16 + fbytes], exp_w, exp_h)
        last_off = 16 + (hdr["total_frames"] - 1) * fbytes
        last_rgb = rgb565_le_to_rgb888(raw[last_off : last_off + fbytes], exp_w, exp_h)
        # Transparent top band must be pure dark/black, NOT white
        assert first_rgb[:6, :, :].mean() < 25.0
        # Starts on Left (Red) and finishes on Right (Blue)
        assert first_rgb[20:, :, 0].mean() > first_rgb[20:, :, 2].mean() + 80.0
        assert last_rgb[20:, :, 2].mean() > last_rgb[20:, :, 0].mean() + 80.0

    # 1. Native `dmd_gif_converter` horizontal scroll path on TATE 64x128
    out_native = tmp_path / "native_tate"
    res_native = convert_image_to_panorama_gif_and_stream(
        raw_image_bytes=marquee_bytes,
        out_dir=out_native,
        stem="pan_marquee",
        width=64,
        height=128,
        fps=16,
        duration_s=2.5,
        color_boost=False,
    )
    assert res_native is not None
    _assert_dmds_ltr(out_native / res_native[1], 64, 128)

    # 2. Legacy `dmd_gif_converter` wheel fallback path (simulating absence of `scroll_direction`)
    orig_method = DmdMediaProcessor.process_image_panorama

    def _legacy_sig_wrapper(
        self,
        input_image,
        output_dir,
        width: int = 128,
        height: int = 32,
        duration_s: float = 4.0,
        fps: int = 20,
        smart_color_boost: bool = True,
        export_gif: bool = True,
        export_rgb565_stream: bool = True,
        stem: str = "pan",
    ):
        return orig_method(
            self,
            input_image=input_image,
            output_dir=output_dir,
            width=width,
            height=height,
            duration_s=duration_s,
            fps=fps,
            smart_color_boost=smart_color_boost,
            scroll_direction="vertical",
            export_gif=export_gif,
            export_rgb565_stream=export_rgb565_stream,
            stem=stem,
        )

    monkeypatch.setattr(DmdMediaProcessor, "process_image_panorama", _legacy_sig_wrapper)
    out_fallback = tmp_path / "fallback_tate"
    res_fb = convert_image_to_panorama_gif_and_stream(
        raw_image_bytes=marquee_bytes,
        out_dir=out_fallback,
        stem="pan_marquee",
        width=64,
        height=128,
        fps=16,
        duration_s=2.5,
        color_boost=False,
    )
    assert res_fb is not None
    _assert_dmds_ltr(out_fallback / res_fb[1], 64, 128)

    # 3. Portrait jacket (200x360) on TATE 64x128 via legacy fallback -> ALSO scrolls Left -> Right,
    #    zooming uniformly to eliminate black bars and centering vertically!
    jacket_arr = np.zeros((360, 200, 3), dtype=np.uint8)
    jacket_arr[:, :90, 0] = 240
    jacket_arr[:, 110:, 2] = 240
    jacket_arr[150:210, :, 1] = 220
    j_buf = io.BytesIO()
    Image.fromarray(jacket_arr, mode="RGB").save(j_buf, format="PNG")
    out_j_fb = tmp_path / "fallback_tate_jacket"
    res_j_fb = convert_image_to_panorama_gif_and_stream(
        raw_image_bytes=j_buf.getvalue(),
        out_dir=out_j_fb,
        stem="pan_jacket",
        width=64,
        height=128,
        fps=16,
        duration_s=2.5,
        color_boost=False,
    )
    assert res_j_fb is not None
    j_raw = (out_j_fb / res_j_fb[1]).read_bytes()
    j_hdr = read_rgb565_stream_header(j_raw[:16])
    j_fbytes = 64 * 128 * 2
    j_first = rgb565_le_to_rgb888(j_raw[16 : 16 + j_fbytes], 64, 128)
    j_last_off = 16 + (j_hdr["total_frames"] - 1) * j_fbytes
    j_last = rgb565_le_to_rgb888(j_raw[j_last_off : j_last_off + j_fbytes], 64, 128)
    assert j_first[:, :, 0].mean() > j_first[:, :, 2].mean() + 40.0
    assert j_last[:, :, 2].mean() > j_last[:, :, 0].mean() + 40.0
    assert j_first[:4, :, :].max(axis=2).mean() > 90.0
    assert j_first[-4:, :, :].max(axis=2).mean() > 90.0


def test_mdns_aaaa_nsec_and_qu_unicast_reply():
    """Verify RFC 6762 `AAAA` -> `NSEC` negative response, `[AAAA, A]` dual questions, and `QU` unicast replies."""
    import socket
    import struct
    from app.infrastructure.mdns_responder import (
        DNS_CLASS_IN,
        DNS_QU_FLAG,
        DNS_TYPE_A,
        DNS_TYPE_AAAA,
        DNS_TYPE_NSEC,
        _MdnsUdpProtocol,
        encode_dns_name,
        parse_mdns_query,
    )

    wire = encode_dns_name("dmdmediashowcase.local.")

    # 1. Pure AAAA query with QU bit set -> returns NSEC + Additional A record and prefer_unicast=True
    aaaa_pkt = struct.pack("!HHHHHH", 0x4242, 0x0000, 1, 0, 0, 0) + wire + struct.pack(
        "!HH", DNS_TYPE_AAAA, DNS_CLASS_IN | DNS_QU_FLAG
    )
    nsec_reply, prefer_unicast = parse_mdns_query(
        aaaa_pkt, hostname="dmdmediashowcase.local", ipv4="192.168.1.22", port=8890
    )
    assert nsec_reply is not None
    assert prefer_unicast is True
    _tx, _fl, _qd, ancount, _ns, arcount = struct.unpack("!HHHHHH", nsec_reply[:12])
    assert ancount == 1
    assert arcount == 1
    assert struct.pack("!H", DNS_TYPE_NSEC) in nsec_reply
    assert nsec_reply.endswith(socket.inet_aton("192.168.1.22"))

    # 2. Dual-question [AAAA, A] packet -> prioritizes authoritative A answer
    dual_pkt = (
        struct.pack("!HHHHHH", 0x4343, 0x0000, 2, 0, 0, 0)
        + wire
        + struct.pack("!HH", DNS_TYPE_AAAA, DNS_CLASS_IN)
        + wire
        + struct.pack("!HH", DNS_TYPE_A, DNS_CLASS_IN)
    )
    dual_reply, _ = parse_mdns_query(
        dual_pkt, hostname="dmdmediashowcase", ipv4="192.168.1.22", port=8890
    )
    assert dual_reply is not None
    _tx2, _fl2, _qd2, ancount2, _ns2, arcount2 = struct.unpack("!HHHHHH", dual_reply[:12])
    assert ancount2 == 1
    assert arcount2 == 0
    assert dual_reply.endswith(socket.inet_aton("192.168.1.22"))

    # 3. QU query from port 5353 must trigger unicast sendto(addr) in addition to multicast
    sent = []

    class _DummyTransport:
        def sendto(self, payload, target):
            sent.append(target)

    proto = _MdnsUdpProtocol("dmdmediashowcase", "192.168.1.22", 8890)
    proto.connection_made(_DummyTransport())  # type: ignore[arg-type]
    qu_a_pkt = struct.pack("!HHHHHH", 0x5151, 0x0000, 1, 0, 0, 0) + wire + struct.pack(
        "!HH", DNS_TYPE_A, DNS_CLASS_IN | DNS_QU_FLAG
    )
    proto.datagram_received(qu_a_pkt, ("192.168.1.31", 5353))
    assert ("224.0.0.251", 5353) in sent
    assert ("192.168.1.31", 5353) in sent


def test_pirate_bootleg_publishers_filtered_while_community_homebrew_preserved():
    """Verify NES pirate ports (e.g. KOF '96 by J.Y. Company) are filtered while genuine homebrews stay allowed."""
    import xml.etree.ElementTree as ET
    from app.domain.taxonomy.game_genres import is_unofficial_game_release
    from app.infrastructure.scrapers.games.screenscraper_xml_parser import (
        parse_screenscraper_game_node,
    )

    assert is_unofficial_game_release("The King of Fighters '96", publisher="J.Y. Company") is True
    assert is_unofficial_game_release("Street Fighter IV", publisher="Hummer Team") is True
    assert is_unofficial_game_release("Super Mario World (Pirate)") is True
    assert is_unofficial_game_release("Contra [p1]") is True
    assert is_unofficial_game_release("Shinobi Neo-Geo Homebrew Edition", publisher="Indie Dev") is False
    assert is_unofficial_game_release("The King of Fighters '96", publisher="SNK") is False

    pirate_xml = ET.fromstring(
        """
        <jeu id="999999">
            <noms><nom region="wor">The King of Fighters '96</nom></noms>
            <editeur>J.Y. Company</editeur>
            <developpeur>Cony Soft</developpeur>
            <synopsis><synopsis langue="fr">Portage non licencié Famicom.</synopsis></synopsis>
            <medias><media type="ss" region="wor">https://example.com/snap.png</media></medias>
        </jeu>
        """
    )
    assert parse_screenscraper_game_node(pirate_xml, "nes", "fr") is None


@pytest.mark.asyncio
async def test_panel_geometry_canonicalization_prevents_false_slot_invalidation(tmp_path: Path, monkeypatch):
    """Verify MQTT/HTTP geometry with omitted vs explicit default poster dims (`32x32` on `128x32`) never invalidates slots."""
    from app.application.consume_once_orchestrator import ConsumeOnceOrchestrator
    from app.domain.entities import FilterCriteria, MediaCandidate, PanelGeometry, PreparedShowcasePackage, ShowcaseCard

    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "canon_geom_data"))
    orch = ConsumeOnceOrchestrator()
    orch._schedule_preparation = lambda *_a, **_kw: None  # type: ignore[method-assign]
    dmd_id = "esp32_edc5d7_games_128x32"

    await orch.register_client_domain(
        dmd_id,
        "games",
        PanelGeometry(width=128, height=32, poster_width=None, poster_height=None),
        FilterCriteria(domain="games", lang="fr"),
    )
    canon_geom = orch.client_configs[(dmd_id, "games")][0]
    dummy_pkg = PreparedShowcasePackage(
        item_id="keep_me_1",
        dmd_id=dmd_id,
        domain="games",
        geometry=canon_geom,
        candidate=MediaCandidate(
            id="neogeo_mslug",
            domain="games",
            title="Metal Slug",
            year="1996",
            badge="NEOGEO",
            badge_color="#FF9800",
            subtitle="Run and gun",
            rating="9.5",
            synopsis="Metal Slug...",
        ),
        card=ShowcaseCard(
            item_id="keep_me_1",
            domain="games",
            title="Metal Slug",
            year="1996",
            badge="NEOGEO",
            badge_color="#FF9800",
            subtitle="Run and gun",
            rating="9.5",
            synopsis="Metal Slug...",
            lang="fr",
            source_used="igdb",
            poster_width=32,
            poster_height=32,
            poster_rgb565_b64="AAH/",
            stream_frames=120,
            stream_fps=15,
        ),
        item_dir=tmp_path / "pkg_keep_me",
    )
    orch.ready_packages[(dmd_id, "games")] = dummy_pkg

    # Re-registering with explicit 32x32 poster dims on 128x32 must compare equal and preserve `ready_packages`
    await orch.register_client_domain(
        dmd_id,
        "games",
        PanelGeometry(width=128, height=32, poster_width=32, poster_height=32),
        FilterCriteria(domain="games", lang="fr"),
    )
    assert orch.ready_packages.get((dmd_id, "games")) is dummy_pkg


@pytest.mark.asyncio
async def test_zero_copy_ffmpeg_pipe_streamer_skips_dmds_header_without_temp_file(tmp_path: Path):
    """Verify `FfmpegUdpClockStreamer` streams a `DMDS` file via `pipe:0` without creating any `.raw565` disk copy."""
    from dmd_gif_converter import write_rgb565_stream
    from app.infrastructure.streaming.ffmpeg_udp_streamer import (
        DMDF_MAGIC,
        FfmpegUdpClockStreamer,
    )

    frames = [np.full((32, 128, 3), fill_value=180, dtype=np.uint8) for _ in range(3)]
    stream_file = tmp_path / "sample.rgb565.stream"
    write_rgb565_stream(frames, stream_file, fps=15)

    captured_pkts = []
    eof_event = asyncio.Event()

    async def _on_eof(_dmd: str, _item: str, _ok: bool) -> None:
        eof_event.set()

    streamer = FfmpegUdpClockStreamer(packet_sink=lambda pkt, _ip, _port: captured_pkts.append(pkt))
    try:
        ok = await streamer.start_udp_clock_stream(
            dmd_id="esp32_test",
            item_id="item_test",
            stream_path=stream_file,
            width=128,
            height=32,
            fps=15,
            udp_ip="127.0.0.1",
            udp_port=5568,
            on_eof=_on_eof,
        )
        assert ok is True
        await asyncio.wait_for(eof_event.wait(), timeout=5.0)
        assert not stream_file.with_suffix(".raw565").exists()
        assert len(captured_pkts) == 3 * 8  # 3 frames * 8 chunks (8192 B / 1024 B)
        assert captured_pkts[0][:4] == DMDF_MAGIC
    finally:
        await streamer.stop_stream("esp32_test")
