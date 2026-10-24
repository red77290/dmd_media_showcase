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
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
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
        "games": ["screenscraper", "arcadeitalia"],
        "movies": ["tmdb_movies", "itunes_movies"],
        "series": ["tmdb_series", "tvdb_series", "tvmaze"],
        "anime": ["jikan_animethemes", "anilist"],
    }
    for domain, expected_sources in expected.items():
        chain = reg.get_chain(domain)  # type: ignore[arg-type]
        assert len(chain) == len(expected_sources)
        for idx, src in enumerate(chain):
            assert isinstance(src, IScraperSource)
            assert src.source_id == expected_sources[idx]
        assert chain[0].role == "primary"
        assert chain[1].role == "fallback"

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
        assert "DMD Media Showcase Server" in r_dash.text

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


@pytest.mark.asyncio
async def test_consolidated_referential_and_nes_platformer_strict_isolation(tmp_path: Path, monkeypatch):
    """
    Verify:
    1. Consolidated referential (`/api/v1/games/referential?lang=fr`) exposes relational
       `System -> Genres & Decades` and French labels (`Plateforme`, `Course / Conduite`).
    2. Synonym normalization maps `Famicom`/`Nintendo Entertainment System` -> `nes` and `Platformer`/`Plateforme` -> `platform`.
    3. When `systems=["nes"]`, `genres=["platform"]`, `lang="fr"` is requested:
       - `adb.arcadeitalia.net` is NEVER queried (`0` HTTP calls to ArcadeItalia).
       - Hardware consoles (`Nintendo Entertainment System`, `CIC`) and non-NES games (`Goof Troop`, `F-Zero`) are rejected.
       - Verified Libretro `(France)` / `(Europe)` boxarts are prioritized over `(USA)`.
    """
    from app.domain.taxonomy import ReferentialConsolidator, resolve_canonical_genre, resolve_canonical_system
    from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter
    from app.infrastructure.scrapers.games.libretro_wikidata_adapter import LibretroWikidataAdapter
    from app.infrastructure.scrapers.referential_store import LibretroWikidataReferentialStrategy

    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "nes_ref_data"))

    assert resolve_canonical_system("Nintendo Entertainment System") == "nes"
    assert resolve_canonical_system("Famicom") == "nes"
    assert resolve_canonical_genre("games", "Platformer") == "platform"
    assert resolve_canonical_genre("games", "Plateforme") == "platform"
    assert not ReferentialConsolidator.source_supports_criteria(
        "arcadeitalia", FilterCriteria(domain="games", systems=["nes"], genres=["platform"])
    )
    assert "[vcodec!*=av01]" in HD_YTDLP_FORMAT

    class _MockResp:
        def __init__(self, status_code: int, json_data=None):
            self.status_code = status_code
            self._json = json_data or {}

        def json(self):
            return self._json

    class _MockHttpClient:
        def __init__(self) -> None:
            self.urls: List[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url: str, params=None, headers=None, timeout=None):
            self.urls.append(url)
            if "adb.arcadeitalia.net" in url:
                raise AssertionError("ArcadeItalia must NEVER be queried when systems=['nes']!")
            if "query.wikidata.org/sparql" in url:
                return _MockResp(
                    200,
                    json_data={
                        "results": {
                            "bindings": [
                                # Should be rejected (hardware console title)
                                {
                                    "game": {"value": "http://www.wikidata.org/entity/Q172742"},
                                    "titleEn": {"value": "Nintendo Entertainment System"},
                                    "titleFr": {"value": "Nintendo Entertainment System"},
                                    "year": {"value": "1985"},
                                },
                                # Should be rejected (SNES-only game not in NES Libretro tree)
                                {
                                    "game": {"value": "http://www.wikidata.org/entity/Q2087654"},
                                    "titleEn": {"value": "Goof Troop"},
                                    "titleFr": {"value": "Goof Troop"},
                                    "year": {"value": "1993"},
                                },
                                # Valid NES Platformer
                                {
                                    "game": {"value": "http://www.wikidata.org/entity/Q12345"},
                                    "titleEn": {"value": "Super Mario Bros. 3"},
                                    "titleFr": {"value": "Super Mario Bros. 3"},
                                    "year": {"value": "1988"},
                                    "frTitle": {"value": "Super Mario Bros. 3"},
                                },
                            ]
                        }
                    },
                )
            if "api.github.com/repos/libretro-thumbnails/Nintendo_-_Nintendo_Entertainment_System/git/trees/master" in url:
                return _MockResp(
                    200,
                    json_data={
                        "tree": [
                            {"path": "Named_Boxarts", "sha": "sha_box"},
                            {"path": "Named_Titles", "sha": "sha_tit"},
                            {"path": "Named_Snaps", "sha": "sha_snp"},
                        ]
                    },
                )
            if "git/trees/sha_box" in url:
                return _MockResp(
                    200,
                    json_data={
                        "tree": [
                            {"path": "Super Mario Bros. 3 (USA).png"},
                            {"path": "Super Mario Bros. 3 (France).png"},
                            {"path": "Super Mario Bros. 3 (Europe).png"},
                        ]
                    },
                )
            if "git/trees/sha_tit" in url or "git/trees/sha_snp" in url:
                return _MockResp(
                    200,
                    json_data={"tree": [{"path": "Super Mario Bros. 3 (Europe).png"}]},
                )
            if "fr.wikipedia.org" in url:
                return _MockResp(
                    200,
                    json_data={
                        "extract": "Super Mario Bros. 3 est un jeu vidéo de plates-formes développé par Nintendo pour la NES."
                    },
                )
            return _MockResp(404)

    mock_client = _MockHttpClient()
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.libretro_wikidata_adapter.create_async_http_client",
        lambda timeout=7.5, headers=None: mock_client,
    )

    strat = LibretroWikidataReferentialStrategy()
    libretro_adapter = LibretroWikidataAdapter(referential_strategy=strat)
    adb_adapter = ArcadeItaliaAdapter(console_adapter=libretro_adapter)

    cands = await adb_adapter.fetch_random_unseen(
        FilterCriteria(domain="games", systems=["nes"], genres=["platform"], lang="fr"),
        seen_ids=set(),
        count=2,
    )
    assert len(cands) == 1
    cand = cands[0]
    assert cand.title == "Super Mario Bros. 3"
    assert "plates-formes développé par Nintendo" in cand.synopsis
    assert "Super%20Mario%20Bros.%203%20%28France%29.png" in (cand.poster_url or "")
    assert not any("adb.arcadeitalia.net" in u for u in mock_client.urls)


@pytest.mark.asyncio
async def test_retained_config_change_invalidation_and_docker_nat_client_ip(tmp_path: Path):
    """
    Verify:
    1. When a DMD client updates its retained `config`, any stale prepared package is immediately
       invalidated and deleted so new filters (`NES` + `Platform`) take effect right away.
    2. Explicit `client_ip` (`192.168.1.55`) overrides Docker bridge `_peer_ip` (`172.18.0.1`) for UDP streaming.
    3. Numeric `text_scroll_speed` (`"1".."10"`) is preserved end-to-end.
    """
    orch = ConsumeOnceOrchestrator()
    await orch.handle_mqtt_client_config(
        "rpi_living",
        {
            "width": 128,
            "height": 32,
            "lang": "fr",
            "client_ip": "192.168.1.55",
            "_peer_ip": "172.18.0.1",
            "domains": {
                "games": {
                    "systems": "Famicom",
                    "genres": "Plateforme",
                    "text_scroll_speed": "8",
                }
            },
        },
    )
    assert orch.client_udp_ips["rpi_living"] == "192.168.1.55"
    _, criteria = orch.client_configs[("rpi_living", "games")]
    assert criteria.systems == ["nes"]
    assert criteria.genres == ["platform"]
    assert criteria.text_scroll_speed == "8"
    assert criteria.lang == "fr"


@pytest.mark.asyncio
async def test_dmd_reboot_mid_broadcast_recovers_and_cleans_abandoned_slot(tmp_path: Path):
    """
    Verify that when a DMD reboots while displaying a Card (after `start_card` was sent,
    so the server popped `pkg_A` into `active_packages` with `consumed=True` and is waiting
    for `start_stream`):
    1. When the rebooted DMD reconnects and publishes `dmd_showcase/clients/{dmd_id}/config`,
       the abandoned `pkg_A` in `active_packages` is immediately purged.
    2. If `N+1` (`pkg_B`) is already in `ready_packages`, the server immediately re-publishes
       `ready: true` (`retain=True`) so the rebooted DMD is never stuck waiting.
    """
    published_topics = []

    class _MockMqtt:
        def set_handlers(self, **_kwargs):
            pass

        async def publish_json(self, topic: str, data: dict, retain: bool = False):
            published_topics.append((topic, data, retain))

    class _MockStreamer:
        def __init__(self):
            self.stopped = []

        async def stop_stream(self, dmd_id: str):
            self.stopped.append(dmd_id)

    mock_mqtt = _MockMqtt()
    mock_streamer = _MockStreamer()
    orch = ConsumeOnceOrchestrator(mqtt_gateway=mock_mqtt, streamer=mock_streamer)  # type: ignore[arg-type]

    cand_a = MediaCandidate(
        id="nes_castlevania",
        domain="games",
        title="Castlevania",
        year="1986",
        badge="NES",
        badge_color="#D32F2F",
        subtitle="Plateforme • NES",
        rating="9.0",
        synopsis="Simon Belmont...",
    )
    card_a = ShowcaseCard(
        item_id="pkg_a",
        domain="games",
        title="Castlevania",
        year="1986",
        badge="NES",
        badge_color="#D32F2F",
        subtitle="Plateforme • NES",
        rating="9.0",
        synopsis="Simon Belmont...",
        lang="fr",
        source_used="libretro_wikidata",
        poster_width=32,
        poster_height=32,
        poster_rgb565_b64="AAH/",
        stream_frames=120,
    )
    dir_a = tmp_path / "pkg_a"
    dir_a.mkdir(parents=True, exist_ok=True)
    pkg_a = PreparedShowcasePackage(
        item_id="pkg_a",
        domain="games",
        dmd_id="rpi_games_128x32",
        candidate=cand_a,
        card=card_a,
        geometry=PanelGeometry(128, 32),
        item_dir=dir_a,
        consumed=True,  # Claimed by start_card before DMD rebooted!
    )
    orch.active_packages["pkg_a"] = pkg_a

    # Suppose N+1 (`pkg_b`) finished preparing in `ready_packages`
    card_b = ShowcaseCard(
        item_id="pkg_b",
        domain="games",
        title="Mega Man 2",
        year="1988",
        badge="NES",
        badge_color="#D32F2F",
        subtitle="Plateforme • NES",
        rating="9.4",
        synopsis="Mega Man...",
        lang="fr",
        source_used="libretro_wikidata",
        poster_width=32,
        poster_height=32,
        poster_rgb565_b64="AAH/",
        stream_frames=150,
    )
    pkg_b = PreparedShowcasePackage(
        item_id="pkg_b",
        domain="games",
        dmd_id="rpi_games_128x32",
        candidate=cand_a,
        card=card_b,
        geometry=PanelGeometry(128, 32),
        item_dir=tmp_path / "pkg_b",
        consumed=False,
    )
    orch.ready_packages[("rpi_games_128x32", "games")] = pkg_b
    orch._schedule_preparation = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    # Pre-register same config so `old_pair == (geometry, criteria)` (simulating reboot with unchanged config)
    cfg_payload = {
        "dmd_id": "rpi_games_128x32",
        "client_ip": "192.168.1.177",
        "width": 128,
        "height": 32,
        "lang": "fr",
        "domains": {"games": {"systems": ["nes"], "genres": ["platform"]}},
    }
    orch.client_configs[("rpi_games_128x32", "games")] = (
        PanelGeometry(128, 32),
        orch._parse_domain_criteria("games", cfg_payload["domains"]["games"], top_lang="fr"),
    )

    # DMD reboots and publishes its `config`
    await orch.handle_mqtt_client_config("rpi_games_128x32", cfg_payload)

    # 1. Abandoned pre-reboot package `pkg_a` was purged and any running stream stopped
    assert "pkg_a" not in orch.active_packages
    assert not dir_a.exists()
    assert "rpi_games_128x32" in mock_streamer.stopped

    # 2. Ready package `pkg_b` was immediately re-published to `dmd_showcase/clients/rpi_games_128x32/games/ready`
    ready_pubs = [p for p in published_topics if p[0].endswith("/games/ready") and p[1].get("ready") is True]
    assert len(ready_pubs) == 1
    assert ready_pubs[0][1]["item_id"] == "pkg_b"

    # 3. Verify 2-slot standby promotion: if `pkg_c` is in `standby_packages`, claiming `pkg_b`
    # immediately promotes `pkg_c` to `ready_packages` and publishes `ready: true` with zero wait!
    card_c = ShowcaseCard(
        item_id="pkg_c",
        domain="games",
        title="Contra",
        year="1988",
        badge="NES",
        badge_color="#D32F2F",
        subtitle="Run and gun • NES",
        rating="9.2",
        synopsis="Contra...",
        lang="fr",
        source_used="libretro_wikidata",
        poster_width=32,
        poster_height=32,
        poster_rgb565_b64="AAH/",
        stream_frames=160,
    )
    pkg_c = PreparedShowcasePackage(
        item_id="pkg_c",
        domain="games",
        dmd_id="rpi_games_128x32",
        candidate=cand_a,
        card=card_c,
        geometry=PanelGeometry(128, 32),
        item_dir=tmp_path / "pkg_c",
        consumed=False,
    )
    orch.standby_packages[("rpi_games_128x32", "games")] = pkg_c
    published_topics.clear()

    claimed_b = await orch.claim_next_package("rpi_games_128x32", "games", item_id="pkg_b")
    assert claimed_b is not None and claimed_b.item_id == "pkg_b"
    assert orch.ready_packages[("rpi_games_128x32", "games")].item_id == "pkg_c"
    assert ("rpi_games_128x32", "games") not in orch.standby_packages
    assert any(p[1].get("ready") is True and p[1].get("item_id") == "pkg_c" for p in published_topics)


@pytest.mark.asyncio
async def test_itunes_movies_fallback_and_client_offline_domain_switch(tmp_path: Path, monkeypatch):
    """
    Verify:
    1. `ItunesMovieAdapter` discovers real films via Wikipedia + Apple iTunes / IMDb fallback
       even when `country=FR` returns empty on raw English genre keywords.
    2. When a DMD client switches domain on the same LAN IP (`rpi_games_128x32` -> `rpi_movies_128x32`)
       or sends `client_offline`, stale background prep tasks are cancelled and never re-scheduled.
    """
    from app.infrastructure.scrapers.movies.itunes_movie_adapter import ItunesMovieAdapter

    class _MockResp:
        def __init__(self, status_code: int, json_data=None):
            self.status_code = status_code
            self._json = json_data or {}

        def json(self):
            return self._json

    class _MockMovieClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url: str, params=None, timeout=None):
            p = dict(params or {})
            if "en.wikipedia.org/w/api.php" in url:
                return _MockResp(
                    200,
                    json_data={
                        "query": {
                            "search": [
                                {"title": "The NeverEnding Story (film)", "snippet": "1984 fantasy film"}
                            ]
                        }
                    },
                )
            if "itunes.apple.com/search" in url:
                # Simulate `country=FR` returning empty, and `country=US` returning the official track + .m4v trailer
                if p.get("country") == "FR":
                    return _MockResp(200, json_data={"resultCount": 0, "results": []})
                return _MockResp(
                    200,
                    json_data={
                        "resultCount": 1,
                        "results": [
                            {
                                "trackId": 998877,
                                "trackName": "The NeverEnding Story",
                                "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Video/v4/poster/100x100bb.jpg",
                                "releaseDate": "1984-04-06T08:00:00Z",
                                "primaryGenreName": "Fantasy",
                                "artistName": "Wolfgang Petersen",
                                "longDescription": "A boy reads a magical book about Fantasia.",
                                "previewUrl": "https://video-ssl.itunes.apple.com/itunes-assets/neverending.m4v",
                            }
                        ],
                    },
                )
            if "fr.wikipedia.org/api/rest_v1/page/summary" in url:
                return _MockResp(
                    200,
                    json_data={
                        "extract": "L'Histoire sans fin est un film de fantasy réalisé par Wolfgang Petersen sorti en 1984."
                    },
                )
            return _MockResp(404)

    monkeypatch.setattr(
        "app.infrastructure.scrapers.movies.itunes_movie_adapter.create_async_http_client",
        lambda timeout=6.0, headers=None: _MockMovieClient(),
    )

    adapter = ItunesMovieAdapter()
    cands = await adapter.fetch_random_unseen(
        FilterCriteria(domain="movies", genres=["fantasy"], decades=["1980s"], lang="fr"),
        seen_ids=set(),
        count=1,
    )
    assert len(cands) == 1
    assert cands[0].title == "The NeverEnding Story"
    assert cands[0].year == "1984"
    assert cands[0].video_url == "https://video-ssl.itunes.apple.com/itunes-assets/neverending.m4v"
    assert "L'Histoire sans fin" in cands[0].synopsis

    # Verify `client_offline` stops active UDP streams while preserving `client_configs`
    # so multi-engine rotation does NOT cancel background retained preparation.
    scheduled_calls = []
    stopped_streams = []

    class _NoopMqtt:
        def set_handlers(self, **_kwargs):
            pass

        async def publish_json(self, topic: str, data: dict, retain: bool = False):
            pass

    class _NoopStreamer:
        async def stop_stream(self, dmd_id: str):
            stopped_streams.append(dmd_id)

    orch = ConsumeOnceOrchestrator(mqtt_gateway=_NoopMqtt(), streamer=_NoopStreamer())  # type: ignore[arg-type]
    orch._schedule_preparation = lambda d, dom: scheduled_calls.append((d, dom))  # type: ignore[method-assign]
    orch.client_configs[("rpi_games_128x32", "games")] = (
        PanelGeometry(128, 32),
        FilterCriteria(domain="games"),
    )
    await orch.handle_mqtt_client_event("rpi_games_128x32", {"event": "client_offline"})
    assert ("rpi_games_128x32", "games") in orch.client_configs
    assert stopped_streams == ["rpi_games_128x32"]
    assert scheduled_calls == []

    # Explicit `unregister: true` payload cleanly removes `client_configs`
    await orch.handle_mqtt_client_config("rpi_games_128x32", {"unregister": True})
    assert ("rpi_games_128x32", "games") not in orch.client_configs


@pytest.mark.asyncio
async def test_tmdb_v3_v4_auth_and_jikan_embed_trailer_extraction(monkeypatch):
    """Verify TMDB v3/v4 token support, Jikan v4 embed_url extraction, and AdminServerConfigPayload tvdb_api_key."""
    from app.infrastructure.scrapers.anime.jikan_animethemes_adapter import JikanAnimeThemesAdapter
    from app.infrastructure.scrapers.catalog_metadata import ANIME_GENRES
    from app.infrastructure.scrapers.http_utils import build_tmdb_auth
    from app.models import AdminServerConfigPayload

    # 1. TMDB v3 hex key vs v4 Read Access Token
    p3, h3 = build_tmdb_auth("a1b2c3d4e5f60718293a4b5c6d7e8f90")
    assert p3 == {"api_key": "a1b2c3d4e5f60718293a4b5c6d7e8f90"}
    assert h3 == {}

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiIxMjM0NTY3ODkwIn0.signature"
    p4, h4 = build_tmdb_auth(jwt)
    assert p4 == {}
    assert h4 == {"Authorization": f"Bearer {jwt}"}

    # 2. AdminServerConfigPayload supports tvdb_api_key
    payload = AdminServerConfigPayload(tmdb_api_key="tmdb123", tvdb_api_key="tvdb456")
    assert payload.tvdb_api_key == "tvdb456"

    # 3. Jikan v4 embed_url trailer extraction when youtube_id is None
    async def _mock_resolve(*_args, **_kwargs):
        return ("Cowboy Bebop FR", "Synopsis français officiel de Cowboy Bebop.", None)

    async def _mock_animethemes(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.infrastructure.scrapers.anime.jikan_animethemes_adapter.resolve_multilingual_media_details",
        _mock_resolve,
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.anime.jikan_animethemes_adapter.enrich_with_animethemes_webm",
        _mock_animethemes,
    )

    adapter = JikanAnimeThemesAdapter()
    cand = await adapter._build_jikan_candidate(
        client=None,
        item={
            "mal_id": 1,
            "title": "Cowboy Bebop",
            "year": 1998,
            "score": 8.75,
            "synopsis": "English synopsis.",
            "images": {"jpg": {"large_image_url": "https://cdn.myanimelist.net/images/anime/4/19644l.jpg"}},
            "trailer": {
                "youtube_id": None,
                "url": None,
                "embed_url": "https://www.youtube-nocookie.com/embed/OhNwckCLzis?enablejsapi=1&wmode=opaque&autoplay=1",
            },
        },
        g_meta=ANIME_GENRES["scifi"],
        lang="fr",
        video_type="opening",
    )
    assert cand is not None
    assert cand.title == "Cowboy Bebop FR"
    assert "Synopsis français officiel" in cand.synopsis
    assert cand.youtube_url == "https://www.youtube.com/watch?v=OhNwckCLzis"


def test_screenscraper_jeu_infos_xml_official_parser_and_media_parent_isolation():
    """
    Verify `ScreenScraperAdapter` & `parse_jeu_infos_xml` against the official `jeuInfos.xml` reference:
    - Correct `SYSTEM_METADATA` `ss_id` values (`cps1=105`, `cps2=106`, `cps3=107`).
    - Extracts `<noms>`, `<synopsis lang="fr">`, `<dates>`, `<editeur>`, `<genres>`, `<note>`.
    - Strictly filters `<medias>` to `parent="jeu"`, ignoring `parent="editeur"`, `parent="famille"`, `parent="genre"`, `parent="region"`.
    - Enforces `criteria.genres` and `criteria.decades` filtering against `<jeu>` XML.
    """
    from app.infrastructure.scrapers.catalog_metadata import SYSTEM_METADATA
    from app.infrastructure.scrapers.games.screenscraper_adapter import (
        ScreenScraperAdapter,
        parse_jeu_infos_xml,
    )

    assert SYSTEM_METADATA["cps1"]["ss_id"] == 105
    assert SYSTEM_METADATA["cps2"]["ss_id"] == 106
    assert SYSTEM_METADATA["cps3"]["ss_id"] == 107

    ref_xml_path = Path("/Users/red1l/Documents/work/git/perso/jeuInfos.xml")
    if ref_xml_path.exists():
        xml_text = ref_xml_path.read_text(encoding="utf-8")
    else:
        xml_text = """<?xml version="1.0" encoding="UTF-8" ?>
        <Data><jeu id="3" romid="129379">
            <systeme id="1" parentid="0">Megadrive</systeme>
            <editeur id="3">SEGA</editeur>
            <developpeur id="518">Sonic Team</developpeur>
            <note>18</note>
            <noms><nom region="eu">Sonic The Hedgehog 2</nom><nom region="us">Sonic The Hedgehog 2</nom></noms>
            <synopsis><synopsis langue="fr">Sonic est de retour et meilleur que jamais. Tails est son nouvel ami.</synopsis></synopsis>
            <dates><date region="eu">1992-11-24</date></dates>
            <genres><genre id="10" nomcourt="Plateforme" principale="1" lang="fr">Plateforme</genre></genres>
            <medias>
                <media parent="jeu" type="box-2D" region="eu">https://neoclone.screenscraper.fr/media/1/3/box-2D(eu).png</media>
                <media parent="jeu" type="screenmarquee" region="wor">https://neoclone.screenscraper.fr/media/1/3/screenmarquee(wor).png</media>
                <media parent="jeu" type="ss" region="wor">https://neoclone.screenscraper.fr/media/1/3/ss(wor).png</media>
                <media parent="jeu" type="video-normalized" region="">https://neoclone.screenscraper.fr/media/1/3/video-normalized.mp4</media>
                <media parent="editeur" type="pictomonochrome" region="wor">https://neoclone.screenscraper.fr/media/editeur/sega.png</media>
                <media parent="famille" type="figurine" region="wor">https://neoclone.screenscraper.fr/media/famille/sonic_fig.png</media>
            </medias>
        </jeu></Data>"""

    jeu_nodes = parse_jeu_infos_xml(xml_text)
    assert len(jeu_nodes) == 1
    jeu_node = jeu_nodes[0]
    assert jeu_node.get("id") == "3"

    adapter = ScreenScraperAdapter()
    cand = adapter._parse_game_node(
        jeu=jeu_node,
        sys_key="megadrive",
        lang="fr",
        criteria=FilterCriteria(domain="games", systems=["megadrive"], genres=["platform"], decades=["1990s"], lang="fr"),
    )
    assert cand is not None
    assert cand.id == "games_ss_3"
    assert cand.title == "Sonic The Hedgehog 2"
    assert cand.year == "1992"
    assert cand.subtitle == "Plateforme • SEGA"
    assert "Sonic est de retour" in cand.synopsis
    assert "Tails" in cand.synopsis
    assert "box-2D(eu)" in (cand.poster_url or "")
    assert "screenmarquee(wor)" in (cand.marquee_url or "")
    assert "video-normalized" in (cand.video_url or "")
    # Non-game parent media (editeur / famille / genre) must never leak into candidate media URLs
    all_media = [cand.poster_url or "", cand.marquee_url or "", cand.video_url or ""] + list(cand.snapshot_urls)
    assert not any("figurine" in u or "pictomonochrome" in u for u in all_media)

    # Mismatched genre or decade in criteria must reject the candidate
    rejected_genre = adapter._parse_game_node(
        jeu=jeu_node,
        sys_key="megadrive",
        lang="fr",
        criteria=FilterCriteria(domain="games", systems=["megadrive"], genres=["fighting"], lang="fr"),
    )
    assert rejected_genre is None

    rejected_decade = adapter._parse_game_node(
        jeu=jeu_node,
        sys_key="megadrive",
        lang="fr",
        criteria=FilterCriteria(domain="games", systems=["megadrive"], decades=["1980s"], lang="fr"),
    )
    assert rejected_decade is None


@pytest.mark.asyncio
async def test_cps2_fighting_ssf2t_catalog_and_mvsc2_naomi_isolation(tmp_path: Path, monkeypatch):
    """
    Verify:
    1. `cps2` + `fighting` in `ArcadeItaliaReferentialStrategy` includes `ssf2t` / `ssf2`
       (Super Street Fighter II Turbo / The New Challengers) and rejects `mvsc2` (Sega Naomi).
    2. `ArcadeItaliaAdapter` rejects `mvsc2` rows on `cps2` and marks skipped/clone ROMs in `seen_ids`
       so `unseen_sg` never stalls.
    3. `resolve_canonical_genre` maps `"Platform / Shooter Scrolling"` -> `"runandgun"` and
       `"Shooter / Flying"` -> `"shootemup"`, never `"platform"`.
    """
    from app.domain.taxonomy import resolve_canonical_genre
    from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter
    from app.infrastructure.scrapers.referential_store import ArcadeItaliaReferentialStrategy

    assert resolve_canonical_genre("games", "Platform / Shooter Scrolling") == "runandgun"
    assert resolve_canonical_genre("games", "Shooter / Flying") == "shootemup"
    assert resolve_canonical_genre("games", "Platform / Fighter Scrolling") == "action"
    assert resolve_canonical_genre("games", "Platform / Run Jump") == "platform"

    assert ArcadeItaliaReferentialStrategy.is_rom_allowed_for_system("ssf2t", "cps2") is True
    assert ArcadeItaliaReferentialStrategy.is_rom_allowed_for_system("ssf2", "cps2") is True
    assert ArcadeItaliaReferentialStrategy.is_rom_allowed_for_system("mvsc2", "cps2") is False

    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "cps2_ref_data"))
    strategy = ArcadeItaliaReferentialStrategy()
    adapter = ArcadeItaliaAdapter(referential_strategy=strategy)

    # Verify `mvsc2` row (Sega Naomi hardware) is rejected when `sys_key == "cps2"`
    naomi_row = {
        "game_name": "mvsc2",
        "title": "Marvel vs. Capcom 2: New Age of Heroes",
        "sourcefile": "sega/naomi.cpp",
        "genre": "Fighter / Versus",
        "year": "2000",
    }
    assert adapter._is_row_valid_for_system(naomi_row, "cps2") is False

    cps2_row = {
        "game_name": "ssf2t",
        "title": "Super Street Fighter II Turbo (World 940223)",
        "sourcefile": "capcom/cps2.cpp",
        "genre": "Fighter / Versus",
        "year": "1994",
    }
    assert adapter._is_row_valid_for_system(cps2_row, "cps2") is True


def test_resolve_framing_and_color_params_orthogonal_matrix():
    """Verify `resolve_framing_and_color_params` across all `video_mode` and `color_boost` combinations."""
    from app.scheduler.asset_builder import resolve_framing_and_color_params

    # 1. Default auto_action + auto color boost
    assert resolve_framing_and_color_params("auto_action", "auto") == (True, False, True, "pixel_art")
    # 2. Panorama + anime preset
    assert resolve_framing_and_color_params("panorama", "anime") == (False, True, False, "anime")
    # 3. Center crop + cinema preset
    assert resolve_framing_and_color_params("center_crop", "cinema") == (False, False, False, "cinema")
    # 4. Auto action + fixed pixel_art preset
    assert resolve_framing_and_color_params("auto_action", "pixel_art") == (True, False, False, "pixel_art")
    # 5. Disabled color boost (boolean False or "off")
    assert resolve_framing_and_color_params("auto_action", False) == (True, False, False, "cinema")


def test_three_tier_credential_resolution_and_public_config_sanitization(tmp_path: Path, monkeypatch):
    """
    Verify:
    1. 3-Tier priority: `server_config.json` (WebUI) > `.env`/`docker-compose` > `_prod_vault`.
    2. `get_public_admin_config()` never leaks `screenscraper_dev_id`, `screenscraper_dev_pass`,
       or built-in `tmdb_api_key`/`tvdb_api_key`, and masks user-saved secrets (`••••••••`).
    """
    import sys
    from app.config import settings
    from app.infrastructure.persistence.server_config_store import ServerConfigStore

    scs_mod = sys.modules["app.infrastructure.persistence.server_config_store"]

    # Simulate Tier 3 (_prod_vault) providing dev keys + default TMDB key
    fake_vault = {
        "tmdb_api_key": "vault_tmdb_secret",
        "tvdb_api_key": "vault_tvdb_secret",
        "screenscraper_dev_id": "vault_dev_id",
        "screenscraper_dev_pass": "vault_dev_pass",
    }
    monkeypatch.setattr(scs_mod, "decrypt_prod_vault_secret", lambda k: fake_vault.get(k, ""))
    monkeypatch.setattr(scs_mod, "has_prod_vault_secret", lambda k: k in fake_vault)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TVDB_API_KEY", raising=False)
    monkeypatch.delenv("SCREENSCRAPER_DEV_ID", raising=False)
    monkeypatch.delenv("SCREENSCRAPER_DEV_PASS", raising=False)
    monkeypatch.setattr(settings, "tmdb_api_key", "")
    monkeypatch.setattr(settings, "tvdb_api_key", "")
    monkeypatch.setattr(settings, "screenscraper_dev_id", "")
    monkeypatch.setattr(settings, "screenscraper_dev_pass", "")

    store = ServerConfigStore(file_path=tmp_path / "server_config.json")

    # Tier 3 active when neither .env nor server_config.json overrides exist
    eff = store.get_all()
    assert eff["tmdb_api_key"] == "vault_tmdb_secret"
    assert eff["screenscraper_dev_id"] == "vault_dev_id"
    assert eff["screenscraper_dev_pass"] == "vault_dev_pass"

    # Public config must never expose vault or dev keys
    pub = store.get_public_admin_config()
    assert "screenscraper_dev_id" not in pub
    assert "screenscraper_dev_pass" not in pub
    assert pub["tmdb_api_key"] == ""
    assert pub["tvdb_api_key"] == ""

    # Tier 2 (.env / docker-compose) overrides Tier 3 (_prod_vault)
    monkeypatch.setenv("TMDB_API_KEY", "compose_env_tmdb_key")
    assert store.get_all()["tmdb_api_key"] == "compose_env_tmdb_key"
    assert store.get_public_admin_config()["tmdb_api_key"] == ""

    # Tier 1 (WebUI server_config.json) overrides Tier 2 and is masked in public config
    store.update({"tmdb_api_key": "user_custom_tmdb_key", "screenscraper_user": "player1", "screenscraper_pass": "p4ss"})
    assert store.get_all()["tmdb_api_key"] == "user_custom_tmdb_key"
    pub_after = store.get_public_admin_config()
    assert pub_after["tmdb_api_key"] == "••••••••"
    assert pub_after["screenscraper_pass"] == "••••••••"

    # Submitting masked placeholder `••••••••` preserves existing user secret
    store.update({"tmdb_api_key": "••••••••", "screenscraper_pass": "••••••••"})
    assert store.get_all()["tmdb_api_key"] == "user_custom_tmdb_key"
    assert store.get_all()["screenscraper_pass"] == "p4ss"


def test_compile_prod_vault_script_end_to_end(monkeypatch):
    """
    Exercise `scripts/compile_prod_vault.py` end-to-end with dummy secrets to guarantee
    C extension generation, compilation, self-test roundtrip, and summary logging never regress.
    Cleans up any generated `_prod_vault*` binary afterwards.
    """
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    security_dir = repo_root / "app" / "security"
    script_path = repo_root / "scripts" / "compile_prod_vault.py"

    for old in security_dir.glob("_prod_vault*"):
        old.unlink(missing_ok=True)

    env = dict(os.environ)
    env["VAULT_MASTER_SECRET"] = "unit_test_master_salt_987654321"
    env["TMDB_API_KEY"] = "unit_test_tmdb_key_abcdef"
    env["TVDB_API_KEY"] = "unit_test_tvdb_key_123456"

    try:
        res = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"compile_prod_vault failed: {res.stderr}\n{res.stdout}"
        assert "[PROD-VAULT] Compiled native binary" in res.stdout
        assert "self-test OK" in res.stdout
        built = list(security_dir.glob("_prod_vault*"))
        assert len(built) >= 1
    finally:
        for f in security_dir.glob("_prod_vault*"):
            f.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_four_showcase_engines_rotation_preserves_prep_and_delivers_retained_ready(tmp_path: Path, monkeypatch):
    """
    Simulate 4 Showcase engines (`series`, `games`, `movies`, `anime`) rotating rapidly on the
    same LAN IP (`192.168.1.177`):
    1. Initial registration publishes retained `ready: false` (`preparing`) and starts background prep.
    2. Rotating to the next engine on `192.168.1.177` + `client_offline` stops UDP streams ONLY,
       never cancels sibling `_prep_tasks`.
    3. All 4 domains complete Slot N (`ready: true` with `retain=True`) before Slot N+1 (`standby`),
       and reconnecting clients immediately receive the retained `ready: true` payload on SUBSCRIBE!
    """
    import json
    from app.config import settings
    from app.domain.entities import MediaCandidate
    from app.infrastructure.mqtt.embedded_broker import EmbeddedMqttBroker, SimpleAsyncMqttClient
    from app.infrastructure.mqtt.showcase_mqtt_gateway import ShowcaseMqttGateway
    from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository

    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")

    broker = EmbeddedMqttBroker(host="127.0.0.1", port=0)

    gw = ShowcaseMqttGateway()
    gw.embedded_broker = broker
    gw._running = True
    broker.subscribe_internal("dmd_showcase/clients/+/config", gw._on_raw_msg)
    broker.subscribe_internal("dmd_showcase/clients/+/status", gw._on_raw_msg)
    broker.subscribe_internal("dmd_showcase/clients/+/+/events", gw._on_raw_msg)

    slot_order: list[tuple[str, int]] = []

    class _FakeChain:
        def __init__(self) -> None:
            self.seq = 0

        async def acquire_unseen_candidates(self, criteria, count=1, extra_exclude_ids=None):
            self.seq += 1
            cid = f"{criteria.domain}_cand_{self.seq}"
            return [
                MediaCandidate(
                    id=cid,
                    domain=criteria.domain,
                    title=f"Title {criteria.domain} #{self.seq}",
                    year="1995",
                    badge=criteria.domain.upper(),
                    badge_color="#00D2FF",
                    subtitle="Action",
                    rating="8.5",
                    synopsis="Test synopsis",
                    source_id="mock_src",
                )
            ]

    class _SlowTranscoder:
        async def transcode_candidate(self, cand, criteria, geometry, item_dir: Path):
            await asyncio.sleep(0.12)
            item_dir.mkdir(parents=True, exist_ok=True)
            stream_p = item_dir / "unified.rgb565.stream"
            stream_p.write_bytes(b"DMDS" + b"\x00" * 28)
            is_slot_n = (f"rpi_{criteria.domain}_128x32", criteria.domain) not in orch.ready_packages
            slot_order.append((criteria.domain, 1 if is_slot_n else 2))
            return {
                "poster_rgb565_b64": "AA==",
                "has_panoramas": True,
                "has_video": True,
                "unified_stream_path": str(stream_p),
                "stream_frames": 30,
                "stream_fps": 15,
                "stream_duration_s": 2.0,
            }

    stopped_udp: list[str] = []

    class _FakeStreamer:
        async def stop_stream(self, dmd_id: str):
            stopped_udp.append(dmd_id)

        async def start_udp_clock_stream(self, **_kwargs):
            pass

    seen = JsonSeenHistoryRepository(file_path=tmp_path / "seen.json")
    orch = ConsumeOnceOrchestrator(
        chain_service=_FakeChain(),  # type: ignore[arg-type]
        seen_repo=seen,
        transcoder=_SlowTranscoder(),  # type: ignore[arg-type]
        streamer=_FakeStreamer(),  # type: ignore[arg-type]
        mqtt_gateway=gw,
    )

    try:
        domains = ("series", "games", "movies", "anime")
        # Pass 1: All 4 engines connect rapidly on 192.168.1.177, see `ready: false`, and rotate out cleanly
        for idx, dom in enumerate(domains):
            dmd_id = f"rpi_{dom}_128x32"
            cli = SimpleAsyncMqttClient(
                "127.0.0.1",
                0,
                f"{dmd_id}_1",
                will_topic=f"dmd_showcase/clients/{dmd_id}/status",
                will_payload=b'{"online":false}',
                will_retain=False,
            )
            await _connect_mqtt_client_to_broker(broker, cli)
            got_msgs: list[dict] = []

            async def _on_ready(_t: str, payload: bytes, _r: bool, bucket=got_msgs) -> None:
                bucket.append(json.loads(payload.decode("utf-8")))

            await cli.publish(
                f"dmd_showcase/clients/{dmd_id}/config",
                json.dumps(
                    {
                        "dmd_id": dmd_id,
                        "client_ip": "192.168.1.177",
                        "udp_ip": "192.168.1.177",
                        "width": 128,
                        "height": 32,
                        "udp_port": 40000 + idx,
                        "lang": "fr",
                        "domains": {dom: {"lang": "fr"}},
                    }
                ).encode("utf-8"),
                retain=True,
            )
            await asyncio.sleep(0.02)
            await cli.subscribe(f"dmd_showcase/clients/{dmd_id}/{dom}/ready", _on_ready)
            await asyncio.sleep(0.02)
            assert len(got_msgs) >= 1
            assert got_msgs[0]["ready"] is False
            # Clean disconnect (simulating rotation switch) -> must NOT fire LWT or cancel prep!
            await cli.disconnect(graceful=True)

        # Wait ~0.6s for background preparation of all 4 domains to finish
        for _ in range(40):
            if all((f"rpi_{d}_128x32", d) in orch.ready_packages for d in domains):
                break
            await asyncio.sleep(0.05)

        for dom in domains:
            assert (f"rpi_{dom}_128x32", dom) in orch.ready_packages, f"{dom} Slot N was cancelled!"

        # Fairness check: the first 4 completed transcodes must be Slot N (1) for each of the 4 domains!
        first_four_slots = [slot_num for (_dom, slot_num) in slot_order[:4]]
        assert first_four_slots == [1, 1, 1, 1], f"Expected all 4 Slot N before Slot N+1, got {slot_order}"

        # Pass 2: Reconnect each engine on its next rotation turn -> retained `ready: true` delivered immediately!
        for dom in domains:
            dmd_id = f"rpi_{dom}_128x32"
            cli2 = SimpleAsyncMqttClient("127.0.0.1", 0, f"{dmd_id}_2")
            await _connect_mqtt_client_to_broker(broker, cli2)
            retained_ready: list[dict] = []

            async def _on_ready2(_t: str, payload: bytes, _r: bool, bucket=retained_ready) -> None:
                bucket.append(json.loads(payload.decode("utf-8")))

            await cli2.subscribe(f"dmd_showcase/clients/{dmd_id}/{dom}/ready", _on_ready2)
            await asyncio.sleep(0.03)
            assert len(retained_ready) == 1
            assert retained_ready[0]["ready"] is True
            assert retained_ready[0]["domain"] == dom
            await cli2.disconnect(graceful=True)
    finally:
        for task in list(orch._prep_tasks.values()):
            task.cancel()
        await broker.stop()








