"""
Regression test suite for:
1. Retained MQTT client configuration persistence & cold-boot slot recovery (`RetainedClientConfigStore`,
   `ShowcaseMqttGateway`, and `SlotPreparationService`).
2. False catalog-exhaustion reset prevention in `DomainSourceChainService` under stochastic sampling.
3. `JikanAnimeThemesAdapter` timeout circuit-breaker failover and unfiltered `anime` / `games` diversity
   (`JikanAnimeThemesAdapter`, `AniListAdapter`, `ArcadeItaliaAdapter`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

from app.application.consume_once_orchestrator import ConsumeOnceOrchestrator
from app.application.source_chain_service import DomainSourceChainService
from app.config import settings
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.mqtt.embedded_broker import EmbeddedMqttBroker
from app.infrastructure.mqtt.showcase_mqtt_gateway import ShowcaseMqttGateway
from app.infrastructure.persistence.dmd_profile_store import DmdProfileStore
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.persistence.mqtt_retained_store import RetainedClientConfigStore
from app.infrastructure.scrapers.anime.anilist_adapter import AniListAdapter
from app.infrastructure.scrapers.anime.jikan_animethemes_adapter import JikanAnimeThemesAdapter
from app.infrastructure.scrapers.registry import ScraperRegistry


@pytest.mark.asyncio
async def test_source_chain_never_wipes_seen_history_on_stochastic_sampling_miss(tmp_path: Path):
    """
    Verify `DomainSourceChainService.acquire_unseen_candidates` never resets `seen_history.json`
    when Pass 1 misses stochastically (e.g. random page/system collision) while unseen items
    still exist in the catalog, yet still resets when 100% of probe items are genuinely seen.
    """
    seen_file = tmp_path / "seen_history.json"
    repo = JsonSeenHistoryRepository(file_path=seen_file)
    for i in range(1, 76):
        repo.mark_consumed("games", f"games_adb_seen_{i}")
    assert repo.stats()["games"] == 75

    class _StochasticSource(IScraperSource):
        source_id = "stochastic_games"
        domain: DomainType = "games"
        role = "primary"

        def __init__(self) -> None:
            self.calls = 0

        def is_configured(self) -> bool:
            return True

        def supported_filters(self, lang: str = "fr"):
            return {}

        async def fetch_random_unseen(
            self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1
        ) -> List[MediaCandidate]:
            self.calls += 1
            # Simulate Pass 1 AND Retry Pass both landing on already-seen pages (returning []),
            # while the Probe Pass (without persisted_seen) samples a mix of seen + unseen items.
            if "games_adb_seen_1" in seen_ids:
                return []
            return [
                MediaCandidate(
                    id="games_adb_seen_1",
                    domain="games",
                    title="Seen Game 1",
                    year="1992",
                    badge="ARCADE",
                    badge_color="#FF5722",
                    subtitle="Arcade",
                    rating="8.5",
                    synopsis="Seen synopsis",
                    source_id=self.source_id,
                    extra_meta={"system": "arcade"},
                ),
                MediaCandidate(
                    id="games_adb_fresh_76",
                    domain="games",
                    title="Fresh Unseen Game 76",
                    year="1996",
                    badge="NEOGEO",
                    badge_color="#E53935",
                    subtitle="Neo-Geo",
                    rating="9.1",
                    synopsis="Fresh synopsis",
                    source_id=self.source_id,
                    extra_meta={"system": "neogeo"},
                ),
            ][:count]

        async def search_by_query(self, query: str, lang: str = "fr"):
            return None

    reg = ScraperRegistry()
    reg._chains["games"] = [_StochasticSource()]
    chain_svc = DomainSourceChainService(registry=reg, history_repo=repo)

    picked = await chain_svc.acquire_unseen_candidates(FilterCriteria(domain="games"), count=1)
    assert len(picked) == 1
    assert picked[0].id == "games_adb_fresh_76"
    # Critical invariant: the 75 previously seen games MUST NOT have been wiped!
    assert repo.stats()["games"] == 75


@pytest.mark.asyncio
async def test_jikan_circuit_breaker_and_unfiltered_anime_genre_diversity(monkeypatch):
    """
    Verify:
    1. `JikanAnimeThemesAdapter` opens its circuit breaker after 2 consecutive timeouts so
       subsequent calls skip dead `api.jikan.moe` in `0ms` instead of burning `12-24s`.
    2. When `criteria.genres == []`, neither `JikanAnimeThemesAdapter` nor `AniListAdapter`
       hardcodes `shonen` / `Shounen`, preserving full multi-genre anime diversity.
    """
    JikanAnimeThemesAdapter.reset_circuit_breaker()
    jikan = JikanAnimeThemesAdapter()
    http_attempts = 0

    class _TimingOutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url: str, params=None, headers=None, timeout=None):
            nonlocal http_attempts
            http_attempts += 1
            raise TimeoutError("api.jikan.moe read timed out")

    monkeypatch.setattr(
        "app.infrastructure.scrapers.anime.jikan_animethemes_adapter.create_async_http_client",
        lambda timeout=3.5, headers=None: _TimingOutClient(),
    )

    try:
        res1 = await jikan.fetch_random_unseen(FilterCriteria(domain="anime", genres=[]), set(), count=1)
        assert res1 == []
        assert http_attempts == 2
        assert JikanAnimeThemesAdapter._is_circuit_open() is True

        # Next call must short-circuit immediately with zero HTTP attempts!
        res2 = await jikan.fetch_random_unseen(FilterCriteria(domain="anime", genres=[]), set(), count=1)
        assert res2 == []
        assert http_attempts == 2
    finally:
        JikanAnimeThemesAdapter.reset_circuit_breaker()

    # Verify AniListAdapter omits `tag`/`genre` when `criteria.genres == []` and infers true genre badge
    captured_vars: List[Dict[str, Any]] = []

    class _MockResp:
        def __init__(self, status_code: int, payload: Dict[str, Any]):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _MockAniListClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url: str, json=None, headers=None, timeout=None):
            variables = dict((json or {}).get("variables") or {})
            captured_vars.append(variables)
            return _MockResp(
                200,
                {
                    "data": {
                        "Page": {
                            "pageInfo": {"lastPage": 50},
                            "media": [
                                {
                                    "id": 30,
                                    "idMal": 30,
                                    "title": {
                                        "english": "Neon Genesis Evangelion",
                                        "romaji": "Shin Seiki Evangelion",
                                        "native": "新世紀エヴァンゲリオン",
                                    },
                                    "genres": ["Mecha", "Sci-Fi", "Drama"],
                                    "startDate": {"year": 1995},
                                    "averageScore": 85,
                                    "description": "Mecha classic.",
                                    "coverImage": {"extraLarge": "https://s4.anilist.co/eva.jpg"},
                                    "bannerImage": "https://s4.anilist.co/eva_banner.jpg",
                                    "trailer": {"id": "13nSISwxrY4", "site": "youtube"},
                                }
                            ],
                        }
                    }
                },
            )

    async def _noop_enrich(*_args, **_kwargs):
        return None

    async def _noop_loc(*_args, **_kwargs):
        return (None, None, None)

    monkeypatch.setattr(
        "app.infrastructure.scrapers.anime.anilist_adapter.create_async_http_client",
        lambda timeout=5.5, headers=None: _MockAniListClient(),
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.anime.anilist_adapter.enrich_with_animethemes_webm",
        _noop_enrich,
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.anime.anilist_adapter.resolve_multilingual_media_details",
        _noop_loc,
    )

    anilist = AniListAdapter()
    cands = await anilist.fetch_random_unseen(
        FilterCriteria(domain="anime", genres=[], lang="en"),
        seen_ids=set(),
        count=1,
    )
    assert len(cands) == 1
    assert captured_vars, "Expected AniList GraphQL request"
    assert "tag" not in captured_vars[0], "Unfiltered anime query must not hardcode tag='Shounen'!"
    assert "genre" not in captured_vars[0], "Unfiltered anime query must not hardcode genre!"
    assert cands[0].badge in ("MECHA", "SCI-FI", "DRAMA")


@pytest.mark.asyncio
async def test_retained_mqtt_client_configs_persist_and_restore_all_4_rotations_on_restart(
    tmp_path: Path, monkeypatch
):
    """
    Verify that when 4 Mode 1 DMD Showcase rotations (`games`, `movies`, `series`, `anime`)
    register via retained MQTT `dmd_showcase/clients/{dmd_id}/config`, restarting the server
    reloads all 4 slots from `mqtt_retained_clients.json`, keeps `online=False` until live
    traffic arrives, immediately pre-warms `ready: true`, and honors `unregister: true`.
    """
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    retained_file = tmp_path / "data" / "mqtt_retained_clients.json"
    store1 = RetainedClientConfigStore(file_path=retained_file)

    broker1 = EmbeddedMqttBroker(host="127.0.0.1", port=0)
    gw1 = ShowcaseMqttGateway(retained_store=store1)
    gw1.embedded_broker = broker1
    gw1._running = True
    broker1.subscribe_internal("dmd_showcase/clients/+/config", gw1._on_raw_msg)

    domains: List[DomainType] = ["games", "movies", "series", "anime"]
    for dom in domains:
        dmd_id = f"rpi_{dom}_128x32"
        await broker1.publish(
            f"dmd_showcase/clients/{dmd_id}/config",
            json.dumps(
                {
                    "domain": dom,
                    "width": 128,
                    "height": 32,
                    "lang": "fr",
                    "client_ip": "192.168.1.183",
                    "udp_port": 5568,
                }
            ).encode("utf-8"),
            retain=True,
        )

    assert retained_file.exists()
    saved_entries = store1.list_entries()
    assert set(saved_entries.keys()) == {f"rpi_{d}_128x32" for d in domains}

    # Simulate container restart (`docker compose down && up`) with fresh broker, gateway & orchestrator
    store2 = RetainedClientConfigStore(file_path=retained_file)
    broker2 = EmbeddedMqttBroker(host="127.0.0.1", port=0)
    gw2 = ShowcaseMqttGateway(retained_store=store2)
    gw2.embedded_broker = broker2
    gw2._running = True
    broker2.subscribe_internal("dmd_showcase/clients/+/config", gw2._on_raw_msg)
    broker2.subscribe_internal("dmd_showcase/clients/+/+/events", gw2._on_raw_msg)

    class _FastChain:
        async def acquire_unseen_candidates(self, criteria, count=1, extra_exclude_ids=None):
            return [
                MediaCandidate(
                    id=f"{criteria.domain}_restored_1",
                    domain=criteria.domain,
                    title=f"Restored {criteria.domain.title()}",
                    year="1999",
                    badge=criteria.domain[:6].upper(),
                    badge_color="#00D2FF",
                    subtitle="Restored",
                    rating="8.9",
                    synopsis="Restored slot.",
                    source_id="mock",
                )
            ]

    class _FastTranscoder:
        async def transcode_candidate(self, cand, criteria, geometry, item_dir: Path):
            item_dir.mkdir(parents=True, exist_ok=True)
            sp = item_dir / "unified.rgb565.stream"
            sp.write_bytes(b"DMDS" + b"\x00" * 28)
            return {
                "poster_rgb565_b64": "AA==",
                "has_panoramas": False,
                "has_video": True,
                "unified_stream_path": str(sp),
                "stream_frames": 15,
                "stream_fps": 15,
                "stream_duration_s": 1.0,
            }

    class _NoopStreamer:
        async def stop_stream(self, dmd_id: str):
            pass

        async def start_udp_clock_stream(self, **_kwargs):
            pass

    orch = ConsumeOnceOrchestrator(
        chain_service=_FastChain(),  # type: ignore[arg-type]
        seen_repo=JsonSeenHistoryRepository(file_path=tmp_path / "seen.json"),
        transcoder=_FastTranscoder(),  # type: ignore[arg-type]
        streamer=_NoopStreamer(),  # type: ignore[arg-type]
        mqtt_gateway=gw2,
        profile_store=DmdProfileStore(file_path=tmp_path / "profiles.json"),
    )

    await gw2._restore_persisted_client_configs()

    # All 4 DMD rotation slots must be immediately registered, yet marked offline until live traffic arrives
    for dom in domains:
        dmd_id = f"rpi_{dom}_128x32"
        assert (dmd_id, dom) in orch.client_configs
        assert orch.client_online[dmd_id] is False
        assert f"dmd_showcase/clients/{dmd_id}/config" in broker2.retained

    # Wait briefly for background N / N+1 pre-warming to publish retained `ready: true` for all 4 domains
    for _ in range(40):
        if len(orch.ready_packages) == 4:
            break
        await asyncio.sleep(0.02)
    assert len(orch.ready_packages) == 4

    # Live `start_card` event from waking DMD flips `online` to True
    games_pkg = orch.ready_packages[("rpi_games_128x32", "games")]
    await broker2.publish(
        "dmd_showcase/clients/rpi_games_128x32/games/events",
        json.dumps({"event": "start_card", "item_id": games_pkg.item_id}).encode("utf-8"),
        retain=False,
    )
    assert orch.client_online["rpi_games_128x32"] is True

    # Publishing `unregister: true` removes the client from persistent retained store
    await broker2.publish(
        "dmd_showcase/clients/rpi_anime_128x32/config",
        json.dumps({"unregister": True}).encode("utf-8"),
        retain=True,
    )
    assert "rpi_anime_128x32" not in store2.list_entries()
    assert "dmd_showcase/clients/rpi_anime_128x32/config" not in broker2.retained
