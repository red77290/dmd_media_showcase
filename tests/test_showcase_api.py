"""
End-to-end unit and integration tests for the DMD Media Showcase Server.

Validates:
- All 4 media domains (`games`, `movies`, `series`, `anime`)
- Multilingual localization (`fr`, `en`, `es`, `de`, `it`)
- Proactive Session + Fast Batch Poll protocol (`POST /session` -> `GET /batch`)
- Bounded worker pool execution & automatic `NextBatch` pre-warming
- Static & animated asset generation (`.png`, `.rgb565`, `.gif`, `.rgb565.stream`)
- Dynamic O(1) stream duration truncation (`?max_duration_s=`)
- Future-ready `POST /api/v1/{domain}/on-demand` priority queue injection
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dmd_gif_converter import read_rgb565_stream_header

from app.config import settings
from app.main import app
from app.scheduler.worker_pool import batch_manager


@pytest.fixture(autouse=True)
async def _isolated_test_environment(tmp_path: Path):
    """Isolate cache directory and use ThreadPoolExecutor in tests for fast deterministic execution."""
    orig_cache = settings.cache_dir
    orig_pool = settings.use_process_pool
    orig_master_max = settings.server_master_clip_max_s

    settings.cache_dir = tmp_path / "showcase_cache"
    settings.use_process_pool = False
    settings.server_master_clip_max_s = 4.0

    await batch_manager.stop()
    batch_manager.sessions.clear()
    batch_manager.asset_index.clear()
    batch_manager.start()

    yield

    await batch_manager.stop()
    settings.cache_dir = orig_cache
    settings.use_process_pool = orig_pool
    settings.server_master_clip_max_s = orig_master_max


@pytest.mark.asyncio
async def test_health_and_domain_filters():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["max_workers"] >= 1
        assert set(data["providers_configured"].keys()) == {"games", "movies", "series", "anime"}

        for domain in ("games", "movies", "series", "anime"):
            rf = await client.get(f"/api/v1/{domain}/filters")
            assert rf.status_code == 200
            fdata = rf.json()
            assert fdata["domain"] == domain
            assert "fr" in fdata["languages"]
            assert "en" in fdata["languages"]
            assert "genres" in fdata


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["games", "movies", "series", "anime"])
@pytest.mark.parametrize("lang", ["fr", "en", "es"])
async def test_static_session_and_multilingual_assets(domain: str, lang: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg_resp = await client.post(
            f"/api/v1/{domain}/session",
            json={
                "client_id": f"dmd-{domain}-{lang}",
                "count": 2,
                "lang": lang,
                "width": 128,
                "height": 32,
                "include_video": False,
                "include_synopsis": True,
            },
        )
        assert reg_resp.status_code == 200
        reg = reg_resp.json()
        assert reg["status"] == "ready"
        assert reg["batch_size"] == 2
        assert reg["ready_count"] == 2
        session_id = reg["session_id"]

        batch_resp = await client.get(
            f"/api/v1/{domain}/batch",
            params={"session_id": session_id},
        )
        assert batch_resp.status_code == 200
        batch = batch_resp.json()
        assert batch["status"] == "ready"
        assert len(batch["items"]) == 2

        first = batch["items"][0]
        assert first["domain"] == domain
        assert first["lang"] == lang
        assert len(first["title"]) > 0
        assert len(first["synopsis"]) > 10

        # Verify binary RGB565 static assets have exact expected byte sizes
        poster_r = await client.get(first["assets"]["poster_rgb565"])
        assert poster_r.status_code == 200
        assert poster_r.headers.get("X-DMD-Media-Ready") == "1"
        assert len(poster_r.content) == 32 * 32 * 2

        marquee_r = await client.get(first["assets"]["marquee_rgb565"])
        assert marquee_r.status_code == 200
        assert len(marquee_r.content) == 128 * 32 * 2

        snap_r = await client.get(first["assets"]["snapshot_pan_rgb565"])
        assert snap_r.status_code == 200
        assert len(snap_r.content) == 128 * 72 * 2

        poster_png = await client.get(first["assets"]["poster_png"])
        assert poster_png.status_code == 200
        assert poster_png.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_video_batch_preparation_and_dynamic_stream_truncation():
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
                "include_video": True,
                "video_max_duration_s": 3.0,
                "video_fps": 10,
                "systems": ["neogeo", "mame"],
            },
        )
        assert reg_resp.status_code == 200
        session_id = reg_resp.json()["session_id"]

        # Poll batch status until bounded worker pool finishes rendering the 2 videos
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
        item = batch["items"][0]
        assert item["video_ready"] is True

        # Verify NextBatch was automatically scheduled one step ahead
        state = batch_manager.sessions[session_id]
        assert state.next_batch is not None

        # Verify .rgb565.stream binary header and dynamic ?max_duration_s= truncation
        stream_url_base = item["assets"]["video_stream_rgb565"].split("?")[0]

        r_full = await client.get(stream_url_base, params={"max_duration_s": 3.0})
        assert r_full.status_code == 200
        hdr_full = read_rgb565_stream_header(r_full.content[:16])
        assert hdr_full["width"] == 128
        assert hdr_full["height"] == 32
        assert hdr_full["total_frames"] >= 15

        r_short = await client.get(stream_url_base, params={"max_duration_s": 1.0})
        assert r_short.status_code == 200
        hdr_short = read_rgb565_stream_header(r_short.content[:16])
        assert hdr_short["total_frames"] <= 10
        assert len(r_short.content) == 16 + hdr_short["total_frames"] * hdr_short["frame_byte_size"]
        assert len(r_short.content) < len(r_full.content)

        # Verify .gif endpoint and truncation
        gif_url_base = item["assets"]["video_gif"].split("?")[0]
        r_gif = await client.get(gif_url_base, params={"max_duration_s": 1.5})
        assert r_gif.status_code == 200
        assert r_gif.headers["content-type"] == "image/gif"
        assert r_gif.content[:6] in (b"GIF87a", b"GIF89a")


@pytest.mark.asyncio
async def test_on_demand_priority_injection_and_ttl_cleanup():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register a session first
        reg = await client.post(
            "/api/v1/movies/session",
            json={
                "client_id": "living-room-dmd",
                "count": 2,
                "lang": "fr",
                "include_video": False,
            },
        )
        session_id = reg.json()["session_id"]

        # Trigger an on-demand item targeting that session
        od_resp = await client.post(
            "/api/v1/movies/on-demand",
            json={
                "query": "Blade Runner",
                "domain": "movies",
                "lang": "fr",
                "width": 128,
                "height": 32,
                "video_max_duration_s": 2.0,
                "video_fps": 10,
                "target_session_id": session_id,
            },
        )
        assert od_resp.status_code == 200
        od_item = od_resp.json()
        assert od_item["on_demand"] is True
        assert "Blade Runner" in od_item["title"]

        # Wait for the high-priority on-demand video worker job to complete
        for _ in range(60):
            if batch_manager.active_workers == 0 and batch_manager.queue_depth == 0:
                break
            await asyncio.sleep(0.1)

        # Next batch poll should inject the ready on-demand item at index 0
        batch_r = await client.get("/api/v1/movies/batch", params={"session_id": session_id})
        items = batch_r.json()["items"]
        assert items[0]["on_demand"] is True
        assert "Blade Runner" in items[0]["title"]

        # Force session expiration and verify cleanup_expired_sessions purges it
        batch_manager.sessions[session_id].last_accessed = time.monotonic() - (
            settings.session_ttl_seconds + 10
        )
        purged = batch_manager.cleanup_expired_sessions()
        assert purged == 1
        assert session_id not in batch_manager.sessions
