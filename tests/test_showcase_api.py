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
