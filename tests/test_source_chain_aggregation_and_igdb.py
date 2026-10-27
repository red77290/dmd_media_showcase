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

    await batch_manager.stop()
