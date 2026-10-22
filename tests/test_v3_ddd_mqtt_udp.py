"""
Unit & Integration Test Suite for V3.0 Architecture:
- DDD `IScraperSource` Ports & Adapters (8 sources: 1 Primary + 1 Fallback per domain)
- Clean HD (`720p`/`1080p`) `yt-dlp` format selector verification
- `JsonSeenHistoryRepository` (`seen_history.json`) & `ConsumeOnceOrchestrator` (zero repetition + post-stream disk purge)
- Embedded MQTT 3.1.1 Broker (`retain=True`, `+`/`#` wildcards, `LWT` Last Will and Testament)
- Real-Time `ffmpeg -re` UDP `DMDF` frame streamer & receiver reconstruction
- Hermetic Portable Environment isolation (`enforce_hermetic_environment`)
- Centralized Web Admin Dashboard (`/`, `/api/v1/admin/config`, `/api/v1/admin/history/reset`)
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import struct
from pathlib import Path
from typing import Dict, List, Set

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from dmd_gif_converter import write_rgb565_stream

from app.application.consume_once_orchestrator import (
    ConsumeOnceOrchestrator,
    consume_once_orchestrator,
)
from app.application.source_chain_service import DomainSourceChainService
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate, PanelGeometry
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.mqtt.embedded_broker import (
    EmbeddedMqttBroker,
    SimpleAsyncMqttClient,
    mqtt_topic_matches,
)
from app.infrastructure.mqtt.showcase_mqtt_gateway import ShowcaseMqttGateway
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.persistence.server_config_store import ServerConfigStore
from app.infrastructure.scrapers.registry import ScraperRegistry
from app.infrastructure.scrapers.youtube_resolver import HD_YTDLP_FORMAT
from app.infrastructure.streaming.ffmpeg_udp_streamer import (
    DMDF_HEADER_FORMAT,
    DMDF_MAGIC,
    FfmpegUdpClockStreamer,
    pack_dmdf_frame_chunks,
)
from app.main import app
from app.portable_env import enforce_hermetic_environment


def _make_png_bytes(w: int = 128, h: int = 180) -> bytes:
    img = Image.new("RGB", (w, h), (220, 80, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_ddd_scraper_registry_and_hd_youtube_selector():
    """Verify each domain has strictly 2 dedicated `IScraperSource` adapters (Primary + Fallback) and HD YouTube format."""
    reg = ScraperRegistry()
    expected = {
        "games": ("screenscraper", "arcadeitalia"),
        "movies": ("tmdb_movies", "itunes_movies"),
        "series": ("tmdb_series", "tvmaze"),
        "anime": ("jikan_animethemes", "anilist"),
    }
    for domain, (prim_id, fall_id) in expected.items():
        chain = reg.get_chain(domain)  # type: ignore[arg-type]
        assert len(chain) == 2
        assert isinstance(chain[0], IScraperSource)
        assert isinstance(chain[1], IScraperSource)
        assert chain[0].source_id == prim_id and chain[0].role == "primary"
        assert chain[1].source_id == fall_id and chain[1].role == "fallback"

    # Verify clean HD (720p/1080p) YouTube format selector is enforced
    assert "height<=1080" in HD_YTDLP_FORMAT
    assert "height>=720" in HD_YTDLP_FORMAT
    assert "height<=360" not in HD_YTDLP_FORMAT


@pytest.mark.asyncio
async def test_seen_history_repository_and_source_chain_failover(tmp_path: Path):
    """Verify `JsonSeenHistoryRepository` persists consumed IDs and `DomainSourceChainService` never repeats titles."""
    seen_file = tmp_path / "seen_history.json"
    repo = JsonSeenHistoryRepository(file_path=seen_file)

    class _FakePrimarySource(IScraperSource):
        source_id = "fake_primary"
        domain: DomainType = "games"
        role = "primary"

        def is_configured(self) -> bool:
            return False  # Simulates unconfigured primary API key -> must failover to fallback

        def supported_filters(self, lang: str = "fr"):
            return {}

        async def fetch_random_unseen(self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1):
            return []

        async def search_by_query(self, query: str, lang: str = "fr"):
            return None

    class _FakeFallbackSource(IScraperSource):
        source_id = "fake_fallback"
        domain: DomainType = "games"
        role = "fallback"

        def __init__(self) -> None:
            self.catalog = ["game_1", "game_2", "game_3"]

        def is_configured(self) -> bool:
            return True

        def supported_filters(self, lang: str = "fr"):
            return {}

        async def fetch_random_unseen(self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1):
            out = []
            for gid in self.catalog:
                if gid not in seen_ids:
                    out.append(
                        MediaCandidate(
                            id=gid,
                            domain="games",
                            title=f"Title {gid}",
                            year="1994",
                            badge="ARCADE",
                            badge_color="#FF5722",
                            subtitle="Arcade • 1994",
                            rating="9.0",
                            synopsis="Arcade synopsis.",
                            source_id=self.source_id,
                        )
                    )
                    if len(out) >= count:
                        break
            return out

        async def search_by_query(self, query: str, lang: str = "fr"):
            return None

    reg = ScraperRegistry()
    reg._chains["games"] = [_FakePrimarySource(), _FakeFallbackSource()]
    chain_svc = DomainSourceChainService(registry=reg, history_repo=repo)
    criteria = FilterCriteria(domain="games")

    picked_ids: List[str] = []
    for _ in range(3):
        cands = await chain_svc.acquire_unseen_candidates(criteria, count=1)
        assert len(cands) == 1
        cid = cands[0].id
        assert cid not in picked_ids
        picked_ids.append(cid)
        repo.mark_consumed("games", cid)

    assert set(picked_ids) == {"game_1", "game_2", "game_3"}
    assert repo.stats()["games"] == 3

    # Reload from disk to prove persistence
    repo_reloaded = JsonSeenHistoryRepository(file_path=seen_file)
    assert repo_reloaded.get_seen_ids("games") == {"game_1", "game_2", "game_3"}

    # 4th draw when all 3 have been seen must automatically reset and start a fresh cycle
    fourth = await chain_svc.acquire_unseen_candidates(criteria, count=1)
    assert len(fourth) == 1
    assert fourth[0].id in {"game_1", "game_2", "game_3"}


async def _connect_mqtt_client_to_broker(
    broker: EmbeddedMqttBroker, client: SimpleAsyncMqttClient
) -> asyncio.Task[None]:
    """Connect `SimpleAsyncMqttClient` to `EmbeddedMqttBroker` via UNIX socketpair (sandbox-safe)."""
    s_cli, s_srv = socket.socketpair()
    r_cli, w_cli = await asyncio.open_connection(sock=s_cli)
    r_srv, w_srv = await asyncio.open_connection(sock=s_srv)
    srv_task = asyncio.get_running_loop().create_task(broker._handle_client(r_srv, w_srv))
    await client.connect(custom_streams=(r_cli, w_cli))
    return srv_task


@pytest.mark.asyncio
async def test_embedded_mqtt_broker_retain_and_lwt():
    """Verify MQTT 3.1.1 `retain=True` immediate delivery on `SUBSCRIBE` and automatic `LWT` on crash."""
    assert mqtt_topic_matches("dmd_showcase/clients/+/config", "dmd_showcase/clients/esp32_1/config")
    assert mqtt_topic_matches("dmd_showcase/#", "dmd_showcase/server/status")
    assert not mqtt_topic_matches("dmd_showcase/clients/+/config", "dmd_showcase/clients/esp32_1/events")

    broker = EmbeddedMqttBroker(host="127.0.0.1", port=0)
    try:
        # 1. Server connects with Last Will (LWT) and publishes retained online=True
        srv_client = SimpleAsyncMqttClient(
            host="127.0.0.1",
            port=0,
            client_id="showcase_srv",
            will_topic="dmd_showcase/server/status",
            will_payload=json.dumps({"online": False}).encode("utf-8"),
            will_retain=True,
        )
        await _connect_mqtt_client_to_broker(broker, srv_client)
        await srv_client.publish(
            "dmd_showcase/server/status",
            json.dumps({"online": True}).encode("utf-8"),
            retain=True,
        )
        await asyncio.sleep(0.05)

        # 2. Late-joining DMD client subscribes and must immediately receive the retained `online: True`
        received: List[Dict[str, object]] = []
        got_event = asyncio.Event()

        async def _on_status(_topic: str, payload: bytes, _retain: bool) -> None:
            received.append(json.loads(payload.decode("utf-8")))
            got_event.set()

        dmd_client = SimpleAsyncMqttClient(host="127.0.0.1", port=0, client_id="dmd_esp32")
        await _connect_mqtt_client_to_broker(broker, dmd_client)
        await dmd_client.subscribe("dmd_showcase/server/status", _on_status)
        await asyncio.wait_for(got_event.wait(), timeout=2.0)
        assert received[-1]["online"] is True

        # 3. Simulate server crash (ungraceful socket close without DISCONNECT packet) -> Broker fires LWT!
        got_event.clear()
        await srv_client.disconnect(graceful=False)
        await asyncio.wait_for(got_event.wait(), timeout=2.0)
        assert received[-1]["online"] is False

        await dmd_client.disconnect(graceful=True)
    finally:
        await broker.stop()


@pytest.mark.asyncio
async def test_ffmpeg_udp_clock_streamer_dmdf_packets(tmp_path: Path):
    """Verify `FfmpegUdpClockStreamer` (`ffmpeg -re`) transmits valid `DMDF` UDP chunks and triggers `on_eof`."""
    width, height, fps = 64, 32, 25
    frames = [np.full((height, width, 3), fill_value=i * 40, dtype=np.uint8) for i in range(1, 5)]
    stream_file = tmp_path / "test_seq.rgb565.stream"
    write_rgb565_stream(frames, stream_file, fps=fps)

    reconstructed_frames: Dict[int, bytearray] = {}
    frame_bytes = width * height * 2

    def _capture_dmdf_pkt(pkt: bytes, _ip: str, _port: int) -> None:
        magic, seq, _chunk_idx, total_chunks, byte_offset = struct.unpack(DMDF_HEADER_FORMAT, pkt[:10])
        assert magic == DMDF_MAGIC
        assert total_chunks == (frame_bytes + 1023) // 1024
        buf = reconstructed_frames.setdefault(seq, bytearray(frame_bytes))
        payload = pkt[10:]
        buf[byte_offset : byte_offset + len(payload)] = payload

    eof_done = asyncio.Event()
    eof_args: Dict[str, object] = {}

    async def _on_eof(dmd_id: str, item_id: str, ok: bool) -> None:
        eof_args["dmd_id"] = dmd_id
        eof_args["item_id"] = item_id
        eof_args["ok"] = ok
        eof_done.set()

    streamer = FfmpegUdpClockStreamer(packet_sink=_capture_dmdf_pkt)
    started = await streamer.start_udp_clock_stream(
        dmd_id="rpi_livingroom",
        item_id="pkg_test_1",
        stream_path=stream_file,
        width=width,
        height=height,
        fps=fps,
        udp_ip="127.0.0.1",
        udp_port=4210,
        on_eof=_on_eof,
    )
    assert started is True
    await asyncio.wait_for(eof_done.wait(), timeout=5.0)
    assert eof_args.get("dmd_id") == "rpi_livingroom"
    assert eof_args.get("ok") is True
    assert len(reconstructed_frames) == 4


@pytest.mark.asyncio
async def test_consume_once_http_and_admin_endpoints(tmp_path: Path, monkeypatch):
    """Verify `/api/v1/{domain}/config`, `/next`, `/stream/...` Consume-Once & immediate disk purge, plus `/admin`."""
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "data"))
    sample_png = _make_png_bytes()
    monkeypatch.setattr(
        "app.scheduler.asset_builder._download_bytes",
        lambda url, timeout=5.0: sample_png if url else None,
    )

    seq = {"i": 0}

    async def _mock_acquire(criteria: FilterCriteria, count: int = 1, extra_exclude_ids=None):
        seq["i"] += 1
        n = seq["i"]
        return [
            MediaCandidate(
                id=f"games_adb_mslug{n}",
                domain=criteria.domain,
                title=f"Metal Slug #{n}",
                year="1996",
                badge="NEOGEO",
                badge_color="#E53935",
                subtitle="Run and gun • SNK",
                rating="9.4",
                synopsis=f"Complete localized synopsis #{n} for Metal Slug.",
                lang=criteria.lang,
                poster_url="https://example.org/mslug_poster.png",
                jacket_url="https://example.org/mslug_jacket.png",
                source_id="arcadeitalia",
            )
        ]

    monkeypatch.setattr(
        consume_once_orchestrator.chain_service,
        "acquire_unseen_candidates",
        _mock_acquire,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Admin dashboard HTML & config API
        r_dash = await client.get("/")
        assert r_dash.status_code == 200
        assert "DMD Media Showcase Server v3.0" in r_dash.text

        r_cfg = await client.post(
            "/api/v1/admin/config",
            json={"default_lang": "fr", "mqtt_mode": "embedded"},
        )
        assert r_cfg.status_code == 200
        assert r_cfg.json()["status"] == "saved"

        # 2. Register DMD client config for Consume-Once
        r_reg = await client.post(
            "/api/v1/games/config",
            json={
                "client_id": "esp32_cab_1",
                "width": 128,
                "height": 32,
                "lang": "fr",
                "text_scroll_speed": "fast",
                "jacket_panorama": True,
                "marquee_panorama": False,
                "snapshot_panorama_count": 0,
                "include_video": False,
            },
        )
        assert r_reg.status_code == 200

        # Wait until the background package is ready and claim it via GET /next
        claimed = None
        for _ in range(60):
            r_next = await client.get("/api/v1/games/next", params={"dmd_id": "esp32_cab_1"})
            assert r_next.status_code == 200
            data = r_next.json()
            if data["ready"] is True:
                claimed = data
                break
            await asyncio.sleep(0.05)

        assert claimed is not None
        item_id = claimed["item_id"]
        assert claimed["card"]["text_scroll_speed"] == "fast"
        assert claimed["card"]["poster_rgb565_b64"] != ""
        assert claimed["stream_url"] == f"/api/v1/games/stream/{item_id}.rgb565.stream"

        # 3. Stream the unified sequence -> must succeed once, then purge disk directory so a 2nd fetch returns 404!
        r_stream = await client.get(claimed["stream_url"])
        assert r_stream.status_code == 200
        assert r_stream.content[:4] == b"DMDS"

        r_stream_again = await client.get(claimed["stream_url"])
        assert r_stream_again.status_code == 404, "Consumed stream must be purged immediately after first playback!"

        # 4. Verify optional Online API Key Security (`showcase_api_key`) vs Home-Lab open mode
        await client.post("/api/v1/admin/config", json={"showcase_api_key": "secret-cloud-token"})
        r_unauth = await client.get("/api/v1/games/filters")
        assert r_unauth.status_code == 401
        r_auth_hdr = await client.get("/api/v1/games/filters", headers={"X-Showcase-Key": "secret-cloud-token"})
        assert r_auth_hdr.status_code == 200
        r_auth_bearer = await client.get(
            "/api/v1/games/filters", headers={"Authorization": "Bearer secret-cloud-token"}
        )
        assert r_auth_bearer.status_code == 200
        # Restore Home-Lab open mode
        await client.post(
            "/api/v1/admin/config",
            json={"showcase_api_key": ""},
            headers={"X-Showcase-Key": "secret-cloud-token"},
        )


def test_hermetic_portable_environment_isolation(tmp_path: Path, monkeypatch):
    """Verify `enforce_hermetic_environment()` strips host PYTHONPATH/PYTHONHOME and isolates PATH in portable mode."""
    monkeypatch.setenv("DMD_PORTABLE_MODE", "1")
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "portable_data"))
    monkeypatch.setenv("PYTHONPATH", "/host/polluted/pythonpath")
    monkeypatch.setenv("PYTHONHOME", "/host/polluted/pythonhome")

    data_dir = enforce_hermetic_environment()
    assert data_dir == (tmp_path / "portable_data").resolve()
    assert (data_dir / "stream_tmp").exists()
    assert "PYTHONPATH" not in os.environ
    assert "PYTHONHOME" not in os.environ
    assert os.environ.get("PYTHONNOUSERSITE") == "1"


@pytest.mark.asyncio
async def test_live_referential_strategy_zero_hardcoded_roms(tmp_path: Path, monkeypatch):
    """Verify `ArcadeItaliaReferentialStrategy` dynamically harvests MAME ROMs from live search without hardcoded lists."""
    from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter
    from app.infrastructure.scrapers.referential_store import ArcadeItaliaReferentialStrategy

    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "ref_data"))
    strategy = ArcadeItaliaReferentialStrategy()
    adapter = ArcadeItaliaAdapter(referential_strategy=strategy)

    class _MockResp:
        def __init__(self, status_code: int, text: str = "", json_data=None):
            self.status_code = status_code
            self.text = text
            self._json = json_data or {}

        def json(self):
            return self._json

    class _MockHttpClient:
        def __init__(self) -> None:
            self.calls: List[tuple] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url: str, params=None, headers=None, timeout=None):
            self.calls.append((url, dict(params or {})))
            if "lista_mame.php" in url:
                return _MockResp(
                    200,
                    text='<a href="dettaglio_mame.php?game_name=mslug">Metal Slug</a>'
                    '<a href="dettaglio_mame.php?game_name=garou">Garou</a>',
                )
            if "service_scraper.php" in url:
                # Must ALWAYS include `game_name` parameter (never `title` without `game_name`)
                assert "game_name" in (params or {}), "query_mame must always specify game_name!"
                return _MockResp(
                    200,
                    json_data={
                        "result": [
                            {
                                "game_name": "mslug",
                                "title": "Metal Slug - Super Vehicle-001",
                                "year": "1996",
                                "genre": "Platform / ShooterScrolling",
                                "manufacturer": "Nazca",
                                "history": "Run and gun masterpiece.",
                                "url_image_flyer": "https://adb.arcadeitalia.net/media/mslug_flyer.png",
                                "url_image_ingame": "https://adb.arcadeitalia.net/media/mslug_snap.png",
                                "url_video_shortplay": "https://adb.arcadeitalia.net/media/mslug.mp4",
                            }
                        ]
                    },
                )
            return _MockResp(404)

    mock_client = _MockHttpClient()
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.arcadeitalia_adapter.create_async_http_client",
        lambda timeout=6.0, headers=None: mock_client,
    )

    seen: Set[str] = set()
    cands = await adapter.fetch_random_unseen(FilterCriteria(domain="games", systems=["neogeo"]), seen, count=1)
    assert len(cands) == 1
    assert cands[0].id == "games_adb_mslug"
    assert cands[0].title == "Metal Slug - Super Vehicle-001"
    # Referential file must be persisted on disk
    assert strategy.store.file_path.exists()
    persisted = json.loads(strategy.store.file_path.read_text(encoding="utf-8"))
    assert "mslug" in persisted["tokens"]
    assert "garou" in persisted["tokens"]
