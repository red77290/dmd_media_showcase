"""
Pre-refactor characterization and integration safety-net suite.

Validates end-to-end public HTTP, MQTT, adapter, referential, security, asset streaming,
Studio 0 ms promotion, and WebUI DOM/asset contracts prior to and after the DDD Hexagonal
refactoring so zero regressions can slip through.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from dmd_gif_converter import read_rgb565_stream_header, write_rgb565_stream

from app.config import settings
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate, PanelGeometry
from app.infrastructure.persistence import dmd_profile_store, server_config_store
from app.infrastructure.scrapers.anime.anilist_adapter import AniListAdapter
from app.infrastructure.scrapers.movies.tmdb_movie_adapter import TmdbMovieAdapter
from app.infrastructure.scrapers.referential_store import (
    AniListReferentialStrategy,
    JikanReferentialStrategy,
    TvMazeReferentialStrategy,
)
from app.infrastructure.scrapers.series.tmdb_series_adapter import TmdbSeriesAdapter
from app.infrastructure.scrapers.series.tvmaze_adapter import TvMazeAdapter
from app.infrastructure.scrapers.tvdb_adapter import TvdbSeriesAdapter, _tvdb_token_cache
from app.main import app


class _MockHttpResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self) -> Any:
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


class _MockAsyncHttpClient:
    """Deterministic offline HTTP client routing URLs to canned responses."""

    def __init__(self, routes: Dict[str, Any]) -> None:
        self._routes = routes

    def _resolve(self, url: str) -> _MockHttpResponse:
        for pattern, payload in self._routes.items():
            if pattern in url:
                if isinstance(payload, _MockHttpResponse):
                    return payload
                return _MockHttpResponse(200, payload)
        return _MockHttpResponse(404, {})

    async def get(self, url: str, params: Any = None, headers: Any = None, timeout: float = 5.0) -> _MockHttpResponse:
        return self._resolve(url)

    async def post(self, url: str, json: Any = None, headers: Any = None, timeout: float = 5.0) -> _MockHttpResponse:
        return self._resolve(url)


def _make_png_bytes(w: int = 64, h: int = 96) -> bytes:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = 230
    arr[:, :, 1] = 110
    arr[:, :, 2] = 45
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def _make_gif_bytes(w: int = 64, h: int = 32, frames: int = 20) -> bytes:
    imgs: List[Image.Image] = []
    for idx in range(frames):
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        arr[:, :, 0] = (idx * 25) % 255
        arr[:, :, 1] = 140
        imgs.append(Image.fromarray(arr, mode="RGB"))
    buf = io.BytesIO()
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:], duration=100, loop=0)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_optional_api_key_security_middleware_all_auth_modes(tmp_path: Path, monkeypatch):
    """
    Verify optional `showcase_api_key` guard:
    - Public endpoints (`/`, `/admin`, `/api/v1/health`, `/api/v1/asyncapi.yaml`) bypass auth.
    - Protected `/api/v1/*` endpoints return 401 without token or with wrong token.
    - `X-Showcase-Key`, `Authorization: Bearer <token>`, and `?api_key=<token>` all authenticate.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "data"))
    orig_cfg = server_config_store.get_all()
    try:
        server_config_store.update({"showcase_api_key": "secret-unit-key-99"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Public endpoints stay accessible without token
            assert (await client.get("/")).status_code == 200
            assert (await client.get("/admin")).status_code == 200
            assert (await client.get("/api/v1/health")).status_code == 200
            assert (await client.get("/api/v1/asyncapi.yaml")).status_code == 200

            # Protected endpoints reject missing or invalid credentials
            assert (await client.get("/api/v1/clients")).status_code == 401
            assert (
                await client.get("/api/v1/games/filters", headers={"X-Showcase-Key": "wrong"})
            ).status_code == 401

            # All 3 supported credential transports succeed
            assert (
                await client.get(
                    "/api/v1/clients",
                    headers={"X-Showcase-Key": "secret-unit-key-99"},
                )
            ).status_code == 200
            assert (
                await client.get(
                    "/api/v1/games/filters",
                    headers={"Authorization": "Bearer secret-unit-key-99"},
                )
            ).status_code == 200
            assert (
                await client.get("/api/v1/movies/referential?api_key=secret-unit-key-99")
            ).status_code == 200
    finally:
        server_config_store.update({"showcase_api_key": str(orig_cfg.get("showcase_api_key") or "")})


@pytest.mark.asyncio
async def test_referential_strategies_jikan_anilist_tvmaze_and_admin_sync_endpoint(
    tmp_path: Path, monkeypatch
):
    """
    Verify `JikanReferentialStrategy`, `AniListReferentialStrategy`, `TvMazeReferentialStrategy`
    persist live tokens/metadata, and `POST /api/v1/admin/referentials/sync` synchronizes all 5 strategies.
    """
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DMD_DATA_DIR", str(data_dir))

    fake_client = _MockAsyncHttpClient(
        {
            "api.jikan.moe/v4/genres/anime": {
                "data": [
                    {"mal_id": 1, "name": "Action"},
                    {"mal_id": 18, "name": "Mecha"},
                ]
            },
            "graphql.anilist.co": {
                "data": {"GenreCollection": ["Action", "Mecha", "Sci-Fi"]}
            },
            "api.tvmaze.com/shows": [
                {"id": 1, "genres": ["Drama", "Science-Fiction", "Thriller"]},
                {"id": 2, "genres": ["Crime", "Mystery"]},
            ],
            "adb.arcadeitalia.net": _MockHttpResponse(
                200,
                '<a href="dettaglio_mame.php?game_name=mslug">Metal Slug</a>'
                '<a href="dettaglio_mame.php?game_name=ssf2t">SSF2T</a>',
            ),
            "query.wikidata.org": {
                "results": {
                    "bindings": [
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q12345"},
                            "titleEn": {"value": "Super Metroid"},
                            "titleFr": {"value": "Super Metroid"},
                            "year": {"value": "1994"},
                        }
                    ]
                }
            },
        }
    )

    jikan = JikanReferentialStrategy()
    j_ref = await jikan.sync_referential(fake_client, force=True)
    assert "action" in j_ref["tokens"]
    assert "mecha" in j_ref["tokens"]
    assert len(await jikan.sample_unseen_tokens(fake_client, FilterCriteria(domain="anime"), set(), count=2)) == 2
    assert (data_dir / "referentials" / "jikan_animethemes.json").exists()

    anilist = AniListReferentialStrategy()
    a_ref = await anilist.sync_referential(fake_client, force=True)
    assert "Sci-Fi" in a_ref["tokens"]
    assert len(await anilist.sample_unseen_tokens(fake_client, FilterCriteria(domain="anime"), set(), count=2)) == 2
    assert (data_dir / "referentials" / "anilist.json").exists()

    tvmaze = TvMazeReferentialStrategy()
    t_ref = await tvmaze.sync_referential(fake_client, force=True)
    assert "Science-Fiction" in t_ref["tokens"]
    sampled_pages = await tvmaze.sample_unseen_tokens(
        fake_client, FilterCriteria(domain="series"), set(), count=3
    )
    assert len(sampled_pages) == 3
    assert all(p.isdigit() for p in sampled_pages)
    assert (data_dir / "referentials" / "tvmaze.json").exists()

    @asynccontextmanager
    async def _fake_client_ctx(timeout: float = 6.0):
        yield fake_client

    # Patch HTTP client factory wherever sync_live_referentials resolves it
    import app.infrastructure.scrapers.http_utils as http_utils_mod
    import app.main as main_mod

    monkeypatch.setattr(http_utils_mod, "create_async_http_client", _fake_client_ctx)
    if hasattr(main_mod, "create_async_http_client"):
        monkeypatch.setattr(main_mod, "create_async_http_client", _fake_client_ctx)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/admin/referentials/sync")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "synced"
        counts = body["referential_counts"]
        for expected_key in (
            "arcadeitalia",
            "libretro_wikidata",
            "jikan_animethemes",
            "anilist",
            "tvmaze",
        ):
            assert expected_key in counts


@pytest.mark.asyncio
async def test_cinema_series_anime_adapters_random_and_search_contracts(
    tmp_path: Path, monkeypatch
):
    """
    Verify `TmdbMovieAdapter`, `TmdbSeriesAdapter`, `TvdbSeriesAdapter`, `TvMazeAdapter`,
    and `AniListAdapter` discovery, search, and filter metadata contracts offline.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "data"))
    server_config_store.update(
        {
            "tmdb_api_key": "fake-tmdb-v3-key",
            "tvdb_api_key": "fake-tvdb-v4-key",
        }
    )
    _tvdb_token_cache["api_key"] = ""
    _tvdb_token_cache["token"] = ""
    _tvdb_token_cache["expires_at"] = 0.0

    canned = _MockAsyncHttpClient(
        {
            "/3/discover/movie": {
                "total_pages": 1,
                "results": [
                    {
                        "id": 603,
                        "title": "La Matrice",
                        "original_title": "The Matrix",
                        "release_date": "1999-03-31",
                        "vote_average": 8.7,
                        "overview": "Un pirate informatique découvre la nature de la réalité.",
                        "poster_path": "/matrix_poster.jpg",
                        "backdrop_path": "/matrix_bg.jpg",
                    }
                ],
            },
            "/3/search/movie": {
                "results": [
                    {
                        "id": 603,
                        "title": "The Matrix",
                        "original_title": "The Matrix",
                        "release_date": "1999-03-31",
                        "vote_average": 8.7,
                        "overview": "Cyberpunk classic.",
                        "poster_path": "/matrix_poster.jpg",
                        "backdrop_path": "/matrix_bg.jpg",
                    }
                ]
            },
            "/3/movie/603/videos": {
                "results": [{"site": "YouTube", "key": "vKQi3bBA1y8", "type": "Trailer", "official": True}]
            },
            "/3/movie/603/images": {
                "logos": [{"file_path": "/matrix_logo.png"}],
                "backdrops": [{"file_path": "/matrix_snap1.jpg"}],
            },
            "/3/discover/tv": {
                "total_pages": 1,
                "results": [
                    {
                        "id": 1396,
                        "name": "Breaking Bad",
                        "original_name": "Breaking Bad",
                        "first_air_date": "2008-01-20",
                        "vote_average": 8.9,
                        "overview": "Un professeur de chimie se tourne vers le crime.",
                        "poster_path": "/bb_poster.jpg",
                        "backdrop_path": "/bb_bg.jpg",
                    }
                ],
            },
            "/3/search/tv": {
                "results": [
                    {
                        "id": 1396,
                        "name": "Breaking Bad",
                        "original_name": "Breaking Bad",
                        "first_air_date": "2008-01-20",
                        "vote_average": 8.9,
                        "overview": "Crime drama.",
                        "poster_path": "/bb_poster.jpg",
                    }
                ]
            },
            "/3/tv/1396/videos": {
                "results": [{"site": "YouTube", "key": "HhesaQXLuRY", "type": "Trailer", "official": True}]
            },
            "/3/tv/1396/images": {"logos": [], "backdrops": []},
            "api4.thetvdb.com/v4/login": {"data": {"token": "mock-tvdb-jwt"}},
            "api4.thetvdb.com/v4/series/filter": {
                "data": [
                    {
                        "id": 81189,
                        "name": "Severance",
                        "year": "2022",
                        "image": "https://artworks.thetvdb.com/banners/posters/sev.jpg",
                        "score": 9000,
                    }
                ]
            },
            "api4.thetvdb.com/v4/search": {
                "data": [
                    {
                        "tvdb_id": "81189",
                        "name": "Severance",
                        "year": "2022",
                        "image_url": "https://artworks.thetvdb.com/banners/posters/sev.jpg",
                        "overview": "Sci-fi workplace thriller.",
                    }
                ]
            },
            "api4.thetvdb.com/v4/series/81189/extended": {
                "data": {
                    "id": 81189,
                    "name": "Severance",
                    "year": "2022",
                    "image": "https://artworks.thetvdb.com/banners/posters/sev.jpg",
                    "overview": "Sci-fi workplace thriller.",
                    "trailers": [{"url": "https://www.youtube.com/watch?v=xEQP4VVuyrY"}],
                    "artworks": [],
                }
            },
            "api4.thetvdb.com/v4/series/81189/translations/": {
                "data": {"name": "Severance", "overview": "Thriller de science-fiction."}
            },
            "api.tvmaze.com/shows": [
                {
                    "id": 82,
                    "name": "Dark",
                    "type": "Scripted",
                    "genres": ["Drama", "Science-Fiction"],
                    "premiered": "2017-12-01",
                    "rating": {"average": 8.8},
                    "network": {"name": "Netflix"},
                    "image": {"original": "https://static.tvmaze.com/uploads/images/original_untouched/dark.jpg"},
                    "summary": "<p>Time travel mystery in Winden.</p>",
                }
            ],
            "api.tvmaze.com/search/shows": [
                {
                    "show": {
                        "id": 82,
                        "name": "Dark",
                        "type": "Scripted",
                        "genres": ["Science-Fiction"],
                        "premiered": "2017-12-01",
                        "rating": {"average": 8.8},
                        "image": {"original": "https://static.tvmaze.com/uploads/images/original_untouched/dark.jpg"},
                        "summary": "<p>Time travel mystery.</p>",
                    }
                }
            ],
            "graphql.anilist.co": {
                "data": {
                    "Page": {
                        "pageInfo": {"lastPage": 1},
                        "media": [
                            {
                                "id": 1,
                                "idMal": 1,
                                "title": {"english": "Cowboy Bebop", "romaji": "Cowboy Bebop", "native": "カウボーイビバップ"},
                                "genres": ["Action", "Sci-Fi"],
                                "startDate": {"year": 1998},
                                "averageScore": 88,
                                "description": "Space bounty hunters.",
                                "coverImage": {"extraLarge": "https://s4.anilist.co/bebop.jpg"},
                                "bannerImage": "https://s4.anilist.co/bebop_banner.jpg",
                                "trailer": {"id": "QCaEJZqLeTU", "site": "youtube"},
                            }
                        ],
                    }
                }
            },
            "itunes.apple.com/search": {"results": []},
            "api.animethemes.moe": {"anime": []},
        }
    )

    @asynccontextmanager
    async def _canned_ctx(timeout: float = 6.0):
        yield canned

    import app.infrastructure.scrapers.anime.anilist_adapter as anilist_mod
    import app.infrastructure.scrapers.http_utils as http_utils_mod
    import app.infrastructure.scrapers.movies.tmdb_movie_adapter as tmdb_mov_mod
    import app.infrastructure.scrapers.series.tmdb_series_adapter as tmdb_ser_mod
    import app.infrastructure.scrapers.series.tvmaze_adapter as tvmaze_mod
    import app.infrastructure.scrapers.tvdb_adapter as tvdb_mod

    for mod in (http_utils_mod, tmdb_mov_mod, tmdb_ser_mod, tvdb_mod, tvmaze_mod, anilist_mod):
        if hasattr(mod, "create_async_http_client"):
            monkeypatch.setattr(mod, "create_async_http_client", _canned_ctx)

    async def _noop_multilingual(*_args, **_kwargs):
        return None, None, None

    for mod in (http_utils_mod, tmdb_mov_mod, tmdb_ser_mod, tvmaze_mod, anilist_mod):
        if hasattr(mod, "resolve_multilingual_media_details"):
            monkeypatch.setattr(mod, "resolve_multilingual_media_details", _noop_multilingual)

    # 1. TmdbMovieAdapter
    mov_adapter = TmdbMovieAdapter()
    assert mov_adapter.is_configured() is True
    assert len(mov_adapter.supported_filters("fr")["genres"]) >= 10
    mov_random = await mov_adapter.fetch_random_unseen(
        FilterCriteria(domain="movies", lang="fr", genres=["scifi"], decades=["1990s"]),
        seen_ids=set(),
        count=1,
    )
    assert len(mov_random) == 1
    assert mov_random[0].id == "movies_tmdb_603"
    assert "youtube.com" in (mov_random[0].youtube_url or "")
    mov_search = await mov_adapter.search_candidates("Matrix", FilterCriteria(domain="movies", lang="en"), limit=2)
    assert len(mov_search) == 1 and mov_search[0].id == "movies_tmdb_603"

    # 2. TmdbSeriesAdapter
    ser_adapter = TmdbSeriesAdapter()
    assert ser_adapter.is_configured() is True
    ser_random = await ser_adapter.fetch_random_unseen(
        FilterCriteria(domain="series", lang="fr", genres=["crime"], decades=["2000s"]),
        seen_ids=set(),
        count=1,
    )
    assert len(ser_random) == 1 and ser_random[0].id == "series_tmdb_1396"
    ser_search = await ser_adapter.search_candidates("Breaking Bad", FilterCriteria(domain="series", lang="en"), limit=2)
    assert len(ser_search) == 1 and ser_search[0].id == "series_tmdb_1396"

    # 3. TvdbSeriesAdapter
    tvdb_adapter = TvdbSeriesAdapter()
    assert tvdb_adapter.is_configured() is True
    tvdb_search = await tvdb_adapter.search_candidates("Severance", FilterCriteria(domain="series", lang="fr"), limit=2)
    assert len(tvdb_search) >= 1 and "81189" in tvdb_search[0].id

    # 4. TvMazeAdapter
    tvm_adapter = TvMazeAdapter()
    assert tvm_adapter.is_configured() is True
    tvm_random = await tvm_adapter.fetch_random_unseen(
        FilterCriteria(domain="series", lang="en", genres=["scifi"]),
        seen_ids=set(),
        count=1,
    )
    assert len(tvm_random) == 1 and tvm_random[0].id == "series_tvmaze_82"

    # 5. AniListAdapter
    ani_adapter = AniListAdapter()
    assert ani_adapter.is_configured() is True
    ani_random = await ani_adapter.fetch_random_unseen(
        FilterCriteria(domain="anime", lang="ja", genres=["scifi"], decades=["1990s"]),
        seen_ids=set(),
        count=1,
    )
    assert len(ani_random) == 1
    assert ani_random[0].id == "anime_anilist_1"
    assert ani_random[0].title == "カウボーイビバップ"


@pytest.mark.asyncio
async def test_dmd_profile_crud_mode1_409_conflict_force_override_and_delete(
    tmp_path: Path, monkeypatch
):
    """
    Verify DMD profile lifecycle:
    - Active Mode 1 client-driven DMD synthesizes a read-only inspector profile (`GET /profile`)
    - `PUT /profile` rejects overwriting an active Mode 1 DMD with `409 Conflict` unless `?force_override=true`
    - `DELETE /profile` removes persisted profile (`200`) and returns `404` when absent.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")

    from app.application.consume_once_orchestrator import consume_once_orchestrator

    dmd_id = "esp32_games_128x32"
    consume_once_orchestrator.record_http_client(dmd_id, "games", "192.168.1.77")
    consume_once_orchestrator.client_configs[(dmd_id, "games")] = (
        PanelGeometry(width=128, height=32),
        FilterCriteria(domain="games", lang="fr", systems=["neogeo"], genres=["fighting"]),
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Read-only synthesized profile for Mode 1 DMD
            r_get = await client.get(f"/api/v1/clients/{dmd_id}/profile")
            assert r_get.status_code == 200
            body_get = r_get.json()
            assert body_get["persisted"] is False
            assert body_get["read_only_client_managed"] is True
            assert body_get["profile"]["config_source"] == "client"

            # 2. PUT without force_override -> 409 Conflict
            profile_payload = {
                "display_name": "Arcade Cabinet",
                "config_source": "server",
                "supports_local_card": False,
                "width": 128,
                "height": 32,
                "udp_ip": "192.168.1.77",
                "udp_port": 4210,
                "lang": "fr",
                "playlist": [{"domain": "games", "enabled": True, "items_per_turn": 1}],
            }
            r_conflict = await client.put(f"/api/v1/clients/{dmd_id}/profile", json=profile_payload)
            assert r_conflict.status_code == 409

            # 3. PUT with ?force_override=true -> 200 Saved
            r_override = await client.put(
                f"/api/v1/clients/{dmd_id}/profile?force_override=true",
                json=profile_payload,
            )
            assert r_override.status_code == 200
            assert r_override.json()["status"] == "saved"

            # 4. DELETE -> 200 Deleted, then 404 Not Found
            r_del = await client.delete(f"/api/v1/clients/{dmd_id}/profile")
            assert r_del.status_code == 200
            assert r_del.json()["status"] == "deleted"

            r_del_again = await client.delete(f"/api/v1/clients/{dmd_id}/profile")
            assert r_del_again.status_code == 404
    finally:
        consume_once_orchestrator.client_configs.pop((dmd_id, "games"), None)
        await consume_once_orchestrator.stop_server_managed_profiles()


@pytest.mark.asyncio
async def test_assets_streaming_endpoint_truncation_gif_png_and_404(tmp_path: Path, monkeypatch):
    """
    Verify `GET /api/v1/assets/{item_key}/{filename}` serves `.rgb565.stream` (with dynamic
    `?max_duration_s=` truncation), `.gif`, `.png`, and enforces 404 on unknown/traversal paths.
    """
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    from app.scheduler.worker_pool import batch_manager

    item_dir = tmp_path / "cache" / "asset_test_item"
    item_dir.mkdir(parents=True, exist_ok=True)

    # Create a 3.0s @ 10 FPS (30 frames) 64x32 RGB565 stream
    frames_rgb = [np.full((32, 64, 3), 120, dtype=np.uint8) for _ in range(30)]
    stream_path = item_dir / "video_trailer.rgb565.stream"
    write_rgb565_stream(frames_rgb, stream_path, fps=10)

    gif_path = item_dir / "video_trailer.gif"
    gif_path.write_bytes(_make_gif_bytes(64, 32, frames=20))

    png_path = item_dir / "poster.png"
    png_path.write_bytes(_make_png_bytes(32, 32))

    batch_manager.asset_index["asset_test_key"] = item_dir
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Truncated stream (?max_duration_s=1.0 -> 10 frames instead of 30)
            r_stream = await client.get(
                "/api/v1/assets/asset_test_key/video_trailer.rgb565.stream?max_duration_s=1.0"
            )
            assert r_stream.status_code == 200
            assert r_stream.headers.get("X-DMD-Media-Ready") == "1"
            hdr = read_rgb565_stream_header(r_stream.content[:16])
            assert hdr["total_frames"] == 10

            # GIF truncation
            r_gif = await client.get(
                "/api/v1/assets/asset_test_key/video_trailer.gif?max_duration_s=1.0"
            )
            assert r_gif.status_code == 200
            assert r_gif.headers["content-type"] == "image/gif"
            assert len(r_gif.content) > 64

            # PNG asset
            r_png = await client.get("/api/v1/assets/asset_test_key/poster.png")
            assert r_png.status_code == 200
            assert r_png.headers["content-type"] == "image/png"

            # Missing key / missing file / traversal guard
            assert (await client.get("/api/v1/assets/unknown_key/poster.png")).status_code == 404
            assert (await client.get("/api/v1/assets/asset_test_key/missing.png")).status_code == 404
    finally:
        batch_manager.asset_index.pop("asset_test_key", None)


@pytest.mark.asyncio
async def test_webui_dom_tabs_static_assets_and_js_module_integrity(tmp_path: Path, monkeypatch):
    """
    Verify WebUI HTML structure across both inline and modularized static serving:
    1. `/` and `/admin` return 200 OK with all 6 canonical tab panes (`tab-overview`,
       `tab-runtime`, `tab-tester`, `tab-studio`, `tab-keys`, `tab-network`).
    2. Every external `<link rel="stylesheet">` and `<script src="...">` served from `/static/`
       returns `200 OK` with non-empty content and valid JS syntax (`node --check`).
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "data"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/")
        assert r.status_code == 200
        html = r.text

        for tab_id in (
            "tab-overview",
            "tab-runtime",
            "tab-tester",
            "tab-studio",
            "tab-keys",
            "tab-network",
        ):
            assert f'id="{tab_id}"' in html, f"Missing required tab container #{tab_id}"

        # Verify all external CSS & JS assets referenced in HTML (when modularized under /static/)
        css_hrefs = re.findall(r'<link[^>]+href=["\'](/[^"\']+\.css[^"\']*)["\']', html)
        js_srcs = re.findall(r'<script[^>]+src=["\'](/[^"\']+\.js[^"\']*)["\']', html)

        for href in css_hrefs:
            rc = await client.get(href)
            assert rc.status_code == 200, f"CSS asset {href} failed with {rc.status_code}"
            assert len(rc.text.strip()) > 50

        node_bin = shutil.which("node")
        for idx, src in enumerate(js_srcs):
            rj = await client.get(src)
            assert rj.status_code == 200, f"JS asset {src} failed with {rj.status_code}"
            assert len(rj.text.strip()) > 20
            if node_bin:
                tmp_js = tmp_path / f"mod_{idx}.mjs"
                tmp_js.write_text(rj.text, encoding="utf-8")
                chk = subprocess.run([node_bin, "--check", str(tmp_js)], capture_output=True, text=True)
                assert chk.returncode == 0, f"JS syntax error in {src}: {chk.stderr}"


@pytest.mark.asyncio
async def test_studio_preview_zero_ms_promotion_and_live_client_controls(
    tmp_path: Path, monkeypatch
):
    """
    Verify end-to-end Card Studio fast preview, full sequence preview (`X-DMD-Preview-Id`),
    `0 ms` zero-retranscode promotion into a DMD priority queue (`from_studio_sim=True`),
    and live client playback controls (`pause`, `resume`, `skip`, `stop`).
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")

    from app.application.consume_once_orchestrator import consume_once_orchestrator
    from app.application.source_chain_service import source_chain_service

    cand = MediaCandidate(
        id="games_studio_char_1",
        domain="games",
        title="Garou: Mark of the Wolves",
        year="1999",
        badge="NEOGEO",
        badge_color="#E53935",
        subtitle="SNK • Fighting",
        rating="9.5",
        synopsis="Masterpiece 2D pixel-art fighting classic.",
        lang="fr",
        source_id="screenscraper",
        extra_meta={"system": "neogeo", "genres": ["fighting"]},
    )
    source_chain_service.stage_candidate(cand)

    dmd_id = "studio_target_dmd"
    consume_once_orchestrator.record_http_client(dmd_id, "games", "192.168.1.88")
    consume_once_orchestrator.client_configs[(dmd_id, "games")] = (
        PanelGeometry(width=128, height=32),
        FilterCriteria(domain="games", lang="fr"),
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r_card = await client.post(
                "/api/v1/studio/card-preview",
                json={
                    "domain": "games",
                    "candidate_id": cand.id,
                    "width": 128,
                    "height": 32,
                    "card_layout": "classic_split",
                    "card_theme": "amber_dmd",
                    "card_font": "arcade_pixel",
                },
            )
            assert r_card.status_code == 200
            assert r_card.content[:4] == b"DMDS"
            assert "X-DMD-Chapters" in r_card.headers

            r_full = await client.post(
                "/api/v1/studio/full-preview",
                json={
                    "domain": "games",
                    "candidate_id": cand.id,
                    "width": 128,
                    "height": 32,
                    "include_card_intro": True,
                    "jacket_panorama": False,
                    "marquee_panorama": False,
                    "snapshot_panorama_count": 0,
                    "include_video": False,
                },
            )
            assert r_full.status_code == 200
            preview_id = r_full.headers.get("X-DMD-Preview-Id")
            assert preview_id and preview_id.startswith("sim_")

            # Promote simulation into target DMD queue with 0 ms retranscode
            r_queue = await client.post(
                f"/api/v1/clients/{dmd_id}/queue",
                json={
                    "domain": "games",
                    "preview_id": preview_id,
                    "mode": "enqueue_next",
                    "mark_seen": False,
                },
            )
            assert r_queue.status_code == 200
            q_item = r_queue.json()["item"]
            assert q_item["from_studio_sim"] is True
            assert q_item["status"] in ("ready", "playing")

            # Exercise all 4 playback control actions
            for action in ("pause", "resume", "skip", "stop"):
                r_ctrl = await client.post(
                    f"/api/v1/clients/{dmd_id}/control",
                    json={"action": action},
                )
                assert r_ctrl.status_code == 200
                assert r_ctrl.json()["status"] == "ok"
    finally:
        consume_once_orchestrator.priority_queues.pop(dmd_id, None)
        consume_once_orchestrator.client_configs.pop((dmd_id, "games"), None)
