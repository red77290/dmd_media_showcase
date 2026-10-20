"""
End-to-end unit and integration tests for the DMD Media Showcase Server.

Validates:
- All 4 media domains (`games`, `movies`, `series`, `anime`)
- Multilingual localization (`fr`, `en`, `es`, `de`, `it`)
- Configurable `primary_source` + automatic fallback cascade + engine-supplied credentials
- Top-to-bottom scrolling panorama images (`image_count` via `dmd_gif_converter` `scroll_enabled=True`)
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

import cv2
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from dmd_gif_converter import read_rgb565_stream_header

from app.config import settings
from app.main import app
from app.scheduler.worker_pool import batch_manager


def _make_sample_png_bytes(width: int = 160, height: int = 120, color=(220, 60, 40)) -> bytes:
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
async def _isolated_test_environment(tmp_path: Path):
    """Isolate cache directory and use ThreadPoolExecutor in tests for fast deterministic execution."""
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

    yield

    await batch_manager.stop()
    settings.cache_dir = orig_cache
    settings.use_process_pool = orig_pool
    settings.server_master_clip_max_s = orig_master_max


@pytest.mark.asyncio
async def test_health_and_domain_filters():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/v1/health", "/health"):
            r = await client.get(path)
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
            assert "sources" in fdata
            assert "fr" in fdata["languages"]
            assert "en" in fdata["languages"]
            assert "genres" in fdata


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["games", "movies", "series", "anime"])
@pytest.mark.parametrize("lang", ["fr", "en", "es"])
async def test_static_session_and_panorama_images(domain: str, lang: str, monkeypatch):
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
                "client_id": f"dmd-{domain}-{lang}",
                "count": 2,
                "lang": lang,
                "width": 128,
                "height": 32,
                "image_count": 2,
                "primary_source": "auto",
                "include_video": False,
                "include_synopsis": True,
            },
        )
        assert reg_resp.status_code == 200
        reg = reg_resp.json()
        assert reg["status"] == "ready"
        assert reg["batch_size"] == 2
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
                "image_count": 2,
                "primary_source": "arcadeitalia",
                "include_video": True,
                "video_max_duration_s": 3.0,
                "video_fps": 10,
                "systems": ["neogeo", "mame"],
            },
        )
        assert reg_resp.status_code == 200
        session_id = reg_resp.json()["session_id"]

        # Force candidate 0 to have a valid video_url and candidate 1 to have NO video_url
        state = batch_manager.sessions[session_id]
        keys = list(state.current_batch.candidates.keys())
        state.current_batch.candidates[keys[0]].video_url = "https://example.org/shortplay.mp4"
        state.current_batch.candidates[keys[0]].image_urls = [
            "https://example.org/img1.png",
            "https://example.org/img2.png",
        ]
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

        # Verify .rgb565.stream binary header and dynamic ?max_duration_s= truncation
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
        # 1. Auto-recovery when RPi polls an unknown `ses_games_...` session after container restart
        r_rec = await client.get(
            "/api/v1/games/batch",
            params={"session_id": "ses_games_b32a6b80e723"},
        )
        assert r_rec.status_code == 200
        assert r_rec.json()["session_id"] == "ses_games_b32a6b80e723"

        # 2. Verify immediate batch rotation on subsequent turn polls
        reg = await client.post(
            "/api/v1/games/session",
            json={
                "client_id": "rpi_rotation_test",
                "count": 3,
                "lang": "fr",
                "include_video": False,
                "image_count": 0,
            },
        )
        ses_id = reg.json()["session_id"]

        b1 = (await client.get("/api/v1/games/batch", params={"session_id": ses_id})).json()
        titles_1 = [it["title"] for it in b1["items"]]

        # Simulate elapsed turn (> 2s) and poll again -> must rotate to NextBatch with different games!
        state = batch_manager.sessions[ses_id]
        state.current_batch.last_served_at = time.monotonic() - 5.0

        b2 = (await client.get("/api/v1/games/batch", params={"session_id": ses_id})).json()
        titles_2 = [it["title"] for it in b2["items"]]
        assert titles_1 != titles_2, "Second rotation turn must serve NextBatch with new games!"


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
                "image_count": 0,
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
                "image_count": 0,
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
