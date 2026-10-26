"""
Tests for Async On-Demand Queue Preparation, Request-Keyed Persistent Conversion Cache,
and MQTT Broker Endpoint Discovery (`GET /api/v1/health`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import struct
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient
import pytest

from app.application.consume_once_orchestrator import ConsumeOnceOrchestrator
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
)
from app.domain.on_demand_cache_key import build_on_demand_request_cache_key
from app.domain.ports import (
    IFrameStreamerPort,
    IMediaTranscoderPort,
    IMqttControlGatewayPort,
)
from app.infrastructure.persistence.dmd_profile_store import DmdProfileStore
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.persistence.on_demand_cache_store import FileOnDemandCacheStore
from app.infrastructure.persistence.server_config_store import ServerConfigStore
from app.models import QueueAddRequest


class _RecordingMqttGateway(IMqttControlGatewayPort):
    def __init__(self) -> None:
        self.published: List[Tuple[str, Dict[str, Any], bool]] = []

    def set_handlers(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def publish_json(
        self, topic: str, data: Dict[str, Any], retain: bool = False
    ) -> None:
        self.published.append((topic, data, retain))


class _NoopStreamer(IFrameStreamerPort):
    active_streams_count: int = 0

    async def start_udp_clock_stream(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def stop_stream(self, dmd_id: str) -> None:
        return None


class _FakeTranscoder(IMediaTranscoderPort):
    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.calls: int = 0

    async def transcode_candidate(
        self,
        cand: MediaCandidate,
        criteria: FilterCriteria,
        geometry: PanelGeometry,
        out_dir: Path,
    ) -> Dict[str, object]:
        self.calls += 1
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        out_dir.mkdir(parents=True, exist_ok=True)
        w, h = geometry.width, geometry.height
        header = struct.pack("<4sBBHHHHH", b"DMDS", 1, 15, w, h, 2, 66, 0)
        pixels = b"\x1f\x00" * (w * h * 2)
        stream_path = out_dir / f"unified_{w}x{h}.rgb565.stream"
        stream_path.write_bytes(header + pixels)
        poster_path = out_dir / "poster.rgb565"
        poster_path.write_bytes(b"\x00\x00" * (32 * 32))
        return {
            "poster_rgb565": "poster.rgb565",
            "poster_width": 32,
            "poster_height": 32,
            "unified_stream_path": str(stream_path),
            "stream_frames": 2,
            "stream_fps": 15,
            "stream_duration_s": 0.13,
            "has_panoramas": True,
            "has_video": True,
        }


class _FakeChainService:
    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.search_calls: int = 0

    def get_staged_candidate(self, candidate_id: str) -> None:
        return None

    async def search_candidates(
        self, criteria: FilterCriteria, query: str, limit: int = 1
    ) -> List[MediaCandidate]:
        self.search_calls += 1
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        if query.strip().lower() == "unknown_rom_xyz":
            return []
        return [
            MediaCandidate(
                id="games_adb_mslugx",
                domain="games",
                title="Metal Slug X - Super Vehicle-001",
                year="1999",
                badge="NEO-GEO",
                badge_color="#E53935",
                subtitle="SNK • Run & Gun",
                rating="9.4",
                synopsis="Classic Neo-Geo run-and-gun action.",
                lang=criteria.lang,
            )
        ]

    async def search_on_demand(
        self, domain: DomainType, query: str, lang: str = "fr"
    ) -> None:
        return None


def test_request_cache_key_normalizes_rom_extension_and_system_synonyms():
    geom = PanelGeometry(width=128, height=32)
    crit = FilterCriteria(domain="games", systems=["neogeo"], genres=[], decades=[], lang="fr")
    k1 = build_on_demand_request_cache_key("games", ["neogeo"], "mslugx", geom, crit)
    k2 = build_on_demand_request_cache_key("games", ["Neo-Geo"], "/roms/neogeo/mslugx.zip", geom, crit)
    assert k1.cache_key == k2.cache_key
    assert k1.render_signature == k2.render_signature

    crit_cyber = FilterCriteria(
        domain="games",
        systems=["neogeo"],
        genres=[],
        decades=[],
        lang="fr",
        card_layout="cyber_grid",
    )
    k3 = build_on_demand_request_cache_key("games", ["neogeo"], "mslugx", geom, crit_cyber)
    assert k3.render_signature != k1.render_signature
    assert k3.cache_key != k1.cache_key


@pytest.mark.asyncio
async def test_async_queue_cache_hit_skips_scraper_and_transcoder_and_evicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "cache_dir", tmp_path / "ephemeral_cache")
    cfg_store = ServerConfigStore(file_path=tmp_path / "server_config.json")
    cache_store = FileOnDemandCacheStore(
        cache_root=tmp_path / "on_demand_cache",
        config_store=cfg_store,
    )
    cache_store.update_settings(enabled=True, max_mb=200)

    chain = _FakeChainService(delay_s=0.05)
    transcoder = _FakeTranscoder(delay_s=0.05)
    mqtt = _RecordingMqttGateway()

    orch = ConsumeOnceOrchestrator(
        chain_service=chain,  # type: ignore[arg-type]
        seen_repo=JsonSeenHistoryRepository(file_path=tmp_path / "seen.json"),
        transcoder=transcoder,
        streamer=_NoopStreamer(),
        mqtt_gateway=mqtt,
        profile_store=DmdProfileStore(file_path=tmp_path / "profiles.json"),
        on_demand_cache=cache_store,
    )

    # 1. First request (cache miss + async_prepare=True): returns immediately in 'preparing'
    req1 = QueueAddRequest(
        domain="games",
        systems=["neogeo"],
        query="mslugx",
        mode="play_now",
        mark_seen=False,
        async_prepare=True,
        render_card_in_stream=True,
        width=128,
        height=32,
    )
    item1 = await orch.enqueue_on_demand("dmd_arcade_sync", req1)
    assert item1["status"] == "preparing"
    assert item1["cache_hit"] is False
    assert chain.search_calls == 0  # Not blocked synchronously!

    # Wait for background preparation to finish
    q_entries = orch.state.priority_queues["dmd_arcade_sync"]
    assert len(q_entries) == 1
    assert q_entries[0].prep_task is not None
    await q_entries[0].prep_task

    assert chain.search_calls == 1
    assert transcoder.calls == 1
    assert q_entries[0].status == "ready"
    cmd_msgs = [p for p in mqtt.published if p[0].endswith("/command")]
    assert len(cmd_msgs) == 1
    assert cmd_msgs[0][1]["action"] == "play_now"
    assert cmd_msgs[0][1]["queue_id"] == item1["queue_id"]

    # Clean up first queue item (as when exiting game)
    removed = await orch.remove_queue_item("dmd_arcade_sync", item1["queue_id"])
    assert removed is True

    # 2. Second request for the same (system + game) with .zip extension -> MUST hit cache in 0 ms
    #    with ZERO scraper call and ZERO transcode call!
    async def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Scraper/Transcoder must NOT be called on request cache hit!")

    monkeypatch.setattr(chain, "search_candidates", _fail_if_called)
    monkeypatch.setattr(transcoder, "transcode_candidate", _fail_if_called)

    req2 = QueueAddRequest(
        domain="games",
        systems=["neogeo"],
        query="mslugx.zip",
        mode="play_now",
        mark_seen=False,
        async_prepare=True,
        render_card_in_stream=True,
        width=128,
        height=32,
    )
    item2 = await orch.enqueue_on_demand("dmd_arcade_sync", req2)
    assert item2["status"] == "ready"
    assert item2["cache_hit"] is True
    assert item2["title"] == "Metal Slug X - Super Vehicle-001"
    assert cache_store.get_stats()["entry_count"] == 1
    assert cache_store.get_stats()["items"][0]["hits"] == 1

    # 3. Evict cache -> frees all cached entries
    evicted = cache_store.evict_all()
    assert evicted["evicted_count"] == 1
    assert evicted["freed_bytes"] > 0
    assert cache_store.get_stats()["entry_count"] == 0


@pytest.mark.asyncio
async def test_async_queue_cancellation_aborts_before_publish(tmp_path: Path):
    chain = _FakeChainService(delay_s=0.5)
    transcoder = _FakeTranscoder(delay_s=0.5)
    mqtt = _RecordingMqttGateway()

    orch = ConsumeOnceOrchestrator(
        chain_service=chain,  # type: ignore[arg-type]
        seen_repo=JsonSeenHistoryRepository(file_path=tmp_path / "seen.json"),
        transcoder=transcoder,
        streamer=_NoopStreamer(),
        mqtt_gateway=mqtt,
        profile_store=DmdProfileStore(file_path=tmp_path / "profiles.json"),
        on_demand_cache=FileOnDemandCacheStore(
            cache_root=tmp_path / "cache",
            config_store=ServerConfigStore(file_path=tmp_path / "cfg.json"),
        ),
    )

    req = QueueAddRequest(
        domain="games",
        systems=["neogeo"],
        query="mslugx",
        mode="play_now",
        mark_seen=False,
        async_prepare=True,
        width=128,
        height=32,
    )
    item = await orch.enqueue_on_demand("dmd_arcade_sync", req)
    assert item["status"] == "preparing"

    removed = await orch.remove_queue_item("dmd_arcade_sync", item["queue_id"])
    assert removed is True
    await asyncio.sleep(0.05)
    assert not [p for p in mqtt.published if p[0].endswith("/command")]


def test_http_health_mqtt_host_and_cache_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import app.main as main_mod

    cfg_store = ServerConfigStore(file_path=tmp_path / "server_config.json")
    cache_store = FileOnDemandCacheStore(
        cache_root=tmp_path / "on_demand_cache",
        config_store=cfg_store,
    )
    monkeypatch.setattr(main_mod, "server_config_store", cfg_store)
    monkeypatch.setattr(main_mod, "on_demand_cache_store", cache_store)

    with TestClient(main_mod.app) as client:
        r_health = client.get("/api/v1/health", headers={"Host": "192.168.1.50:8890"})
        assert r_health.status_code == 200
        h = r_health.json()
        assert h["mqtt_host"] == "192.168.1.50"

        cfg_store.update({"mqtt_mode": "external", "mqtt_external_url": "mqtt://192.168.1.99:1884"})
        r_health_ext = client.get("/api/v1/health", headers={"Host": "192.168.1.50:8890"})
        assert r_health_ext.status_code == 200
        h_ext = r_health_ext.json()
        assert h_ext["mqtt_mode"] == "external"
        assert h_ext["mqtt_host"] == "192.168.1.99"
        assert h_ext["mqtt_port"] == 1884

        r_cache_put = client.put(
            "/api/v1/cache/on-demand/config",
            json={"enabled": True, "max_mb": 512},
        )
        assert r_cache_put.status_code == 200
        assert r_cache_put.json()["enabled"] is True
        assert r_cache_put.json()["max_mb"] == 512

        r_evict = client.post("/api/v1/cache/on-demand/evict")
        assert r_evict.status_code == 200
        assert r_evict.json()["status"] == "evicted"
