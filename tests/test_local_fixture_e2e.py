"""
Unit & FastAPI integration tests for offline `LocalFixtureCatalogAdapter` and `/api/v1/e2e/audit`.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.domain.entities import FilterCriteria
from app.domain.ports.media_transcoder import ConversionAbortedError
from app.infrastructure.http.routers.e2e_fixtures import (
    ensure_e2e_scraper_registered,
    reset_e2e_audit,
)
from app.infrastructure.scrapers.local_fixture_adapter import LocalFixtureCatalogAdapter
from app.infrastructure.scrapers.registry import ScraperRegistry
from app.main import app


def _seed_sample_fixtures(root: Path) -> None:
    mslug_dir = root / "mslug"
    mslug_dir.mkdir(parents=True, exist_ok=True)
    (mslug_dir / "poster.png").write_bytes(b"\x89PNG\r\n\x1a\nE2E_POSTER")
    catalog = {
        "items": [
            {
                "id": "e2e_neogeo_mslug",
                "domain": "games",
                "rom": "mslug",
                "aliases": ["mslug", "metal slug", "metalslug"],
                "systems": ["neogeo", "arcade", "mame"],
                "title": "Metal Slug",
                "year": "1996",
                "badge": "NEOGEO",
                "badge_color": "#FF5722",
                "subtitle": "Run & Gun • Nazca",
                "rating": "9.6/10",
                "synopsis": {"fr": "Action arcade mythique.", "en": "Legendary run and gun."},
                "poster_rel": "mslug/poster.png",
                "jacket_rel": "mslug/poster.png",
                "marquee_rel": "mslug/poster.png",
                "theme_palette": [255, 87, 34],
            }
        ]
    }
    (root / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")


@pytest.mark.asyncio
async def test_local_fixture_adapter_search_and_random_lookup(tmp_path: Path) -> None:
    _seed_sample_fixtures(tmp_path)
    adapter = LocalFixtureCatalogAdapter(
        fixtures_dir=tmp_path,
        base_url_resolver=lambda: "http://127.0.0.1:8890/_e2e_fixtures",
        domain="games",
    )
    assert adapter.is_configured() is True

    crit = FilterCriteria(domain="games", lang="en", systems=["neogeo"])
    matches = await adapter.search_candidates("mslug.zip", criteria=crit, limit=4)
    assert len(matches) == 1
    assert matches[0].id == "e2e_neogeo_mslug"
    assert matches[0].title == "Metal Slug"
    assert matches[0].synopsis == "Legendary run and gun."
    assert matches[0].poster_url == "http://127.0.0.1:8890/_e2e_fixtures/mslug/poster.png"
    assert matches[0].marquee_url == "http://127.0.0.1:8890/_e2e_fixtures/mslug/poster.png"

    unseen = await adapter.fetch_random_unseen(criteria=crit, seen_ids=set(), count=1)
    assert len(unseen) == 1
    assert unseen[0].id == "e2e_neogeo_mslug"


def test_e2e_routes_disabled_by_default_and_enabled_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import consume_once_orchestrator as co

    async def _noop_hello(dmd_id: str, payload: dict, detected_ip: str = "") -> dict:
        return {"dmd_id": dmd_id, "width": payload.get("width", 128), "height": payload.get("height", 32)}

    monkeypatch.setattr(co, "handle_client_hello", _noop_hello)
    monkeypatch.setattr(settings, "e2e_fixtures_dir", "")
    client = TestClient(app)
    assert client.get("/api/v1/e2e/audit").status_code == 404

    _seed_sample_fixtures(tmp_path)
    monkeypatch.setattr(settings, "e2e_fixtures_dir", str(tmp_path))
    reset_e2e_audit()
    try:
        reg = ScraperRegistry()
        assert ensure_e2e_scraper_registered(reg) is True
        assert reg.get_chain("games")[0].source_id == "e2e_local_fixtures"

        asset_res = client.get("/_e2e_fixtures/mslug/poster.png")
        assert asset_res.status_code == 200
        assert asset_res.content.startswith(b"\x89PNG")

        # Path traversal must be rejected
        assert client.get("/_e2e_fixtures/../../etc/passwd").status_code == 404

        hello_res = client.post(
            "/api/v1/clients/hello",
            json={"dmd_id": "e2e-dmd", "width": 128, "height": 32},
        )
        assert hello_res.status_code == 200

        audit = client.get("/api/v1/e2e/audit").json()
        assert audit["e2e_enabled"] is True
        assert audit["counts"]["hello_posts"] >= 1
    finally:
        reset_e2e_audit()


@pytest.mark.asyncio
async def test_server_managed_director_survives_conversion_aborted_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: `DELETE /queue` worker cancellation must never kill `ServerManagedDirector`."""
    import asyncio
    from app.main import consume_once_orchestrator as co

    dmd_id = "esp32_director_abort_test"
    calls = 0
    recovered = asyncio.Event()

    async def _fake_prepare(target_dmd_id: str, _prof: dict) -> None:
        nonlocal calls
        if target_dmd_id == dmd_id:
            calls += 1
            if calls == 1:
                raise ConversionAbortedError("Converter worker terminated on DMD cancel")
            recovered.set()
            co.state.server_managed_ids.discard(dmd_id)
        return None

    monkeypatch.setattr(co.director, "prepare_next_playlist_package", _fake_prepare)
    monkeypatch.setattr(
        co.director.profile_store,
        "get_profile",
        lambda did: {"dmd_id": did, "config_source": "server", "supports_local_card": True, "paused": False}
        if did == dmd_id
        else None,
    )
    co.state.server_managed_ids.add(dmd_id)
    co.state.client_online[dmd_id] = True
    try:
        await asyncio.wait_for(co.director.run_server_managed_loop(dmd_id), timeout=3.0)
        assert recovered.is_set()
        assert calls >= 2
    finally:
        co.state.server_managed_ids.discard(dmd_id)
        co.state.client_online.pop(dmd_id, None)
