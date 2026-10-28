"""
Comprehensive test suite for Multi-Source Fallback, Cross-Source Aggregation (`CandidateAggregator`),
`IgdbGamesAdapter`, `ArcadeItaliaAdapter` 1-hop shorthand resolution (`sf2x` on `cps2`),
ScreenScraper HTTP-200 quota circuit-breaker, `SecretRedactingLogFilter`, 64-system
`RETRO_SYSTEM_CATALOG`, On-Demand filter forwarding, and Live Source Telemetry.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from app.application.candidate_aggregator import CandidateAggregator
from app.application.consume_once_orchestrator import ConsumeOnceOrchestrator
from app.application.source_chain_service import DomainSourceChainService
from app.config import settings
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate, PanelGeometry
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import GAME_GENRE_CATALOG, RETRO_SYSTEM_CATALOG
from app.infrastructure.persistence.dmd_profile_store import DmdProfileStore
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.persistence.server_config_store import (
    DEFAULT_SOURCE_POLICIES,
    ServerConfigStore,
    server_config_store,
)
from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter
from app.infrastructure.scrapers.games.igdb_adapter import IgdbGamesAdapter
from app.infrastructure.scrapers.games.screenscraper_adapter import ScreenScraperAdapter
from app.infrastructure.scrapers.registry import ScraperRegistry
from app.main import app, batch_manager
from app.security.log_redactor import SecretRedactingLogFilter, install_secret_log_redaction


def _png_bytes(w: int = 32, h: int = 32) -> bytes:
    img = Image.new("RGB", (w, h), (240, 100, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_configurable_source_policies_ordering_and_health_metadata(
    tmp_path: Path, monkeypatch
):
    """
    Scenario 1: Configurable `source_policies` ordering (`Primary -> Secondary -> Tertiary -> Quaternary`),
    disabled sources filtering, and runtime reordering via `POST /api/v1/admin/config`.
    """
    data_dir = tmp_path / "cfg_data"
    monkeypatch.setenv("DMD_DATA_DIR", str(data_dir))
    local_store = ServerConfigStore(file_path=data_dir / "server_config.json")
    monkeypatch.setattr(
        "app.infrastructure.scrapers.registry.server_config_store", local_store
    )
    monkeypatch.setattr("app.main.server_config_store", local_store)

    reg = ScraperRegistry()
    monkeypatch.setattr("app.main.scraper_registry", reg)

    # Verify canonical default ordering across all 4 domains
    for dom, expected_pol in DEFAULT_SOURCE_POLICIES.items():
        chain = reg.get_chain(dom)  # type: ignore[arg-type]
        assert [s.source_id for s in chain] == expected_pol["ordered_sources"]

    detailed = reg.describe_chains_detailed()
    assert [e["display_name"] for e in detailed["games"]] == [
        "IGDB (Twitch / Amazon)",
        "ScreenScraper",
        "ArcadeItalia (ADB)",
        "Libretro + Wikidata",
    ]
    assert [e["display_name"] for e in detailed["anime"]] == [
        "Jikan + AnimeThemes",
        "AniList",
    ]
    for dom in ("games", "movies", "series", "anime"):
        assert {e["role"] for e in detailed[dom]} == {"primary", "fallback"}
        assert [e["priority"] for e in detailed[dom]] == list(
            range(1, len(detailed[dom]) + 1)
        )

    # Reorder `games` chain and disable `screenscraper` via POST /api/v1/admin/config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/admin/config",
            json={
                "igdb_client_id": "test_igdb_cid",
                "igdb_client_secret": "test_igdb_secret",
                "source_policies": {
                    "games": {
                        "ordered_sources": [
                            "arcadeitalia",
                            "igdb",
                            "libretro_wikidata",
                            "screenscraper",
                        ],
                        "disabled_sources": ["screenscraper"],
                        "aggregation_mode": "fallback_only",
                    },
                    "anime": {
                        "ordered_sources": ["anilist", "jikan"],
                        "disabled_sources": [],
                        "aggregation_mode": "enrich_missing",
                    },
                },
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        games_prov = payload["providers"]["games"]
        assert [p["source_id"] for p in games_prov] == [
            "arcadeitalia",
            "igdb",
            "libretro_wikidata",
            "screenscraper",
        ]
        assert games_prov[0]["role"] == "primary"
        assert all(p["role"] == "fallback" for p in games_prov[1:])
        assert games_prov[3]["enabled"] is False
        assert games_prov[0]["aggregation_mode"] == "fallback_only"

        # Active `get_chain` excludes disabled `screenscraper`
        assert [s.source_id for s in reg.get_chain("games")] == [
            "arcadeitalia",
            "igdb",
            "libretro_wikidata",
        ]
        assert [s.source_id for s in reg.get_chain("anime")] == ["anilist", "jikan"]

        # Public config masks user-entered IGDB credentials
        pub = payload["config"]
        assert pub["igdb_client_id"] == "••••••••"
        assert pub["igdb_client_secret"] == "••••••••"


@pytest.mark.asyncio
async def test_screenscraper_http200_quota_circuit_breaker_and_immediate_failover(
    tmp_path: Path, monkeypatch
):
    """
    Scenario 2: ScreenScraper HTTP-200 plaintext quota error (`Le nombre de threads autorisé...`)
    immediately breaks the retry loop on attempt 1 (zero 10x retry storm), activates the 60s
    cooldown circuit-breaker, and `DomainSourceChainService` fails over to the next source.
    """
    ScreenScraperAdapter.reset_circuit_breaker()
    IgdbGamesAdapter.reset_circuit_breaker()
    data_dir = tmp_path / "ss_cb"
    monkeypatch.setenv("DMD_DATA_DIR", str(data_dir))
    local_store = ServerConfigStore(file_path=data_dir / "server_config.json")
    local_store.update(
        {
            "screenscraper_dev_id": "dev_test",
            "screenscraper_dev_pass": "dev_pass_secret",
            "screenscraper_user": "user1",
            "screenscraper_pass": "pass1",
        }
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.screenscraper_adapter.server_config_store",
        local_store,
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.igdb_adapter.server_config_store",
        local_store,
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.registry.server_config_store",
        local_store,
    )

    ss_http_calls = 0

    class _QuotaClient:
        async def get(self, url: str, params: Any = None, **kwargs: Any) -> httpx.Response:
            nonlocal ss_http_calls
            ss_http_calls += 1
            return httpx.Response(
                200,
                text="Le nombre de threads autorisé pour le membre est atteint",
                request=httpx.Request("GET", url),
            )

    @asynccontextmanager
    async def _fake_ss_client(timeout: float = 6.0):
        yield _QuotaClient()

    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.screenscraper_adapter.create_async_http_client",
        _fake_ss_client,
    )

    class _SecondaryFallbackSource(IScraperSource):
        source_id = "libretro_wikidata"
        display_name = "Libretro + Wikidata"
        domain: DomainType = "games"
        role = "fallback"

        def is_configured(self) -> bool:
            return True

        def supported_filters(self, lang: str = "fr"):
            return {}

        async def fetch_random_unseen(
            self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1
        ) -> List[MediaCandidate]:
            return [
                MediaCandidate(
                    id="games_libretro_snes_super_metroid",
                    domain="games",
                    title="Super Metroid",
                    year="1994",
                    badge="SNES",
                    badge_color="#7E57C2",
                    subtitle="Action • SNES",
                    rating="9.5",
                    synopsis="Samus Aran explore la planète Zebes.",
                    lang="fr",
                    poster_url="https://example.org/smetroid_box.png",
                    marquee_url="https://example.org/smetroid_title.png",
                    video_url="https://example.org/smetroid.mp4",
                    source_id=self.source_id,
                    extra_meta={"system": "snes", "synopsis_localized": "1"},
                )
            ]

        async def search_by_query(self, query: str, lang: str = "fr"):
            return None

    ss_adapter = ScreenScraperAdapter()
    sec_adapter = _SecondaryFallbackSource()
    reg = ScraperRegistry()
    reg._chains["games"] = [ss_adapter, sec_adapter]

    repo = JsonSeenHistoryRepository(file_path=data_dir / "seen_history.json")
    chain_svc = DomainSourceChainService(registry=reg, history_repo=repo)

    picked = await chain_svc.acquire_unseen_candidates(
        FilterCriteria(domain="games", systems=["snes"], lang="fr"), count=1
    )
    assert len(picked) == 1
    assert picked[0].title == "Super Metroid"
    assert picked[0].source_id == "libretro_wikidata"
    # Must abort on the very first HTTP-200 quota response (not 6-10 retries!)
    assert ss_http_calls == 1
    assert ss_adapter.is_in_cooldown() is True
    assert reg.describe_chains_detailed()["games"][0]["health"] == "cooldown"

    # Subsequent calls skip ScreenScraper in 0 HTTP calls while in cooldown
    picked_2 = await chain_svc.acquire_unseen_candidates(
        FilterCriteria(domain="games", systems=["snes"], lang="fr"), count=1
    )
    assert ss_http_calls == 1
    ScreenScraperAdapter.reset_circuit_breaker()


@pytest.mark.asyncio
async def test_igdb_adapter_sf2x_cps2_and_cross_source_candidate_aggregation(
    tmp_path: Path, monkeypatch
):
    """
    Scenario 3:
    - `IgdbGamesAdapter` resolves `"sf2x"` on `cps2` via Twitch OAuth2 + Apicalypse into
      `"Super Street Fighter II Turbo"` (`mame_rom='ssf2xj'`, cover + screenshot + YouTube trailer,
      missing arcade `marquee_url` & direct `.mp4` `video_url`).
    - `CandidateAggregator` (`enrich_missing`) queries secondary `ArcadeItaliaAdapter` (`ssf2xj`)
      and merges missing `marquee_url` & direct `.mp4` `video_url` without overwriting IGDB's cover,
      producing composite provenance `source_id == "igdb+arcadeitalia"` and
      `extra_meta["sources_aggregated"] == ["igdb", "arcadeitalia"]`.
    - Also verifies `movies` cross-source enrichment (`tmdb_movies+itunes_movies`) and `fallback_only` mode.
    """
    IgdbGamesAdapter.reset_circuit_breaker()
    data_dir = tmp_path / "agg_data"
    monkeypatch.setenv("DMD_DATA_DIR", str(data_dir))
    local_store = ServerConfigStore(file_path=data_dir / "server_config.json")
    local_store.update(
        {
            "igdb_client_id": "igdb_cid_123",
            "igdb_client_secret": "igdb_secret_456",
        }
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.igdb_adapter.server_config_store",
        local_store,
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.registry.server_config_store",
        local_store,
    )

    igdb_calls: List[str] = []
    adb_queries: List[str] = []

    class _MockIgdbAndAdbClient:
        async def post(self, url: str, params: Any = None, content: bytes = b"", headers: Any = None, **kw: Any) -> httpx.Response:
            if "id.twitch.tv/oauth2/token" in url:
                return httpx.Response(
                    200,
                    json={"access_token": "twitch_tok_abc", "expires_in": 3600},
                    request=httpx.Request("POST", url),
                )
            body_txt = content.decode("utf-8", errors="ignore")
            igdb_calls.append(body_txt)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9921,
                        "name": "Super Street Fighter II Turbo",
                        "summary": "Fast-paced CPS-2 tournament fighting classic introducing Super Combos and Akuma.",
                        "first_release_date": 761875200,  # Feb 1994
                        "total_rating": 91.5,
                        "platforms": [52],
                        "genres": [4],
                        "cover": {"image_id": "co2xyz"},
                        "screenshots": [{"image_id": "sc1abc"}],
                        "artworks": [{"image_id": "ar1def"}],
                        "videos": [{"video_id": "yt_sf2x_01"}],
                        "alternative_names": [
                            {"name": "Super Street Fighter II X: Grand Master Challenge"}
                        ],
                    }
                ],
                request=httpx.Request("POST", url),
            )

        async def get(self, url: str, params: Any = None, **kw: Any) -> httpx.Response:
            if "adb.arcadeitalia.net" in url:
                gname = (params or {}).get("game_name", "")
                adb_queries.append(str(gname))
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "game_name": "ssf2xj",
                                "cloneof": "ssf2t",
                                "title": "Super Street Fighter II X: Grand Master Challenge (Japan 940223)",
                                "year": "1994",
                                "manufacturer": "Capcom",
                                "genre": "Fighter / Versus",
                                "sourcefile": "capcom/cps2.cpp",
                                "rate": "95",
                                "url_image_flyer": "https://adb.arcadeitalia.net/flyer/ssf2xj.png",
                                "url_image_marquee": "https://adb.arcadeitalia.net/marquee/ssf2xj.png",
                                "url_image_ingame": "https://adb.arcadeitalia.net/snap/ssf2xj.png",
                                "url_video_shortplay": "https://adb.arcadeitalia.net/videosnaps/mp4/ssf2xj.mp4",
                            }
                        ]
                    },
                    request=httpx.Request("GET", url, params=params),
                )
            # Wikipedia mock returning French localized synopsis
            return httpx.Response(
                200,
                json={
                    "type": "standard",
                    "description": "jeu vidéo de combat de 1994",
                    "extract": "Super Street Fighter II X est un jeu vidéo de combat développé par Capcom sur borne CPS-2 en 1994.",
                },
                request=httpx.Request("GET", url),
            )

    @asynccontextmanager
    async def _mock_client_ctx(timeout: float = 6.0):
        yield _MockIgdbAndAdbClient()

    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.igdb_adapter.create_async_http_client",
        _mock_client_ctx,
    )
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.arcadeitalia_adapter.create_async_http_client",
        _mock_client_ctx,
    )

    igdb_adapter = IgdbGamesAdapter()
    adb_adapter = ArcadeItaliaAdapter()
    reg = ScraperRegistry()
    reg._chains["games"] = [igdb_adapter, adb_adapter]
    repo = JsonSeenHistoryRepository(file_path=data_dir / "seen_history.json")
    svc = DomainSourceChainService(registry=reg, history_repo=repo)

    criteria = FilterCriteria(domain="games", systems=["cps2"], lang="fr")
    cands = await svc.search_candidates(criteria=criteria, query="sf2x", limit=1)
    assert len(cands) == 1
    merged = cands[0]

    assert merged.title == "Super Street Fighter II Turbo"
    assert merged.extra_meta["system"] == "cps2"
    # Primary IGDB cover preserved; missing marquee & direct MP4 enriched from ArcadeItalia!
    assert merged.poster_url == "https://images.igdb.com/igdb/image/upload/t_cover_big/co2xyz.jpg"
    assert merged.marquee_url == "https://adb.arcadeitalia.net/marquee/ssf2xj.png"
    assert merged.video_url == "https://adb.arcadeitalia.net/videosnaps/mp4/ssf2xj.mp4"
    assert merged.source_id == "igdb+arcadeitalia"
    assert merged.extra_meta["sources_aggregated"] == ["igdb", "arcadeitalia"]
    assert len(adb_queries) >= 1 and "ssf2xj" in adb_queries[0]

    # Verify `fallback_only` disables secondary enrichment
    local_store.update(
        {
            "source_policies": {
                "games": {
                    "ordered_sources": ["igdb", "arcadeitalia"],
                    "disabled_sources": [],
                    "aggregation_mode": "fallback_only",
                }
            }
        }
    )
    adb_queries.clear()
    cands_fb_only = await svc.search_candidates(criteria=criteria, query="sf2x", limit=1)
    assert len(cands_fb_only) == 1
    assert cands_fb_only[0].source_id == "igdb"
    assert cands_fb_only[0].marquee_url is None
    assert adb_queries == []

    # Verify cross-source enrichment in `movies` (`tmdb_movies+itunes_movies`)
    class _MockTmdbMovies(IScraperSource):
        source_id = "tmdb_movies"
        display_name = "TMDB (Movies)"
        domain: DomainType = "movies"
        role = "primary"

        def is_configured(self) -> bool:
            return True

        def supported_filters(self, lang: str = "fr"):
            return {}

        async def fetch_random_unseen(self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1):
            return []

        async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
            return MediaCandidate(
                id="movies_tmdb_603",
                domain="movies",
                title="The Matrix",
                year="1999",
                badge="FILM",
                badge_color="#E53935",
                subtitle="Sci-Fi • 1999",
                rating="8.7",
                synopsis="Un pirate informatique découvre la nature réelle de son univers.",
                lang=lang,
                poster_url="https://image.tmdb.org/t/p/w500/matrix.jpg",
                backdrop_url="https://image.tmdb.org/t/p/w780/matrix_bg.jpg",
                video_url=None,
                youtube_url="https://www.youtube.com/watch?v=vKQi3bBA1y8",
                source_id=self.source_id,
                extra_meta={"synopsis_localized": "1"},
            )

    class _MockItunesMovies(IScraperSource):
        source_id = "itunes_movies"
        display_name = "Apple iTunes"
        domain: DomainType = "movies"
        role = "fallback"

        def is_configured(self) -> bool:
            return True

        def supported_filters(self, lang: str = "fr"):
            return {}

        async def fetch_random_unseen(self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1):
            return []

        async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
            return MediaCandidate(
                id="movies_itunes_603",
                domain="movies",
                title="The Matrix",
                year="1999",
                badge="FILM",
                badge_color="#E53935",
                subtitle="Sci-Fi • 1999",
                rating="8.6",
                synopsis="iTunes Matrix description.",
                lang=lang,
                poster_url="https://is1-ssl.mzstatic.com/matrix_1000x1000bb.jpg",
                marquee_url="https://is1-ssl.mzstatic.com/matrix_banner.jpg",
                video_url="https://video-ssl.itunes.apple.com/matrix_preview.m4v",
                source_id=self.source_id,
            )

    agg = CandidateAggregator()
    movie_chain: List[IScraperSource] = [_MockTmdbMovies(), _MockItunesMovies()]
    primary_movie = await movie_chain[0].search_by_query("The Matrix", lang="fr")
    assert primary_movie is not None
    enriched_movie = await agg.enrich_candidate(
        primary_movie, movie_chain, FilterCriteria(domain="movies", lang="fr")
    )
    assert enriched_movie.poster_url == "https://image.tmdb.org/t/p/w500/matrix.jpg"
    assert enriched_movie.video_url == "https://video-ssl.itunes.apple.com/matrix_preview.m4v"
    assert enriched_movie.marquee_url == "https://is1-ssl.mzstatic.com/matrix_banner.jpg"
    assert enriched_movie.source_id == "tmdb_movies+itunes_movies"
    assert enriched_movie.extra_meta["sources_aggregated"] == ["tmdb_movies", "itunes_movies"]


@pytest.mark.asyncio
async def test_arcadeitalia_sf2x_single_https_call_zero_400_and_wiki_timeout_resilience(
    monkeypatch,
):
    """
    Scenario 4:
    - `ArcadeItaliaAdapter` resolves `"sf2x"` on `cps2` in 1 single HTTPS call
      (`game_name=ssf2xj;ssf2t`) with zero `title=` `400 Bad Request` calls.
    - Survives a simulated Wikipedia timeout (`asyncio.TimeoutError`) without losing the valid
      CPS-2 ADB candidate!
    - Console-only queries (`systems=['snes']`) skip `ArcadeItaliaAdapter` in `0 ms` in
      `DomainSourceChainService`.
    """
    recorded_requests: List[httpx.URL] = []

    class _StrictAdbClient:
        async def get(self, url: str, params: Any = None, **kw: Any) -> httpx.Response:
            req = httpx.Request("GET", url, params=params)
            recorded_requests.append(req.url)
            if "title=" in str(req.url):
                return httpx.Response(400, text="Bad Request", request=req)
            if "adb.arcadeitalia.net" in url:
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "game_name": "ssf2t",
                                "cloneof": "",
                                "title": "Super Street Fighter II Turbo (World 940223)",
                                "year": "1994",
                                "manufacturer": "Capcom",
                                "genre": "Fighter / Versus",
                                "sourcefile": "capcom/cps2.cpp",
                                "rate": "94",
                                "url_image_flyer": "https://adb.arcadeitalia.net/flyer/ssf2t.png",
                                "url_image_marquee": "https://adb.arcadeitalia.net/marquee/ssf2t.png",
                                "url_image_ingame": "https://adb.arcadeitalia.net/snap/ssf2t.png",
                                "url_video_shortplay": "https://adb.arcadeitalia.net/videosnaps/mp4/ssf2t.mp4",
                            }
                        ]
                    },
                    request=req,
                )
            raise httpx.ReadTimeout("Simulated Wikipedia timeout", request=req)

    @asynccontextmanager
    async def _strict_ctx(timeout: float = 12.0):
        yield _StrictAdbClient()

    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.arcadeitalia_adapter.create_async_http_client",
        _strict_ctx,
    )

    adb = ArcadeItaliaAdapter()
    hits = await adb.search_candidates(
        query="sf2x",
        criteria=FilterCriteria(domain="games", systems=["cps2"], lang="fr"),
        limit=2,
    )
    assert len(hits) == 1
    assert hits[0].id == "games_adb_ssf2t"
    assert hits[0].extra_meta["system"] == "cps2"
    assert hits[0].video_url == "https://adb.arcadeitalia.net/videosnaps/mp4/ssf2t.mp4"

    adb_urls = [str(u) for u in recorded_requests if "adb.arcadeitalia.net" in str(u)]
    assert len(adb_urls) == 1
    assert "ajax=query_mame" in adb_urls[0]
    assert "ssf2xj" in adb_urls[0] and "ssf2t" in adb_urls[0]
    assert all("title=" not in u for u in adb_urls)


def test_secret_redacting_log_filter_scrubs_urls_json_and_httpx_args():
    """
    Scenario 5: `SecretRedactingLogFilter` scrubs `devpassword`, `sspassword`, `devid`, `ssid`,
    `api_key`, `client_secret`, `access_token`, and `Bearer` tokens across `record.msg` and
    `record.args` (including `httpx.URL` objects), and clamps `httpx`/`httpcore` to `WARNING`.
    """
    install_secret_log_redaction()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING

    filt = SecretRedactingLogFilter()
    raw_url = httpx.URL(
        "https://api.screenscraper.fr/api2/jeuRecherche.php"
        "?devid=mySecretDevId&devpassword=mySuperSecretDevPass&ssid=playerOne&sspassword=myUserPass&recherche=sf2"
    )
    rec = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: %s "%s" -> Bearer eyJhbGciOiJIUzI1NiJ9.secretPayload {"client_secret": "twitchTopSecret", "api_key": "tmdbKey999"}',
        args=("GET", raw_url),
        exc_info=None,
    )
    assert filt.filter(rec) is True
    rendered = rec.getMessage()

    for forbidden in (
        "mySecretDevId",
        "mySuperSecretDevPass",
        "playerOne",
        "myUserPass",
        "eyJhbGciOiJIUzI1NiJ9.secretPayload",
        "twitchTopSecret",
        "tmdbKey999",
    ):
        assert forbidden not in rendered
    assert "***" in rendered
    assert "recherche=sf2" in rendered


@pytest.mark.asyncio
async def test_expanded_64_system_catalog_ondemand_filter_forwarding_and_live_source_telemetry(
    tmp_path: Path, monkeypatch
):
    """
    Scenario 6:
    - `RETRO_SYSTEM_CATALOG` contains >= 55 systems (64 systems) with `igdb_id` / `igdb_ids` on all,
      and `GAME_GENRE_CATALOG` contains `igdb_genre_ids` on all 16 genres.
    - `POST /api/v1/games/on-demand` forwards `systems=["cps2"]`, `genres`, `decades` to provider.
    - `/api/v1/clients` and `/api/v1/clients/{dmd_id}/queue` expose live source telemetry:
      `playing_source`, `ready_source`, `standby_source`, `preparing_source`, `last_played_source`,
      and `source_used`.
    """
    assert len(RETRO_SYSTEM_CATALOG) >= 55
    for sys_id, smeta in RETRO_SYSTEM_CATALOG.items():
        has_igdb = bool(smeta.get("igdb_id") or smeta.get("igdb_ids"))
        assert has_igdb, f"System '{sys_id}' is missing igdb_id / igdb_ids"
    for expected_sys in (
        "cps1",
        "cps2",
        "cps3",
        "neogeo",
        "naomi",
        "atomiswave",
        "model2",
        "model3",
        "segacd",
        "sega32x",
        "ps2",
        "psp",
        "nds",
        "3do",
        "jaguar",
        "amiga",
        "c64",
        "msx2",
        "zxspectrum",
        "dos",
        "scummvm",
    ):
        assert expected_sys in RETRO_SYSTEM_CATALOG
    for g_id, gmeta in GAME_GENRE_CATALOG.items():
        assert bool(gmeta.get("igdb_genre_ids")), f"Genre '{g_id}' missing igdb_genre_ids"

    # Verify POST /api/v1/games/on-demand forwards `systems=['cps2']` into `criteria`
    data_dir = tmp_path / "tel_data"
    cache_dir = tmp_path / "tel_cache"
    monkeypatch.setenv("DMD_DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "cache_dir", cache_dir)

    captured_criteria: List[FilterCriteria] = []

    async def _capturing_od_search(
        query: str, lang: str = "fr", criteria: Optional[FilterCriteria] = None
    ):
        from app.providers.base import RawMediaCandidate

        if criteria is not None:
            captured_criteria.append(criteria)
        return RawMediaCandidate(
            id="games_igdb_cps2_9921",
            domain="games",
            title="Super Street Fighter II Turbo",
            year="1994",
            badge="CPS2",
            badge_color="#00ACC1",
            subtitle="Combat • CPS2",
            rating="9.2",
            synopsis="Classique CPS-2.",
            lang=lang,
            source_used="igdb+arcadeitalia",
        )

    monkeypatch.setattr(
        batch_manager.providers["games"], "search_on_demand", _capturing_od_search
    )

    # Set up isolated ConsumeOnceOrchestrator for live source telemetry check
    class _AggSource(IScraperSource):
        source_id = "igdb+arcadeitalia"
        display_name = "IGDB + ArcadeItalia"
        domain: DomainType = "games"
        role = "primary"

        def is_configured(self) -> bool:
            return True

        def supported_filters(self, lang: str = "fr"):
            return {}

        async def fetch_random_unseen(self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1):
            out = []
            for idx in range(1, 5):
                cid = f"games_agg_{idx}"
                if cid not in seen_ids:
                    out.append(
                        MediaCandidate(
                            id=cid,
                            domain="games",
                            title=f"Aggregated CPS2 Hit {idx}",
                            year="1994",
                            badge="CPS2",
                            badge_color="#00ACC1",
                            subtitle="Fighting • CPS2",
                            rating="9.1",
                            synopsis="Synopsis FR.",
                            lang="fr",
                            poster_url="https://example.org/p.png",
                            marquee_url="https://example.org/m.png",
                            video_url="https://example.org/v.mp4",
                            source_id="igdb+arcadeitalia",
                            extra_meta={"system": "cps2", "synopsis_localized": "1"},
                        )
                    )
                    if len(out) >= count:
                        break
            return out

        async def search_by_query(self, query: str, lang: str = "fr"):
            return (await self.fetch_random_unseen(FilterCriteria(domain="games", lang=lang), set(), 1))[0]

    class _FastTranscoder:
        async def transcode_candidate(self, cand: MediaCandidate, criteria: FilterCriteria, geometry: PanelGeometry, out_dir: Path) -> Dict[str, Any]:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "poster.png").write_bytes(_png_bytes(32, 32))
            (out_dir / "poster.rgb565").write_bytes(b"\x00" * (32 * 32 * 2))
            stream_p = out_dir / "unified.rgb565.stream"
            stream_p.write_bytes(b"DMDS\x01\x0f\x80\x00\x20\x00\x02\x00\x42\x00\x00\x00" + b"\x00" * (128 * 32 * 4))
            return {
                "poster_png": "poster.png",
                "poster_rgb565": "poster.rgb565",
                "unified_stream_rgb565": "unified.rgb565.stream",
                "has_video": True,
                "stream_frames": 2,
                "stream_fps": 15,
                "stream_duration_s": 4.0,
            }

    class _NoopMqtt:
        def set_handlers(self, **kw: Any) -> None:
            pass

        async def publish_json(self, topic: str, data: Dict[str, Any], retain: bool = False) -> None:
            pass

    class _NoopStreamer:
        active_streams_count = 0

        def get_active_stream(self, dmd_id: str) -> None:
            return None

        async def start_udp_clock_stream(self, **kw: Any) -> None:
            pass

        async def stop_stream(self, dmd_id: str) -> None:
            pass

    reg = ScraperRegistry()
    reg._chains = {"games": [_AggSource()], "movies": [], "series": [], "anime": []}
    seen_repo = JsonSeenHistoryRepository(file_path=data_dir / "seen_history.json")
    prof_store = DmdProfileStore(file_path=data_dir / "dmd_profiles.json")
    chain_svc = DomainSourceChainService(registry=reg, history_repo=seen_repo)
    orch = ConsumeOnceOrchestrator(
        chain_service=chain_svc,
        seen_repo=seen_repo,
        transcoder=_FastTranscoder(),  # type: ignore[arg-type]
        streamer=_NoopStreamer(),  # type: ignore[arg-type]
        mqtt_gateway=_NoopMqtt(),  # type: ignore[arg-type]
        profile_store=prof_store,
    )
    monkeypatch.setattr("app.main.consume_once_orchestrator", orch)
    monkeypatch.setattr("app.main.source_chain_service", chain_svc)
    monkeypatch.setattr("app.main.dmd_profile_store", prof_store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_od = await client.post(
            "/api/v1/games/on-demand",
            json={
                "domain": "games",
                "query": "sf2x",
                "systems": ["cps2"],
                "genres": ["fighting"],
                "decades": ["1990s"],
                "lang": "fr",
            },
        )
        assert r_od.status_code == 200
        assert r_od.json()["source_used"] == "igdb+arcadeitalia"
        assert len(captured_criteria) == 1
        assert captured_criteria[0].systems == ["cps2"]
        assert captured_criteria[0].genres == ["fighting"]
        assert captured_criteria[0].decades == ["1990s"]

        # Register DMD slot, wait for N & N+1 slots, enqueue an On-Demand item, and claim 1 package
        await client.post(
            "/api/v1/games/config",
            json={"client_id": "rpi_cab_cps2", "width": 128, "height": 32, "lang": "fr"},
        )
        for _ in range(30):
            if ("rpi_cab_cps2", "games") in orch.ready_packages and (
                "rpi_cab_cps2",
                "games",
            ) in orch.standby_packages:
                break
            await asyncio.sleep(0.02)

        r_q = await client.post(
            "/api/v1/clients/rpi_cab_cps2/queue",
            json={"domain": "games", "query": "sf2x", "systems": ["cps2"], "lang": "fr"},
        )
        assert r_q.status_code == 200
        assert r_q.json()["item"]["source_used"] == "igdb+arcadeitalia"

        r_next = await client.get("/api/v1/games/next?dmd_id=rpi_cab_cps2")
        assert r_next.status_code == 200
        assert r_next.json()["ready"] is True

        r_clients = await client.get("/api/v1/clients")
        assert r_clients.status_code == 200
        cab = next(c for c in r_clients.json()["clients"] if c["dmd_id"] == "rpi_cab_cps2")
        assert cab["playing_source"] == "igdb+arcadeitalia"
        assert cab["last_played_source"] == "igdb+arcadeitalia"
        assert cab["ready_source"] == "igdb+arcadeitalia"
        assert "standby_source" in cab
        assert "preparing_source" in cab

        r_filters = await client.get("/api/v1/games/filters?lang=fr")
        assert r_filters.status_code == 200
        systems_payload = r_filters.json()["systems"]
        assert len(systems_payload) >= 60
        cps2_entry = next(s for s in systems_payload if s["id"] == "cps2")
        assert isinstance(cps2_entry.get("supported_genres"), list) and len(cps2_entry["supported_genres"]) >= 1
        assert isinstance(cps2_entry.get("supported_decades"), list) and len(cps2_entry["supported_decades"]) >= 1
        assert cps2_entry["supported_genres"] == cps2_entry["genres"]
        assert cps2_entry["supported_decades"] == cps2_entry["decades"]

    await batch_manager.stop()


@pytest.mark.asyncio
async def test_igdb_autofill_sanitization_and_webui_autocomplete_guards(
    tmp_path: Path, monkeypatch
):
    """
    Browser password-manager autofill pollution (such as short username 'red1ll' or site URL
    'http://dmdmediashowcase:8890/') must be rejected as empty so it never falsely marks IGDB
    as configured or shadows valid `.env` credentials, and updating IGDB keys resets cooldown.
    """
    data_dir = tmp_path / "autofill_guard"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = data_dir / "server_config.json"
    cfg_file.write_text(
        '{"igdb_client_id": "red1ll", "igdb_client_secret": "http://dmdmediashowcase:8890/"}',
        encoding="utf-8",
    )

    monkeypatch.setenv("IGDB_CLIENT_ID", "w8chw0voao0qjj26vpsmukitqj02y6")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "igdb_client_id", "w8chw0voao0qjj26vpsmukitqj02y6")
    monkeypatch.setattr(settings, "igdb_client_secret", "")

    local_store = ServerConfigStore(file_path=cfg_file)
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.igdb_adapter.server_config_store",
        local_store,
    )
    monkeypatch.setattr("app.main.server_config_store", local_store)

    eff = local_store.get_all()
    assert eff["igdb_client_id"] == "w8chw0voao0qjj26vpsmukitqj02y6"
    assert eff["igdb_client_secret"] == ""

    adapter = IgdbGamesAdapter()
    assert adapter.is_configured() is False

    # Simulate cooldown then update valid credentials via POST /api/v1/admin/config -> resets cooldown
    IgdbGamesAdapter._record_cooldown("test auth failure")
    assert adapter.is_in_cooldown() is True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_save = await client.post(
            "/api/v1/admin/config",
            json={
                "igdb_client_id": "w8chw0voao0qjj26vpsmukitqj02y6",
                "igdb_client_secret": "valid_twitch_secret_token_30ch",
            },
        )
        assert r_save.status_code == 200
        assert adapter.is_in_cooldown() is False
        assert adapter.is_configured() is True

        r_html = await client.get("/")
        assert r_html.status_code == 200
        html = r_html.text
        assert 'id="igdb_client_id" autocomplete="new-password"' in html
        assert 'id="igdb_client_secret" autocomplete="new-password"' in html


@pytest.mark.asyncio
async def test_kid_icarus_neogeo_isolation_genre_matching_and_wiki_disambiguation():
    """
    Regression suite for Docker log data integrity anomalies:
    1. `Kid Icarus` (`platforms=[18, 52]` NES + PlayChoice-10 Arcade) MUST resolve to `nes` when
       `req_systems=['neogeo', 'cps1', 'nes']` and MUST be rejected (`None`) when `req_systems=['neogeo']`.
    2. `Tekken 4` (`platforms=[8, 52]`) MUST be rejected when only `['neogeo', 'cps1', 'cps2', 'nes']` are requested,
       while `Super Street Fighter II Turbo` (`[52]`, `genres=[4, 33]`) resolves to `cps2` + `fighting` (NOT `platform`!).
    3. `Zelda: Ocarina of Time` (`genres=[9, 12, 31]`) resolves to `rpg`/`action` instead of `puzzle`.
    4. Unofficial fan mods/bootlegs (`Sonic 1 Spike Bug Fix & Spindash`, `Unreal Engine ...`, `Ka Sheng` bootlegs)
       are rejected by `is_unofficial_game_release`.
    5. `LibretroWikidataAdapter` never stamps `Super Mario Bros.` onto `cps1`, and `SEARCH_SYLLABLES` are all >= 3 chars.
    6. `is_relevant_media_extract` rejects film (`Pinocchio` Zemeckis), toy (`Shogun Warriors`), cartoon (`Les Pierrafeu`),
       and unrelated Wikipedia articles (`Namco × Capcom` for `Bravoman`, `Graphismes de jeu vidéo` for `Battletoads`).
    """
    from app.domain.taxonomy import RETRO_SYSTEM_CATALOG, is_unofficial_game_release
    from app.infrastructure.scrapers.games.libretro_wikidata_adapter import LibretroWikidataAdapter
    from app.infrastructure.scrapers.games.screenscraper_tokens import SEARCH_SYLLABLES
    from app.infrastructure.scrapers.http_utils import is_relevant_media_extract

    # 1. Generic IGDB platform 52 must NEVER belong to `neogeo`
    assert 52 not in RETRO_SYSTEM_CATALOG["neogeo"]["igdb_ids"]
    assert all(len(tok) >= 3 for tok in SEARCH_SYLLABLES)

    adapter = IgdbGamesAdapter()
    kid_icarus = {
        "id": 3663,
        "name": "Kid Icarus",
        "summary": "Nintendo classic 8-bit platform adventure starring Pit.",
        "first_release_date": 535334400,
        "platforms": [18, 52],
        "genres": [8, 31],
        "cover": {"image_id": "co_kid_icarus"},
    }
    cand_nes = await adapter._build_candidate(
        None,
        kid_icarus,
        req_systems=["neogeo", "cps1", "nes"],
        req_genres=["platform", "fighting"],
        lang="en",
        fetch_wiki=False,
    )
    assert cand_nes is not None
    assert cand_nes.badge == "NES"
    assert cand_nes.extra_meta["system"] == "nes"

    cand_neogeo_only = await adapter._build_candidate(
        None,
        kid_icarus,
        req_systems=["neogeo"],
        req_genres=["platform"],
        lang="en",
        fetch_wiki=False,
    )
    assert cand_neogeo_only is None

    # 2. Tekken 4 rejected on Neo-Geo/CPS; Super Turbo resolves to CPS-2 + Fighting (never Platform!)
    tekken4 = {
        "id": 1245,
        "name": "Tekken 4",
        "summary": "3D fighting game by Namco.",
        "first_release_date": 996624000,
        "platforms": [8, 52],
        "genres": [4],
        "cover": {"image_id": "co_tk4"},
    }
    assert (
        await adapter._build_candidate(
            None,
            tekken4,
            req_systems=["neogeo", "cps1", "cps2", "nes"],
            req_genres=["platform", "fighting"],
            lang="fr",
            fetch_wiki=False,
        )
        is None
    )

    ssf2t = {
        "id": 4238,
        "name": "Super Street Fighter II Turbo",
        "summary": "Capcom CPS-2 tournament fighter.",
        "first_release_date": 761875200,
        "platforms": [52, 50],
        "genres": [4, 33],
        "cover": {"image_id": "co_ssf2t"},
    }
    cand_ssf2t = await adapter._build_candidate(
        None,
        ssf2t,
        req_systems=["neogeo", "cps2", "nes"],
        req_genres=["platform", "fighting", "beatemup"],
        lang="fr",
        fetch_wiki=False,
    )
    assert cand_ssf2t is not None
    assert cand_ssf2t.badge == "CPS-2"
    assert cand_ssf2t.extra_meta["genre"] == "fighting"
    assert "Combat" in cand_ssf2t.subtitle

    # 3. Zelda [9, 12, 31] resolves to rpg/action instead of puzzle
    zelda_oot = {
        "id": 1029,
        "name": "The Legend of Zelda: Ocarina of Time",
        "platforms": [4],
        "genres": [9, 12, 31],
        "cover": {"image_id": "co_oot"},
    }
    assert adapter._resolve_genre_for_item(zelda_oot, ["platform", "rpg", "puzzle"], title=zelda_oot["name"]) == "rpg"

    # 4. Non-retro PC engine tech demos & bug-fix patches rejected, while genuine Neo-Geo fanmods/homebrews pass!
    assert is_unofficial_game_release("Sonic 1 Spike Bug Fix & Spindash") is True
    assert is_unofficial_game_release("Unreal Engine The Legend of Zelda: Ocarina of Time") is True
    assert is_unofficial_game_release("Chrono Trigger", synopsis="RPG développé par Square.") is False

    neogeo_homebrew = {
        "id": 999888,
        "name": "Shinobi Neo-Geo Homebrew Edition",
        "summary": "Community fan-made port running natively on SNK Neo-Geo MVS/AES.",
        "first_release_date": 1700000000,
        "platforms": [79, 80],
        "genres": [8],
        "cover": {"image_id": "co_ng_hb"},
    }
    cand_hb = await adapter._build_candidate(
        None,
        neogeo_homebrew,
        req_systems=["neogeo", "nes"],
        req_genres=["platform"],
        lang="en",
        fetch_wiki=False,
    )
    assert cand_hb is not None and cand_hb.badge == "NEO-GEO"

    # 5. Libretro MAME tree discovery never stamps CPS-1 onto generic MAME titles
    lw = LibretroWikidataAdapter()
    assert await lw._discover_from_verified_libretro_tree(None, "cps1", "platform", "fr", set(), 1) == []

    # 6. Wikipedia disambiguation rejects film/toy/cartoon & unrelated page titles
    assert is_relevant_media_extract(
        {
            "title": "Pinocchio (film, 2022)",
            "description": "film réalisé par Robert Zemeckis",
            "extract": "Pinocchio est un film américain réalisé par Robert Zemeckis et sorti en 2022.",
        },
        "fr",
        is_game_domain=True,
        query_title="Pinocchio",
    ) is False
    assert is_relevant_media_extract(
        {
            "title": "Namco × Capcom",
            "description": "jeu vidéo de 2005",
            "extract": "Namco × Capcom est un jeu vidéo Tactical RPG incluant Bravoman.",
        },
        "fr",
        is_game_domain=True,
        query_title="Bravoman",
    ) is False
    assert is_relevant_media_extract(
        {
            "title": "Graphismes de jeu vidéo",
            "description": "aspect visuel",
            "extract": "Les graphismes de jeu vidéo ont évolué avec Battletoads.",
        },
        "fr",
        is_game_domain=True,
        query_title="Battletoads",
    ) is False
    assert is_relevant_media_extract(
        {
            "title": "Pinocchio (jeu vidéo)",
            "description": "jeu vidéo de 1996",
            "extract": "Pinocchio est un jeu vidéo de plates-formes sorti en 1996 sur Super Nintendo et Mega Drive.",
        },
        "fr",
        is_game_domain=True,
        query_title="Pinocchio",
    ) is True


@pytest.mark.asyncio
async def test_cross_source_synopsis_localization_precedence_and_free_translation_across_domains():
    """
    Verify:
    1. `CandidateAggregator` never overwrites a French primary synopsis (`synopsis_localized="1"`)
       with a longer English donor synopsis (`"0"`), even when `len(donor) > len(primary) + 25`.
    2. Conversely, a shorter human-localized French donor (`"1"`, e.g. ScreenScraper) always upgrades
       an English (`"0"`) or machine-translated (`"auto"`, e.g. IGDB) primary synopsis.
    3. Localized movie titles (`"Piège de cristal"` + `original_title="Die Hard"`) match English
       secondary donors (`"Die Hard"`) to pull `.m4v` trailers while keeping French title & synopsis.
    4. `ensure_candidate_localized` + `translate_synopsis_free` translates English-only synopses via
       GTX / MyMemory fallback (`"auto"`), catches English leaks, and localizes genre badges/subtitles.
    """
    from app.infrastructure.scrapers.translation_service import (
        _TRANSLATION_CACHE,
        ensure_candidate_localized,
        is_likely_english_synopsis,
    )

    _TRANSLATION_CACHE.clear()
    agg = CandidateAggregator()

    # Case 1: ScreenScraper FR ("1", short) + ArcadeItalia EN ("0", much longer) -> FR preserved!
    ss_primary = MediaCandidate(
        id="games_ss_cps1_sf2",
        domain="games",
        title="Street Fighter II",
        year="1991",
        badge="CPS1",
        badge_color="#FB8C00",
        subtitle="Combat • CPS1",
        rating="9.4",
        synopsis="Jeu de combat culte développé par Capcom mettant en scène huit guerriers.",
        lang="fr",
        poster_url="https://screenscraper.fr/sf2_box.png",
        marquee_url=None,
        video_url=None,
        source_id="screenscraper",
        extra_meta={"system": "cps1", "mame_rom": "sf2", "synopsis_localized": "1"},
    )
    adb_donor = MediaCandidate(
        id="games_adb_sf2",
        domain="games",
        title="Street Fighter II: The World Warrior",
        year="1991",
        badge="CPS1",
        badge_color="#FB8C00",
        subtitle="Fighting • CPS1",
        rating="9.4",
        synopsis=(
            "Street Fighter II is a competitive fighting game developed and published by Capcom "
            "for arcades in 1991 featuring eight selectable characters from around the world in a tournament."
        ),
        lang="fr",
        poster_url="https://adb.arcadeitalia.net/sf2_flyer.png",
        marquee_url="https://adb.arcadeitalia.net/sf2_marquee.png",
        video_url="https://adb.arcadeitalia.net/sf2.mp4",
        source_id="arcadeitalia",
        extra_meta={"system": "cps1", "mame_rom": "sf2", "synopsis_localized": "0"},
    )
    assert agg._merge_donor_into_primary(ss_primary, adb_donor, "fr") is True
    assert ss_primary.marquee_url == "https://adb.arcadeitalia.net/sf2_marquee.png"
    assert ss_primary.video_url == "https://adb.arcadeitalia.net/sf2.mp4"
    assert "Jeu de combat culte" in ss_primary.synopsis
    assert ss_primary.extra_meta["synopsis_localized"] == "1"

    # Case 2: IGDB longer "auto" primary + ScreenScraper shorter "1" human FR donor -> upgraded to "1"!
    igdb_primary = MediaCandidate(
        id="games_igdb_snes_zelda",
        domain="games",
        title="The Legend of Zelda: A Link to the Past",
        year="1991",
        badge="SNES",
        badge_color="#7E57C2",
        subtitle="Aventure • SNES",
        rating="9.6",
        synopsis=(
            "Link parcourt les mondes de la lumière et des ténèbres dans cette traduction automatique "
            "beaucoup plus longue issue d'un résumé anglophone détaillé pour tester la priorité."
        ),
        lang="fr",
        poster_url="https://images.igdb.com/zelda.jpg",
        marquee_url=None,
        source_id="igdb",
        extra_meta={"system": "snes", "synopsis_localized": "auto"},
    )
    ss_donor_fr = MediaCandidate(
        id="games_ss_snes_zelda",
        domain="games",
        title="The Legend of Zelda: A Link to the Past",
        year="1991",
        badge="SNES",
        badge_color="#7E57C2",
        subtitle="Aventure • SNES",
        rating="9.6",
        synopsis="Link part sauver la princesse Zelda et le royaume d'Hyrule face à Agahnim.",
        lang="fr",
        poster_url="https://screenscraper.fr/zelda_box.png",
        marquee_url="https://screenscraper.fr/zelda_wheel.png",
        source_id="screenscraper",
        extra_meta={"system": "snes", "synopsis_localized": "1"},
    )
    assert CandidateAggregator.needs_enrichment(igdb_primary, FilterCriteria(domain="games", lang="fr")) is True
    assert agg._merge_donor_into_primary(igdb_primary, ss_donor_fr, "fr") is True
    assert "sauver la princesse Zelda" in igdb_primary.synopsis
    assert igdb_primary.extra_meta["synopsis_localized"] == "1"

    # Case 3: TMDB FR ("Piège de cristal" / original_title="Die Hard") + iTunes US ("Die Hard")
    tmdb_fr = MediaCandidate(
        id="movies_tmdb_562",
        domain="movies",
        title="Piège de cristal",
        year="1988",
        badge="ACTION",
        badge_color="#E53935",
        subtitle="Action • 1988",
        rating="8.2",
        synopsis="John McClane affronte des preneurs d'otages au Nakatomi Plaza.",
        lang="fr",
        poster_url="https://image.tmdb.org/diehard_fr.jpg",
        backdrop_url="https://image.tmdb.org/diehard_bg.jpg",
        video_url=None,
        source_id="tmdb_movies",
        extra_meta={"original_title": "Die Hard", "synopsis_localized": "1"},
    )
    itunes_us = MediaCandidate(
        id="movies_itunes_562",
        domain="movies",
        title="Die Hard",
        year="1988",
        badge="ACTION",
        badge_color="#E53935",
        subtitle="Action • Bruce Willis",
        rating="8.2",
        synopsis=(
            "NYPD cop John McClane visits his estranged wife in Los Angeles when terrorists seize "
            "the Nakatomi Plaza skyscraper during a Christmas Eve party."
        ),
        lang="fr",
        poster_url="https://is1-ssl.mzstatic.com/diehard.jpg",
        marquee_url="https://is1-ssl.mzstatic.com/diehard_wide.jpg",
        video_url="https://video-ssl.itunes.apple.com/diehard.m4v",
        source_id="itunes_movies",
        extra_meta={"original_title": "Die Hard", "synopsis_localized": "0"},
    )
    assert CandidateAggregator._is_compatible_donor(tmdb_fr, itunes_us) is True
    q_sec, _ = CandidateAggregator._build_secondary_lookup(
        tmdb_fr, "itunes_movies", FilterCriteria(domain="movies", lang="fr")
    )
    assert q_sec == "Die Hard"
    assert agg._merge_donor_into_primary(tmdb_fr, itunes_us, "fr") is True
    assert tmdb_fr.title == "Piège de cristal"
    assert tmdb_fr.video_url == "https://video-ssl.itunes.apple.com/diehard.m4v"
    assert "John McClane affronte" in tmdb_fr.synopsis

    # Case 4: Free zero-key translation cascade (GTX fail -> MyMemory success) + genre localization
    class _MockTranslateClient:
        async def get(self, url: str, params: Any = None, timeout: float = 2.2) -> httpx.Response:
            req = httpx.Request("GET", url, params=params)
            if "translate.googleapis.com" in url:
                return httpx.Response(503, text="Service Unavailable", request=req)
            if "api.mymemory.translated.net" in url:
                return httpx.Response(
                    200,
                    json={
                        "responseStatus": 200,
                        "responseData": {
                            "translatedText": "Un jeune chasseur de primes voyage à travers le système solaire avec son équipage."
                        },
                    },
                    request=req,
                )
            return httpx.Response(404, request=req)

    en_anime = MediaCandidate(
        id="anime_anilist_1",
        domain="anime",
        title="Cowboy Bebop",
        year="1998",
        badge="FANTASY",
        badge_color="#00ACC1",
        subtitle="Fantasy • 1998",
        rating="8.8",
        synopsis="A young bounty hunter travels through the solar system with his ragtag crew.",
        lang="fr",
        poster_url="https://s4.anilist.co/bebop.jpg",
        source_id="anilist",
        extra_meta={"synopsis_localized": "1"},  # Simulated upstream English leak!
    )
    assert is_likely_english_synopsis(en_anime.synopsis, "fr") is True
    await ensure_candidate_localized(en_anime, "fr", _MockTranslateClient())
    assert "chasseur de primes" in en_anime.synopsis
    assert en_anime.extra_meta["synopsis_localized"] == "auto"
    assert en_anime.subtitle == "Fantasy / Isekai • 1998"


def test_igdb_discovery_excludes_subgames_and_video_pipeline_falls_back_on_dead_yt_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """
    Verify:
    1. `IgdbGamesAdapter._build_where_clauses(..., for_discovery=True)` enforces `parent_game = null`
       and `is_unofficial_game_release()` rejects embedded sub-games like Kirby's 'The Arena'.
    2. `build_video_assets_worker_task()` retries `ytsearch1:<title> <badge> gameplay trailer` when
       an upstream IGDB `youtube_url` points to a dead/deleted YouTube video ID.
    """
    from app.domain.taxonomy.game_genres import is_unofficial_game_release
    from app.infrastructure.rendering import video_pipeline
    from app.infrastructure.scrapers.games.igdb_adapter import IgdbGamesAdapter

    clauses = IgdbGamesAdapter._build_where_clauses(["snes"], [], [], require_cover=True, for_discovery=True)
    assert "parent_game = null" in clauses
    assert "version_parent = null" in clauses
    assert (
        is_unofficial_game_release(
            "The Arena",
            synopsis="The Arena is a Sub-Game debuting in Kirby Super Star, unlocked after beating the game.",
        )
        is True
    )
    assert is_unofficial_game_release("Kirby Super Star", synopsis="A classic platform game by HAL Laboratory.") is False

    yt_calls: List[str] = []

    def _fake_extract(video_page_url: str, out_mp4: Any, max_duration_s: float = 8.0, max_filesize_mb: int = 60) -> bool:
        yt_calls.append(video_page_url)
        if video_page_url.startswith("ytsearch1:"):
            out_mp4.write_bytes(b"\x00" * 16384)
            return True
        return False

    class _FakeProc:
        def process_video_trailer(self, **kwargs: Any) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(frame_count=120, fps=15, duration_s=8.0, quality_score=88)

    monkeypatch.setattr(video_pipeline, "extract_hd_youtube_clip_ytdlp", _fake_extract)
    monkeypatch.setattr(video_pipeline, "DmdMediaProcessor", _FakeProc)

    res = video_pipeline.build_video_assets_worker_task(
        item_dir=str(tmp_path / "aladdin"),
        title="Disney's Aladdin",
        badge="MEGA DRIVE",
        palette=(0, 120, 255),
        video_url=None,
        youtube_url="https://www.youtube.com/watch?v=5IXhJs8DZt0",
        width=128,
        height=32,
        fps=15,
        max_duration_s=8.0,
    )
    assert res["has_video"] is True
    assert yt_calls == [
        "https://www.youtube.com/watch?v=5IXhJs8DZt0",
        "ytsearch1:Disney's Aladdin MEGA DRIVE gameplay trailer",
    ]


def test_resolve_domain_scene_type_hint_unifies_platformer_and_fighting_and_forwards_to_clip_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """
    Verify Showcase maps 2D platformer, 1v1 fighting, beat 'em up, run & gun, and action genres
    (both canonical slugs and localized subtitle prefixes) to `scene_type='platformer'`, and
    forwards `scene_type` into `DmdClipConfig` during video asset transcoding.
    """
    from app.infrastructure.rendering import video_pipeline
    from app.infrastructure.rendering.video_pipeline import resolve_domain_scene_type_hint

    assert resolve_domain_scene_type_hint("games", "platform") == "platformer"
    assert resolve_domain_scene_type_hint("games", "fighting") == "platformer"
    assert resolve_domain_scene_type_hint("games", "beatemup") == "platformer"
    assert resolve_domain_scene_type_hint("games", "runandgun") == "platformer"
    assert resolve_domain_scene_type_hint("games", "Plateforme • Mega Drive") == "platformer"
    assert resolve_domain_scene_type_hint("games", "Combat (1v1 / Versus) • CPS-2") == "platformer"
    assert resolve_domain_scene_type_hint("games", "shootemup") == "action_horizontal"
    assert resolve_domain_scene_type_hint("games", "maze") == "top_down_isometric"
    assert resolve_domain_scene_type_hint("movies", "action") == ""

    captured_configs: List[Any] = []

    def _fake_extract(video_page_url: str, out_mp4: Any, max_duration_s: float = 8.0, max_filesize_mb: int = 60) -> bool:
        out_mp4.write_bytes(b"\x00" * 16384)
        return True

    class _CapturingProc:
        def process_video_trailer(self, **kwargs: Any) -> Any:
            from types import SimpleNamespace

            captured_configs.append(kwargs.get("config"))
            return SimpleNamespace(frame_count=120, fps=15, duration_s=8.0, quality_score=91)

    monkeypatch.setattr(video_pipeline, "extract_hd_youtube_clip_ytdlp", _fake_extract)
    monkeypatch.setattr(video_pipeline, "DmdMediaProcessor", _CapturingProc)

    res = video_pipeline.build_video_assets_worker_task(
        item_dir=str(tmp_path / "dracula"),
        title="Bram Stoker's Dracula",
        badge="MD",
        palette=(180, 40, 40),
        video_url=None,
        youtube_url="https://www.youtube.com/watch?v=real_clip",
        width=128,
        height=32,
        fps=15,
        max_duration_s=8.0,
        domain="games",
        genre_hint="Plateforme • MD",
    )
    assert res["has_video"] is True
    assert len(captured_configs) == 1
    if "scene_type" in getattr(video_pipeline.DmdClipConfig, "__dataclass_fields__", {}):
        assert captured_configs[0].scene_type == "platformer"





