"""
End-to-end automated tests for:
1. Multi-candidate localized & filtered catalog search (`GET/POST /api/v1/{domain}/search`) + staging cache.
2. Per-DMD On-Demand Priority Queue (`GET/POST/DELETE /api/v1/clients/{dmd_id}/queue`) supporting:
   - Exact `candidate_id` selection from `/search`
   - 1-shot `FrontendSync` queries (`{domain, query, lang, mode='play_now', mark_seen=False}`)
   - Lossless demotion of random `ready` slot to `standby` (`enqueue_next`), strict FIFO ordering, and disk cleanup.
3. Persistent DMD Profiles (`./data/dmd_profiles.json`), `hello` announcements (HTTP & MQTT),
   Mode 2B 100% Passive UDP Framebuffer Receiver (with server-rendered Info Card `DMDS` intro stream
   `build_card_stream_sync` across landscape & TATE), Mode 2A Hybrid Receiver, and live playback controls
   (`skip`, `pause`, `resume`, `stop`).
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from dmd_gif_converter import read_rgb565_stream_header, write_rgb565_stream

from app.application.consume_once_orchestrator import (
    ConsumeOnceOrchestrator,
    consume_once_orchestrator,
)
from app.application.source_chain_service import DomainSourceChainService, source_chain_service
from app.config import settings
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate, PanelGeometry
from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.mqtt.showcase_mqtt_gateway import ShowcaseMqttGateway
from app.infrastructure.persistence.dmd_profile_store import DmdProfileStore, dmd_profile_store
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository, seen_repository
from app.infrastructure.scrapers.registry import ScraperRegistry
from app.main import app
from app.scheduler.asset_builder import build_card_stream_sync


class _FakeCatalogSource(IScraperSource):
    """Deterministic multi-language, multi-domain test scraper source."""

    def __init__(self, domain: DomainType) -> None:
        self._domain = domain

    @property
    def source_id(self) -> str:
        return "screenscraper" if self._domain == "games" else "tmdb_movies"

    @property
    def domain(self) -> DomainType:
        return self._domain

    @property
    def role(self) -> str:
        return "primary"

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {"systems": [], "genres": [], "decades": []}

    @staticmethod
    def _matches_criteria(cand: MediaCandidate, criteria: FilterCriteria) -> bool:
        sys_val = str(cand.extra_meta.get("system", "")).lower()
        cand_genres = {str(g).lower() for g in cand.extra_meta.get("genres", [])}
        if criteria.systems:
            allowed_sys = {s.lower() for s in criteria.systems}
            if sys_val and sys_val not in allowed_sys:
                return False
        if criteria.genres:
            allowed_genres = {g.lower() for g in criteria.genres}
            if cand_genres and not (cand_genres & allowed_genres):
                return False
        return True

    def _catalog(self, lang: str) -> List[MediaCandidate]:
        eff_lang = (lang or "fr").lower()
        if self._domain == "games":
            syn_map = {
                "fr": "Combat 2D légendaire sur CPS-2.",
                "en": "Legendary 2D fighting classic on CPS-2.",
                "es": "Clásico legendario de lucha 2D en CPS-2.",
                "ja": "カプコン伝説の2D対戦格闘ゲーム。",
            }
            ms_syn = {
                "fr": "Run and gun explosif sur Neo-Geo.",
                "en": "Explosive run-and-gun action on Neo-Geo.",
                "es": "Acción run-and-gun explosiva en Neo-Geo.",
                "ja": "ネオジオの名作アクションシューティング。",
            }
            sor_syn = {
                "fr": "Beat 'em up culte sur Mega Drive.",
                "en": "Iconic beat 'em up on Sega Genesis / Mega Drive.",
                "es": "Beat 'em up de culto en Mega Drive.",
                "ja": "メガドライブの名作ベルトスクロールアクション。",
            }
            return [
                MediaCandidate(
                    id="games_ss_ssf2t",
                    domain="games",
                    title="Super Street Fighter II Turbo",
                    year="1994",
                    badge="CPS2",
                    badge_color="#FF6D00",
                    subtitle="Capcom • Fighting",
                    rating="9.2",
                    synopsis=syn_map.get(eff_lang, syn_map["en"]),
                    lang=eff_lang,
                    poster_url="https://example.test/ssf2t.png",
                    video_url="https://example.test/ssf2t.mp4",
                    source_id="screenscraper",
                    extra_meta={"system": "cps2", "genres": ["fighting"]},
                ),
                MediaCandidate(
                    id="games_ss_mslug",
                    domain="games",
                    title="Metal Slug",
                    year="1996",
                    badge="NEOGEO",
                    badge_color="#00E5FF",
                    subtitle="Nazca / SNK • Run & Gun",
                    rating="9.0",
                    synopsis=ms_syn.get(eff_lang, ms_syn["en"]),
                    lang=eff_lang,
                    poster_url="https://example.test/mslug.png",
                    video_url="https://example.test/mslug.mp4",
                    source_id="screenscraper",
                    extra_meta={"system": "neogeo", "genres": ["shooter", "action"]},
                ),
                MediaCandidate(
                    id="games_ss_sor2",
                    domain="games",
                    title="Streets of Rage 2",
                    year="1992",
                    badge="MEGADRIVE",
                    badge_color="#2979FF",
                    subtitle="Sega • Beat 'em up",
                    rating="9.1",
                    synopsis=sor_syn.get(eff_lang, sor_syn["en"]),
                    lang=eff_lang,
                    poster_url="https://example.test/sor2.png",
                    video_url="https://example.test/sor2.mp4",
                    source_id="screenscraper",
                    extra_meta={"system": "megadrive", "genres": ["beatemup"]},
                ),
            ]
        # Movies domain
        m_titles = {
            "fr": ("Le Cinquième Élément", "Science-fiction culte de Luc Besson."),
            "en": ("The Fifth Element", "Luc Besson's vibrant sci-fi adventure."),
            "es": ("El Quinto Elemento", "Aventura de ciencia ficción de Luc Besson."),
            "ja": ("フィフス・エレメント", "リュック・ベッソン監督のSFアクション。"),
        }
        t_val, s_val = m_titles.get(eff_lang, m_titles["en"])
        return [
            MediaCandidate(
                id="movies_tmdb_18",
                domain="movies",
                title=t_val,
                year="1997",
                badge="MOVIE",
                badge_color="#E64A19",
                subtitle="Sci-Fi • 1997",
                rating="8.1",
                synopsis=s_val,
                lang=eff_lang,
                poster_url="https://example.test/fifth.png",
                video_url="https://example.test/fifth.mp4",
                source_id="tmdb_movies",
                extra_meta={"original_title": "The Fifth Element"},
            ),
            MediaCandidate(
                id="movies_tmdb_603",
                domain="movies",
                title="The Matrix",
                year="1999",
                badge="MOVIE",
                badge_color="#00E676",
                subtitle="Sci-Fi • 1999",
                rating="8.7",
                synopsis=f"[{eff_lang}] Neo discovers the truth of the Matrix.",
                lang=eff_lang,
                poster_url="https://example.test/matrix.png",
                video_url="https://example.test/matrix.mp4",
                source_id="tmdb_movies",
            ),
        ]

    async def fetch_random_unseen(
        self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1
    ) -> List[MediaCandidate]:
        items = [
            c
            for c in self._catalog(criteria.lang)
            if c.id not in seen_ids and self._matches_criteria(c, criteria)
        ]
        return items[:count]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        q = query.strip().lower()
        for c in self._catalog(lang):
            orig = str(c.extra_meta.get("original_title", "")).lower()
            if q in c.title.lower() or q in c.id.lower() or (orig and q in orig):
                return c
        return None

    async def search_candidates(
        self, query: str, criteria: FilterCriteria, limit: int = 8
    ) -> List[MediaCandidate]:
        q = query.strip().lower()
        items = [
            c
            for c in self._catalog(criteria.lang)
            if self._matches_criteria(c, criteria)
            and (
                not q
                or q in c.title.lower()
                or q in c.id.lower()
                or q in c.subtitle.lower()
                or q in str(c.extra_meta.get("original_title", "")).lower()
            )
        ]
        return items[:limit]


class _FastStreamTranscoder(IMediaTranscoderPort):
    """Fast deterministic transcoder exercising `build_card_stream_sync` when `render_card_in_stream=True`."""

    async def transcode_candidate(
        self,
        candidate: MediaCandidate,
        criteria: FilterCriteria,
        geometry: PanelGeometry,
        out_dir: Path,
    ) -> Dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        pw, ph = geometry.resolved_poster_dims()
        poster_png = out_dir / f"poster_{pw}x{ph}.png"
        poster_arr = np.full((ph, pw, 3), (200, 80, 30), dtype=np.uint8)
        Image.fromarray(poster_arr, mode="RGB").save(poster_png, format="PNG")
        poster_rgb_bytes = b"\x1f\x00" * (pw * ph)
        (out_dir / f"poster_{pw}x{ph}.rgb565").write_bytes(poster_rgb_bytes)

        frames: List[np.ndarray] = []
        if criteria.render_card_in_stream:
            card_name = build_card_stream_sync(
                item_dir=str(out_dir),
                width=geometry.width,
                height=geometry.height,
                fps=criteria.video_fps,
                title=candidate.title,
                year=candidate.year,
                badge=candidate.badge,
                badge_color=candidate.badge_color,
                subtitle=candidate.subtitle,
                rating=candidate.rating,
                synopsis=candidate.synopsis,
                poster_png_name=poster_png.name,
                include_poster=criteria.include_poster,
                include_synopsis=criteria.include_synopsis,
                text_scroll_speed=criteria.text_scroll_speed,
            )
            raw_card = (out_dir / card_name).read_bytes()
            hdr = read_rgb565_stream_header(raw_card[:16])
            assert hdr["width"] == geometry.width
            assert hdr["height"] == geometry.height
            assert hdr["total_frames"] >= 20
        else:
            card_name = ""

        # Append 4 synthetic video frames so unified stream is fast and non-empty
        for i in range(4):
            frames.append(np.full((geometry.height, geometry.width, 3), 40 + i * 30, dtype=np.uint8))

        vid_stream = out_dir / f"video_{geometry.width}x{geometry.height}.rgb565.stream"
        write_rgb565_stream(frames, vid_stream, fps=criteria.video_fps)

        from app.infrastructure.transcoder.dmd_converter_adapter import merge_rgb565_streams

        ordered = [out_dir / card_name, vid_stream] if card_name else [vid_stream]
        unified = out_dir / f"unified_{geometry.width}x{geometry.height}.rgb565.stream"
        total_frames, total_dur = merge_rgb565_streams(
            ordered, unified, geometry.width, geometry.height, criteria.video_fps
        )
        return {
            "poster_png": poster_png.name,
            "poster_rgb565": f"poster_{pw}x{ph}.rgb565",
            "poster_rgb565_b64": base64.b64encode(poster_rgb_bytes).decode("ascii"),
            "card_stream_rgb565": card_name,
            "has_panoramas": False,
            "has_video": True,
            "stream_frames": total_frames,
            "stream_fps": criteria.video_fps,
            "stream_duration_s": total_dur,
            "unified_stream_path": str(unified),
        }


class _ControlledMockStreamer(IFrameStreamerPort):
    """Records UDP `DMDF` stream starts/stops and lets tests trigger EOF deterministically."""

    def __init__(self, auto_eof_delay_s: Optional[float] = None) -> None:
        self.started: List[Dict[str, Any]] = []
        self.active: Dict[str, Callable[[str, str, bool], Coroutine[Any, Any, None]]] = {}
        self.active_meta: Dict[str, Dict[str, Any]] = {}
        self.auto_eof_delay_s = auto_eof_delay_s

    @property
    def active_streams_count(self) -> int:
        return len(self.active)

    def get_active_stream(self, dmd_id: str) -> Optional[Dict[str, Any]]:
        return self.active_meta.get(dmd_id)

    async def start_udp_clock_stream(
        self,
        dmd_id: str,
        item_id: str,
        stream_path: Path,
        width: int,
        height: int,
        fps: int,
        udp_ip: str,
        udp_port: int = 4210,
        on_eof: Optional[Callable[[str, str, bool], Coroutine[Any, Any, None]]] = None,
    ) -> bool:
        rec = {
            "dmd_id": dmd_id,
            "item_id": item_id,
            "stream_path": stream_path,
            "width": width,
            "height": height,
            "fps": fps,
            "udp_ip": udp_ip,
            "udp_port": udp_port,
        }
        self.started.append(rec)
        self.active_meta[dmd_id] = rec
        if on_eof is not None:
            self.active[dmd_id] = on_eof
            if self.auto_eof_delay_s is not None:
                delay = self.auto_eof_delay_s

                async def _auto_finish() -> None:
                    await asyncio.sleep(delay)
                    cb = self.active.pop(dmd_id, None)
                    self.active_meta.pop(dmd_id, None)
                    if cb is not None:
                        await cb(dmd_id, item_id, True)

                asyncio.create_task(_auto_finish())
        return True

    async def stop_stream(self, dmd_id: str) -> None:
        self.active.pop(dmd_id, None)
        self.active_meta.pop(dmd_id, None)

    async def trigger_eof(self, dmd_id: str) -> None:
        meta = self.active_meta.pop(dmd_id, None)
        cb = self.active.pop(dmd_id, None)
        if cb is not None and meta is not None:
            await cb(dmd_id, meta["item_id"], True)


class _DummyMqttGateway(ShowcaseMqttGateway):
    """Captures outgoing MQTT publishes in memory without binding a TCP socket."""

    def __init__(self) -> None:
        super().__init__()
        self.published: List[Dict[str, Any]] = []

    async def publish_json(self, topic: str, data: Dict[str, Any], retain: bool = False) -> None:
        self.published.append({"topic": topic, "data": data, "retain": retain})


@pytest.fixture()
async def isolated_showcase_env(tmp_path: Path, monkeypatch):
    """Wire `app.main` singletons to isolated temp stores, fake catalog, and fast transcoder."""
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("DMD_DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "cache_dir", cache_dir)

    reg = ScraperRegistry()
    reg._chains = {
        "games": [_FakeCatalogSource("games")],
        "movies": [_FakeCatalogSource("movies")],
        "series": [],
        "anime": [],
    }
    local_seen = JsonSeenHistoryRepository(file_path=data_dir / "seen_history.json")
    local_profiles = DmdProfileStore(file_path=data_dir / "dmd_profiles.json")
    local_chain = DomainSourceChainService(registry=reg, history_repo=local_seen)
    local_mqtt = _DummyMqttGateway()
    local_streamer = _ControlledMockStreamer()
    local_transcoder = _FastStreamTranscoder()

    orch = ConsumeOnceOrchestrator(
        chain_service=local_chain,
        seen_repo=local_seen,
        transcoder=local_transcoder,
        streamer=local_streamer,
        mqtt_gateway=local_mqtt,
        profile_store=local_profiles,
    )

    monkeypatch.setattr("app.main.source_chain_service", local_chain)
    monkeypatch.setattr("app.main.seen_repository", local_seen)
    monkeypatch.setattr("app.main.dmd_profile_store", local_profiles)
    monkeypatch.setattr("app.main.consume_once_orchestrator", orch)

    try:
        yield {
            "orch": orch,
            "chain": local_chain,
            "seen": local_seen,
            "profiles": local_profiles,
            "mqtt": local_mqtt,
            "streamer": local_streamer,
            "tmp_path": tmp_path,
        }
    finally:
        await orch.stop_server_managed_profiles()


@pytest.mark.asyncio
async def test_multi_candidate_search_api_localized_and_filtered(isolated_showcase_env):
    """Verify GET/POST `/api/v1/{domain}/search` respects `q`, `systems`/`genres`, `lang`, and stages candidates without marking seen."""
    env = isolated_showcase_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Filtered + localized French search on Games
        r_fr = await client.get(
            "/api/v1/games/search",
            params={"q": "Street Fighter", "systems": "cps2", "genres": "fighting", "lang": "fr"},
        )
        assert r_fr.status_code == 200
        body_fr = r_fr.json()
        assert body_fr["count"] == 1
        cand_fr = body_fr["items"][0]
        assert cand_fr["candidate_id"] == "games_ss_ssf2t"
        assert cand_fr["lang"] == "fr"
        assert "légendaire" in cand_fr["synopsis"]
        assert env["chain"].get_staged_candidate("games_ss_ssf2t") is not None

        # 2. POST search in Japanese (`ja`) vs Spanish (`es`) on Movies
        r_ja = await client.post(
            "/api/v1/movies/search",
            json={"q": "Element", "lang": "ja", "limit": 5},
        )
        assert r_ja.status_code == 200
        m_ja = r_ja.json()["items"][0]
        assert m_ja["title"] == "フィフス・エレメント"
        assert m_ja["lang"] == "ja"

        r_es = await client.post(
            "/api/v1/movies/search",
            json={"q": "Elemento", "lang": "es", "limit": 5},
        )
        assert r_es.status_code == 200
        assert r_es.json()["items"][0]["title"] == "El Quinto Elemento"

        # 3. Empty-query browse returns unseen candidates without marking them consumed in seen_history.json
        r_browse = await client.get("/api/v1/games/search", params={"lang": "en", "limit": 3})
        assert r_browse.status_code == 200
        assert r_browse.json()["count"] == 3
        assert env["seen"].get_seen_ids("games") == set()


@pytest.mark.asyncio
async def test_ondemand_priority_queue_enqueue_next_fifo_standby_demotion_and_delete(isolated_showcase_env):
    """
    Verify Mode 1 DMD On-Demand Queue:
    - Demotes unconsumed random `ready` slot to `standby` (zero lost transcode)
    - Delivers queued items in strict FIFO order in the requested `lang`
    - Supports `DELETE /queue/{queue_id}` cleanup, then resumes preserved random standby slot.
    """
    env = isolated_showcase_env
    orch: ConsumeOnceOrchestrator = env["orch"]

    # Register Mode 1 DMD client with Mega Drive filter -> prepares `Streets of Rage 2` randomly
    await orch.handle_mqtt_client_config(
        "esp32_cab_1",
        {"width": 128, "height": 32, "lang": "fr", "domain": "games", "systems": ["megadrive"], "client_ip": "192.168.1.77"},
    )
    for _ in range(40):
        if ("esp32_cab_1", "games") in orch.ready_packages:
            break
        await asyncio.sleep(0.02)

    initial_random = orch.ready_packages[("esp32_cab_1", "games")]
    assert initial_random.candidate.id == "games_ss_sor2"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Search in English and pick `Super Street Fighter II Turbo` + `Metal Slug`
        r_s = await client.get("/api/v1/games/search", params={"lang": "en", "limit": 5})
        assert r_s.status_code == 200

        # Enqueue #1 (`ssf2t` in English)
        r_q1 = await client.post(
            "/api/v1/clients/esp32_cab_1/queue",
            json={"candidate_id": "games_ss_ssf2t", "domain": "games", "lang": "en", "mode": "enqueue_next"},
        )
        assert r_q1.status_code == 200

        # Enqueue #2 (`mslug` in English)
        r_q2 = await client.post(
            "/api/v1/clients/esp32_cab_1/queue",
            json={"candidate_id": "games_ss_mslug", "domain": "games", "lang": "en", "mode": "enqueue_next"},
        )
        assert r_q2.status_code == 200
        q2_id = r_q2.json()["item"]["queue_id"]

        # Enqueue #3 (`sor2`) then delete it immediately from queue
        r_q3 = await client.post(
            "/api/v1/clients/esp32_cab_1/queue",
            json={"candidate_id": "games_ss_sor2", "domain": "games", "lang": "en", "mode": "enqueue_next"},
        )
        q3_id = r_q3.json()["item"]["queue_id"]
        r_del = await client.delete(f"/api/v1/clients/esp32_cab_1/queue/{q3_id}")
        assert r_del.status_code == 200
        assert len(r_del.json()["queue"]) == 2
        assert q2_id in [it["queue_id"] for it in r_del.json()["queue"]]

        # Verify random `Streets of Rage 2` was demoted cleanly to `standby_packages`, while `ssf2t` is now `ready`!
        assert orch.ready_packages[("esp32_cab_1", "games")].candidate.id == "games_ss_ssf2t"
        assert orch.ready_packages[("esp32_cab_1", "games")].card.lang == "en"
        assert orch.standby_packages[("esp32_cab_1", "games")].candidate.id == "games_ss_sor2"

        # Claim #1 -> `ssf2t`, and #2 (`mslug`) automatically promotes to `ready` ahead of `standby`!
        claimed_1 = await orch.claim_next_package("esp32_cab_1", "games")
        assert claimed_1 is not None and claimed_1.candidate.id == "games_ss_ssf2t"
        assert orch.ready_packages[("esp32_cab_1", "games")].candidate.id == "games_ss_mslug"

        # Claim #2 -> `mslug`, and preserved random `sor2` promotes from `standby` back to `ready`!
        claimed_2 = await orch.claim_next_package("esp32_cab_1", "games")
        assert claimed_2 is not None and claimed_2.candidate.id == "games_ss_mslug"
        assert orch.ready_packages[("esp32_cab_1", "games")].candidate.id == "games_ss_sor2"
        assert orch.list_queue("esp32_cab_1") == []


@pytest.mark.asyncio
async def test_ondemand_frontendsync_oneshot_play_now_and_mark_seen_false(isolated_showcase_env):
    """
    Verify 1-shot `FrontendSync` use case:
    - `POST /api/v1/clients/{dmd_id}/queue` with `{domain, query, lang='ja', mode='play_now', mark_seen=False}`
    - Searches across all systems even if DMD idle config was restricted to `megadrive`
    - Preempts active stream immediately and does NOT pollute `seen_history.json` when claimed.
    """
    env = isolated_showcase_env
    orch: ConsumeOnceOrchestrator = env["orch"]

    await orch.handle_mqtt_client_config(
        "esp32_cab_sync",
        {"width": 128, "height": 32, "lang": "fr", "domain": "games", "systems": ["megadrive"], "client_ip": "192.168.1.88"},
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_sync = await client.post(
            "/api/v1/clients/esp32_cab_sync/queue",
            json={
                "domain": "games",
                "query": "Metal Slug",
                "lang": "ja",
                "mode": "play_now",
                "mark_seen": False,
            },
        )
        assert r_sync.status_code == 200
        item_info = r_sync.json()["item"]
        assert item_info["candidate_id"] == "games_ss_mslug"
        assert item_info["lang"] == "ja"
        assert item_info["mark_seen"] is False

        # Verify `play_now` MQTT command was emitted and `Metal Slug` is in `ready_packages`
        cmd_msgs = [m for m in env["mqtt"].published if m["topic"] == "dmd_showcase/clients/esp32_cab_sync/command"]
        assert any(m["data"].get("action") == "play_now" for m in cmd_msgs)

        claimed = await orch.claim_next_package("esp32_cab_sync", "games")
        assert claimed is not None
        assert claimed.candidate.id == "games_ss_mslug"
        assert "ネオジオ" in claimed.card.synopsis
        # Because mark_seen=False, seen_history.json must NOT contain games_ss_mslug!
        assert "games_ss_mslug" not in env["seen"].get_seen_ids("games")


@pytest.mark.asyncio
async def test_dmd_profiles_persistence_hello_and_server_managed_modes_2b_and_2a(isolated_showcase_env):
    """
    Verify:
    1. `build_card_stream_sync` generates valid `DMDS` RGB565 streams for both `128x32` and TATE `64x128`.
    2. `POST /api/v1/clients/hello` persists `dmd_profiles.json` and preserves curated playlists on subsequent hellos.
    3. Mode 2B (`supports_local_card=False`, `always_stream_udp=True`) automatically renders Card Intro + Video
       and streams UDP `DMDF` frames across multi-domain playlist slots (`games` -> `movies`), honoring
       `pause`, `resume`, and `skip` controls.
    4. Mode 2A (`supports_local_card=True`) publishes `play_card` commands over MQTT.
    """
    env = isolated_showcase_env
    orch: ConsumeOnceOrchestrator = env["orch"]
    streamer: _ControlledMockStreamer = env["streamer"]
    streamer.auto_eof_delay_s = 0.03

    # Verify TATE 64x128 server-side Info Card stream generation
    tate_dir = env["tmp_path"] / "tate_card_test"
    tate_stream_name = build_card_stream_sync(
        item_dir=str(tate_dir),
        width=64,
        height=128,
        fps=15,
        title="Ikaruga",
        year="2001",
        badge="NAOMI",
        badge_color="#00E5FF",
        subtitle="Treasure • Shoot 'em up",
        rating="9.4",
        synopsis=" Polarity-switching vertical shoot 'em up masterpiece.",
        text_scroll_speed="fast",
    )
    tate_hdr = read_rgb565_stream_header((tate_dir / tate_stream_name).read_bytes()[:16])
    assert tate_hdr["width"] == 64
    assert tate_hdr["height"] == 128
    assert tate_hdr["total_frames"] >= 20

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Passive ESP32 sends HTTP `hello`
        r_hello = await client.post(
            "/api/v1/clients/hello",
            json={
                "dmd_id": "esp32_passive_2b",
                "display_name": "Passive Barcade DMD",
                "config_source": "server",
                "supports_local_card": False,
                "width": 128,
                "height": 32,
                "udp_ip": "192.168.1.95",
                "udp_port": 4210,
                "lang": "es",
            },
        )
        assert r_hello.status_code == 200
        assert env["profiles"].file_path.exists()

        # 2. Configure curated multi-domain Spanish playlist (`games` count=1 -> `movies` count=1) via PUT /profile
        r_put = await client.put(
            "/api/v1/clients/esp32_passive_2b/profile",
            json={
                "display_name": "Passive Barcade DMD",
                "config_source": "server",
                "supports_local_card": False,
                "always_stream_udp": True,
                "width": 128,
                "height": 32,
                "udp_ip": "192.168.1.95",
                "udp_port": 4212,
                "lang": "es",
                "inter_item_gap_s": 0.0,
                "playlist": [
                    {"enabled": True, "domain": "games", "count": 1, "systems": ["cps2"]},
                    {"enabled": True, "domain": "movies", "count": 1},
                ],
            },
        )
        assert r_put.status_code == 200

        # Wait until Mode 2B director streams both a `games` item and a `movies` item on updated port 4212
        for _ in range(100):
            on_4212 = [s for s in streamer.started if s["udp_port"] == 4212]
            if any("games" in s["item_id"] for s in on_4212) and any("movies" in s["item_id"] for s in on_4212):
                break
            await asyncio.sleep(0.03)

        on_4212 = [s for s in streamer.started if s["udp_port"] == 4212]
        assert len(on_4212) >= 2
        assert all(s["udp_ip"] == "192.168.1.95" for s in on_4212)
        assert any("games" in s["item_id"] for s in on_4212)
        assert any("movies" in s["item_id"] for s in on_4212)

        # Re-sending `hello` with new IP updates IP without clobbering curated `lang="es"` or `playlist`!
        r_hello2 = await client.post(
            "/api/v1/clients/hello",
            json={
                "dmd_id": "esp32_passive_2b",
                "config_source": "server",
                "supports_local_card": False,
                "width": 128,
                "height": 32,
                "udp_ip": "192.168.1.96",
                "udp_port": 4212,
            },
        )
        assert r_hello2.status_code == 200
        prof_reloaded = DmdProfileStore(file_path=env["profiles"].file_path).get_profile("esp32_passive_2b")
        assert prof_reloaded is not None
        assert prof_reloaded["udp_ip"] == "192.168.1.96"
        assert prof_reloaded["lang"] == "es"
        assert len(prof_reloaded["playlist"]) == 2

        # Pause & Resume controls
        r_pause = await client.post("/api/v1/clients/esp32_passive_2b/control", json={"action": "pause"})
        assert r_pause.status_code == 200 and r_pause.json()["paused"] is True

        r_resume = await client.post("/api/v1/clients/esp32_passive_2b/control", json={"action": "resume"})
        assert r_resume.status_code == 200 and r_resume.json()["paused"] is False

        # Mode 2A Hybrid Receiver (`supports_local_card=True`) via MQTT `hello`
        await orch.handle_client_hello(
            "esp32_hybrid_2a",
            {
                "dmd_id": "esp32_hybrid_2a",
                "config_source": "server",
                "supports_local_card": True,
                "width": 128,
                "height": 64,
                "udp_ip": "192.168.1.99",
                "lang": "fr",
            },
        )
        for _ in range(50):
            if any(
                m["topic"] == "dmd_showcase/clients/esp32_hybrid_2a/command"
                and m["data"].get("action") == "play_card"
                for m in env["mqtt"].published
            ):
                break
            await asyncio.sleep(0.02)

        hybrid_cmds = [
            m
            for m in env["mqtt"].published
            if m["topic"] == "dmd_showcase/clients/esp32_hybrid_2a/command"
            and m["data"].get("action") == "play_card"
        ]
        assert len(hybrid_cmds) >= 1
        assert hybrid_cmds[0]["data"]["card"]["poster_width"] == 48
        assert hybrid_cmds[0]["data"]["card"]["poster_height"] == 64

        # Clean up directors
        r_del_p1 = await client.delete("/api/v1/clients/esp32_passive_2b/profile")
        assert r_del_p1.status_code == 200
        await orch.stop_server_managed_profiles()


@pytest.mark.asyncio
async def test_mode1_multi_engine_mqtt_live_profile_inspector_and_readonly_put_guard(isolated_showcase_env):
    """
    Verify Mode 1 ('Active DMD / Passive Showcase', e.g. `ArcadeMatrix_RPi` over MQTT Retain):
    1. `GET /api/v1/clients/{dmd_id}/profile` synthesizes the exact live configuration pushed by the
       physical DMD's sibling engines (`rpi_games_128x32`, `rpi_movies_128x32`, `rpi_anime_128x32`)
       instead of returning fake `default_playlist_slots()` defaults, marks uninstantiated domains
       (`series`) as `enabled=False`, and exposes `read_only_client_managed=True` + `client_live_payload`.
    2. Accidental `PUT /api/v1/clients/{dmd_id}/profile` is rejected with `409 Conflict` so `dmd_profiles.json`
       cannot hijack future MQTT updates from `ArcadeMatrix_RPi` unless `?force_override=true` is passed.
    3. Transient On-Demand / Studio Simulation Queue injection (`POST /api/v1/clients/{dmd_id}/queue`)
       remains 100% operational on Mode 1 DMDs.
    """
    env = isolated_showcase_env
    orch: ConsumeOnceOrchestrator = env["orch"]

    # Register 3 Mode 1 Showcase engines on 1 physical RPi (`192.168.1.42`, UDP 5568), omitting `series`
    await orch.handle_mqtt_client_config(
        "rpi_games_128x32",
        {
            "dmd_id": "rpi_games_128x32",
            "client_ip": "192.168.1.42",
            "udp_ip": "192.168.1.42",
            "udp_port": 5568,
            "width": 128,
            "height": 32,
            "lang": "fr",
            "domains": {
                "games": {
                    "lang": "fr",
                    "systems": ["neogeo", "cps2"],
                    "genres": ["fighting"],
                    "decades": ["1990s"],
                    "min_rating": 7.5,
                    "show_poster": True,
                    "show_synopsis": True,
                    "text_scroll_speed": "fast",
                    "jacket_panorama": True,
                    "marquee_panorama": True,
                    "snapshot_panorama_count": 3,
                    "show_video": True,
                    "video_max_duration_s": 11.0,
                    "video_fps": 15,
                    "video_mode": "auto_action",
                    "color_boost": "pixel_art",
                }
            },
        },
    )
    await orch.handle_mqtt_client_config(
        "rpi_movies_128x32",
        {
            "dmd_id": "rpi_movies_128x32",
            "client_ip": "192.168.1.42",
            "udp_ip": "192.168.1.42",
            "udp_port": 5568,
            "width": 128,
            "height": 32,
            "lang": "fr",
            "domains": {
                "movies": {
                    "lang": "fr",
                    "genres": ["science-fiction"],
                    "decades": ["1980s"],
                    "show_video": True,
                    "video_max_duration_s": 14.0,
                    "video_fps": 15,
                    "video_mode": "center_crop",
                    "color_boost": "cinema",
                    "video_type": "trailer",
                    "snapshot_panorama_count": 2,
                }
            },
        },
    )
    await orch.handle_mqtt_client_config(
        "rpi_anime_128x32",
        {
            "dmd_id": "rpi_anime_128x32",
            "client_ip": "192.168.1.42",
            "udp_ip": "192.168.1.42",
            "udp_port": 5568,
            "width": 128,
            "height": 32,
            "lang": "ja",
            "domains": {
                "anime": {
                    "lang": "ja",
                    "video_type": "opening",
                    "video_max_duration_s": 12.0,
                    "video_fps": 15,
                    "video_mode": "auto_action",
                    "color_boost": "anime",
                }
            },
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Inspecting ANY engine of the physical RPi board returns consolidated live MQTT config (read-only)
        r_prof = await client.get("/api/v1/clients/rpi_movies_128x32/profile")
        assert r_prof.status_code == 200
        body = r_prof.json()
        assert body["persisted"] is False
        assert body["read_only_client_managed"] is True

        prof = body["profile"]
        assert prof["config_source"] == "client"
        assert prof["udp_ip"] == "192.168.1.42"
        assert prof["udp_port"] == 5568

        slots_by_dom = {s["domain"]: s for s in prof["playlist"]}
        # `games` has exact MQTT systems/genres/decades/duration (not default 6-system fallback!)
        assert slots_by_dom["games"]["enabled"] is True
        assert slots_by_dom["games"]["engine_dmd_id"] == "rpi_games_128x32"
        assert slots_by_dom["games"]["systems"] == ["neogeo", "cps2"]
        assert slots_by_dom["games"]["genres"] == ["fighting"]
        assert slots_by_dom["games"]["decades"] == ["1990s"]
        assert slots_by_dom["games"]["video_max_duration_s"] == 11.0
        assert slots_by_dom["games"]["color_boost"] == "pixel_art"
        assert slots_by_dom["games"]["snapshot_panorama_count"] == 3

        # `movies` has exact MQTT settings
        assert slots_by_dom["movies"]["enabled"] is True
        assert slots_by_dom["movies"]["engine_dmd_id"] == "rpi_movies_128x32"
        assert slots_by_dom["movies"]["genres"] == ["scifi"]
        assert slots_by_dom["movies"]["decades"] == ["1980s"]
        assert slots_by_dom["movies"]["video_max_duration_s"] == 14.0
        assert slots_by_dom["movies"]["video_mode"] == "center_crop"
        assert slots_by_dom["movies"]["color_boost"] == "cinema"

        # `series` was NOT instantiated on the RPi -> must be disabled with empty filters
        assert slots_by_dom["series"]["enabled"] is False
        assert slots_by_dom["series"]["engine_dmd_id"] is None
        assert slots_by_dom["series"]["genres"] == []

        # `anime` is active with opening + anime boost
        assert slots_by_dom["anime"]["enabled"] is True
        assert slots_by_dom["anime"]["engine_dmd_id"] == "rpi_anime_128x32"
        assert slots_by_dom["anime"]["video_type"] == "opening"
        assert slots_by_dom["anime"]["color_boost"] == "anime"

        live_payload = body["client_live_payload"]
        assert live_payload["read_only"] is True
        assert set(live_payload["active_engines"].keys()) == {"games", "movies", "anime"}
        assert "rpi_games_128x32" in live_payload["received_configs"]
        assert live_payload["received_configs"]["rpi_games_128x32"]["udp_port"] == 5568

        # 2. Accidental PUT /profile on an active Mode 1 DMD is rejected with 409 Conflict
        r_hijack = await client.put(
            "/api/v1/clients/rpi_games_128x32/profile",
            json={"display_name": "Accidental Save", "config_source": "server"},
        )
        assert r_hijack.status_code == 409
        assert env["profiles"].get_profile("rpi_games_128x32") is None

        # 3. Transient Queue injection (On-Demand / Studio Simulation) still works 100% on Mode 1 DMD!
        r_queue = await client.post(
            "/api/v1/clients/rpi_games_128x32/queue",
            json={
                "domain": "games",
                "query": "Metal Slug",
                "lang": "fr",
                "mode": "enqueue_next",
                "mark_seen": False,
            },
        )
        assert r_queue.status_code == 200
        assert r_queue.json()["item"]["candidate_id"] == "games_ss_mslug"

        # 4. Explicit `?force_override=true` escape hatch succeeds if an integrator intentionally converts it
        r_force = await client.put(
            "/api/v1/clients/rpi_games_128x32/profile?force_override=true",
            json={"display_name": "Converted Passive", "config_source": "server", "paused": True},
        )
        assert r_force.status_code == 200
        await orch.stop_server_managed_profiles()

