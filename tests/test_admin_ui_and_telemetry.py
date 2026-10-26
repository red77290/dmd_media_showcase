"""
Automated verification suite for the Centralized Web Admin Dashboard (`/`, `/admin`)
and Live Showcase Telemetry (`/api/v1/health`, `/api/v1/admin/config`).

Covers:
1. HTML/CSS form control alignment (`input`, `select`, dark `option`, `.input-row`, `.form-grid-*`),
   embedded JavaScript syntax (`node --check`), and 100% `I18N` (`en`, `fr`, `es`) key parity.
2. Physical DMD board deduplication (`canonical_dmd_board_key`) across multi-engine `ArcadeMatrix`
   ESP32 and `ArcadeMatrix_RPi` clients, Docker bridge NAT gateways, and MQTT reconnect suffixes.
3. 4-domain normalization (`games`, `movies`, `series`, `anime`) for `seen_history` and DDD
   `[Primary, Fallback]` scraper chains even when `seen_history.json` is empty.
4. End-to-end live telemetry transitions: Ready (N) & Standby (N+1) pre-warmed slots, active
   claim/playback (`is_playing`, `playing_title`, `active_broadcasts`), real-time UDP streaming
   (`active_udp_streams`, `udp_streaming`), `stop_stream` cleanup (`last_played_title`), and
   `webui_tester` / internal `dmd_media_showcase_server` exclusion.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
import io

from app.application.consume_once_orchestrator import (
    ConsumeOnceOrchestrator,
    canonical_dmd_board_key,
    consume_once_orchestrator,
    strip_mqtt_gen_suffix,
)
from app.application.update_checker import (
    ReleaseUpdateChecker,
    detect_runtime_platform,
    is_newer_release,
    parse_semver_key,
    release_update_checker,
    select_os_release_asset,
)
from app.config import settings
from app.domain.entities import FilterCriteria, MediaCandidate
from app.infrastructure.mqtt.embedded_broker import EmbeddedMqttBroker, _ClientSession
from app.infrastructure.mqtt.showcase_mqtt_gateway import showcase_mqtt_gateway
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.web_admin import ADMIN_DASHBOARD_HTML
from app.main import _session_client_meta, app
from app.providers.base import RawMediaCandidate
from app.scheduler.worker_pool import batch_manager


def _make_sample_png_bytes() -> bytes:
    arr = np.zeros((32, 32, 3), dtype=np.uint8)
    arr[:, :, 0] = 220
    arr[:, :, 2] = 90
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _cleanup_worker_pool_between_tests():
    """Ensure global `batch_manager` workers are cleanly stopped after every test."""
    yield
    if batch_manager._running or batch_manager._worker_tasks:
        cleanup_loop = asyncio.new_event_loop()
        try:
            cleanup_loop.run_until_complete(batch_manager.stop())
        finally:
            cleanup_loop.close()


@pytest.mark.asyncio
async def test_admin_dashboard_html_css_alignment_and_js_syntax(tmp_path: Path, monkeypatch):
    """
    Verify `/` and `/admin` serve valid HTML, strict 42px dark form control CSS rules,
    syntactically valid JavaScript (`node --check`), and 100% EN/FR/ES I18N parity.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "data"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_root = await client.get("/")
        r_admin = await client.get("/admin")
        assert r_root.status_code == 200
        assert r_admin.status_code == 200
        assert r_root.text == r_admin.text

    html = ADMIN_DASHBOARD_HTML
    assert '<html lang="en">' in html
    assert "color-scheme: dark;" in html

    # Verify cross-browser select/input alignment & Windows Chrome/Edge dark <option> styling
    assert "select option, option" in html
    assert "background-color: #131924;" in html
    assert "height: 42px;" in html
    assert "align-items: end;" in html
    assert 'class="form-actions"' in html

    # Ensure #test-system-wrap is never hidden with display:none (prevents 3-column grid shift)
    script_match = re.search(r"<script>([\s\S]*?)</script>", html)
    assert script_match is not None, "Embedded <script> block missing from ADMIN_DASHBOARD_HTML"
    js_code = script_match.group(1)
    assert "sysWrap.style.display" not in js_code
    assert "sysEl.disabled = !isGames;" in js_code
    assert "X-Showcase-Key" in js_code
    assert "isMaskedSecret" in js_code

    # Extract & verify JavaScript syntax + I18N key parity via node when available
    node_bin = shutil.which("node")
    if node_bin:
        js_file = tmp_path / "admin_dashboard.js"
        js_file.write_text(js_code, encoding="utf-8")
        chk = subprocess.run([node_bin, "--check", str(js_file)], capture_output=True, text=True)
        assert chk.returncode == 0, f"JavaScript syntax error in web_admin.py: {chk.stderr}"

        i18n_prefix = js_code.split("const CANONICAL_DOMAINS")[0]
        eval_file = tmp_path / "eval_i18n.js"
        eval_file.write_text(
            i18n_prefix
            + "\nconsole.log(JSON.stringify({"
            + "en: Object.keys(I18N.en).sort(),"
            + "fr: Object.keys(I18N.fr).sort(),"
            + "es: Object.keys(I18N.es).sort()"
            + "}));\n",
            encoding="utf-8",
        )
        res = subprocess.run([node_bin, str(eval_file)], capture_output=True, text=True, check=True)
        parsed = json.loads(res.stdout)
        en_keys = set(parsed["en"])
        fr_keys = set(parsed["fr"])
        es_keys = set(parsed["es"])
        assert en_keys == fr_keys, f"I18N EN vs FR key mismatch: {en_keys ^ fr_keys}"
        assert en_keys == es_keys, f"I18N EN vs ES key mismatch: {en_keys ^ es_keys}"

        html_i18n_attrs = set(re.findall(r'data-i18n="([^"]+)"', html))
        html_ph_attrs = set(re.findall(r'data-i18n-placeholder="([^"]+)"', html))
        js_t_calls = set(re.findall(r"\bt\('([^']+)'", js_code))
        missing = (html_i18n_attrs | html_ph_attrs | js_t_calls) - en_keys
        assert not missing, f"Missing I18N translation keys: {missing}"


def test_canonical_dmd_board_key_deduplicates_multi_engine_boards():
    """
    Verify per-engine `dmd_id` conventions (`rpi_{domain}_{W}x{H}`, `esp32_{domain}_{W}x{H}`,
    plus MQTT reconnect generation suffixes `_{gen}`) resolve to physical DMD boards.
    """
    assert strip_mqtt_gen_suffix("rpi_games_128x32_14") == "rpi_games_128x32"
    assert strip_mqtt_gen_suffix("esp32_movies_128x64_2") == "esp32_movies_128x64"
    assert strip_mqtt_gen_suffix("rpi_anime_128x32") == "rpi_anime_128x32"
    assert strip_mqtt_gen_suffix("cab_1") == "cab_1"

    # 1 physical Raspberry Pi running all 4 Showcase engines on LAN IP 192.168.1.177
    lan_keys = {
        canonical_dmd_board_key(f"rpi_{dom}_128x32", "192.168.1.177")
        for dom in ("games", "movies", "series", "anime")
    }
    assert lan_keys == {"ip:192.168.1.177"}

    # Same 4 engines behind Docker bridge NAT gateway (172.18.0.1) or loopback (127.0.0.1)
    nat_rpi_keys = {
        canonical_dmd_board_key(f"rpi_{dom}_128x32", "172.18.0.1")
        for dom in ("games", "movies", "series", "anime")
    }
    nat_esp_keys = {
        canonical_dmd_board_key(f"esp32_{dom}_128x32", "172.18.0.1")
        for dom in ("games", "movies", "series", "anime")
    }
    assert nat_rpi_keys == {"board:rpi_128x32"}
    assert nat_esp_keys == {"board:esp32_128x32"}
    assert nat_rpi_keys != nat_esp_keys

    # Note: 172.16.5.42 (non-.1 host) is a valid private LAN address, not a Docker .1 gateway
    assert canonical_dmd_board_key("custom_panel", "172.16.5.42") == "ip:172.16.5.42"
    assert canonical_dmd_board_key("custom_panel", "127.0.0.1") == "id:custom_panel"


@pytest.mark.asyncio
async def test_health_and_admin_config_four_domains_and_empty_seen_normalization(
    tmp_path: Path, monkeypatch
):
    """
    Verify `/api/v1/health`, `/api/v1/admin/config`, and `/api/v1/admin/history/reset`
    always return all 4 canonical domains (`games`, `movies`, `series`, `anime`) in
    `seen_history` and `providers` even when `seen_history.json` does not exist yet.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "empty_data"))
    fresh_seen = JsonSeenHistoryRepository(file_path=tmp_path / "empty_data" / "seen_history.json")
    monkeypatch.setattr("app.main.seen_repository", fresh_seen)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_health = await client.get("/api/v1/health")
        assert r_health.status_code == 200
        h = r_health.json()
        assert h["seen_history_counts"] == {
            "games": 0,
            "movies": 0,
            "series": 0,
            "anime": 0,
        }
        assert list(h["providers_configured"].keys()) == ["games", "movies", "series", "anime"]
        assert list(h["providers_detailed"].keys()) == ["games", "movies", "series", "anime"]
        for dom in ("games", "movies", "series", "anime"):
            chain = h["providers_detailed"][dom]
            assert isinstance(chain, list) and len(chain) >= 2
            assert {entry["role"] for entry in chain} == {"primary", "fallback"}
            for entry in chain:
                assert "source_id" in entry and isinstance(entry["configured"], bool)

        r_cfg = await client.get("/api/v1/admin/config")
        assert r_cfg.status_code == 200
        cfg = r_cfg.json()
        assert cfg["seen_history"] == {"games": 0, "movies": 0, "series": 0, "anime": 0}
        assert list(cfg["providers"].keys()) == ["games", "movies", "series", "anime"]

        r_rst = await client.post("/api/v1/admin/history/reset")
        assert r_rst.status_code == 200
        assert r_rst.json()["seen_history"] == {
            "games": 0,
            "movies": 0,
            "series": 0,
            "anime": 0,
        }


@pytest.mark.asyncio
async def test_live_telemetry_multi_engine_board_slots_playback_and_eof_lifecycle(
    tmp_path: Path, monkeypatch
):
    """
    End-to-end verification of live Showcase telemetry when 1 physical RPi (4 Showcase engines
    over MQTT+UDP at 192.168.1.177) and 1 physical ESP32 (HTTP Consume-Once at 192.168.1.188)
    are active simultaneously alongside internal `dmd_media_showcase_server` and `webui_tester`.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "live_data"))
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "live_cache")
    fresh_seen = JsonSeenHistoryRepository(file_path=tmp_path / "live_data" / "seen_history.json")
    monkeypatch.setattr("app.main.seen_repository", fresh_seen)

    seq: Dict[str, int] = {"n": 0}

    class _FastFakeChain:
        async def acquire_unseen_candidates(
            self, criteria: FilterCriteria, count: int = 1, extra_exclude_ids=None
        ) -> List[MediaCandidate]:
            seq["n"] += 1
            idx = seq["n"]
            cand_id = f"{criteria.domain}_cand_{idx}"
            fresh_seen.mark_consumed(criteria.domain, cand_id)
            return [
                MediaCandidate(
                    id=cand_id,
                    domain=criteria.domain,
                    title=f"{criteria.domain.title()} Hit #{idx}",
                    year="1998",
                    badge=criteria.domain.upper(),
                    badge_color="#FF6D00",
                    subtitle="Live Telemetry Verification",
                    rating="9.1",
                    synopsis="Synthetic candidate for telemetry verification.",
                    lang=criteria.lang,
                    source_id="arcadeitalia",
                )
            ]

    class _FastFakeTranscoder:
        async def transcode_candidate(self, cand, criteria, geometry, item_dir: Path):
            await asyncio.sleep(0.01)
            item_dir.mkdir(parents=True, exist_ok=True)
            stream_p = item_dir / f"unified_{geometry.width}x{geometry.height}.rgb565.stream"
            stream_p.write_bytes(b"DMDS" + b"\x00" * 28)
            pw, ph = geometry.resolved_poster_dims()
            return {
                "poster_width": pw,
                "poster_height": ph,
                "poster_rgb565_b64": "AA==",
                "has_panoramas": True,
                "has_video": True,
                "unified_stream_path": str(stream_p),
                "stream_frames": 90,
                "stream_fps": 15,
                "stream_duration_s": 6.0,
            }

    class _ControllableFakeStreamer:
        def __init__(self) -> None:
            self._active: Dict[str, Dict[str, object]] = {}

        @property
        def active_streams_count(self) -> int:
            return len(self._active)

        def get_active_stream(self, dmd_id: str):
            return self._active.get(dmd_id)

        def describe_active_streams(self) -> Dict[str, Dict[str, object]]:
            return dict(self._active)

        async def start_udp_clock_stream(self, **kwargs) -> bool:
            self._active[kwargs["dmd_id"]] = {
                "item_id": kwargs["item_id"],
                "udp_ip": kwargs.get("udp_ip", "127.0.0.1"),
                "udp_port": kwargs.get("udp_port", 4210),
                "fps": kwargs.get("fps", 15),
            }
            return True

        async def stop_stream(self, dmd_id: str) -> None:
            self._active.pop(dmd_id, None)

    class _NoopMqttGateway:
        def set_handlers(self, **_kwargs) -> None:
            pass

        async def publish_json(self, _topic: str, _payload: Dict[str, object], retain: bool = False) -> None:
            pass

    fake_streamer = _ControllableFakeStreamer()
    isolated_orch = ConsumeOnceOrchestrator(
        chain_service=_FastFakeChain(),  # type: ignore[arg-type]
        seen_repo=fresh_seen,
        transcoder=_FastFakeTranscoder(),  # type: ignore[arg-type]
        streamer=fake_streamer,  # type: ignore[arg-type]
        mqtt_gateway=_NoopMqttGateway(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("app.main.consume_once_orchestrator", isolated_orch)

    # Set up mock EmbeddedMqttBroker with internal server session + 4 RPi per-engine TCP sessions
    fake_broker = EmbeddedMqttBroker(host="127.0.0.1", port=1883)
    fake_broker.clients["dmd_media_showcase_server"] = _ClientSession(
        client_id="dmd_media_showcase_server", writer=None, peer_ip="127.0.0.1"  # type: ignore[arg-type]
    )
    for idx, dom in enumerate(("games", "movies", "series", "anime")):
        cid = f"rpi_{dom}_128x32_{idx + 1}"
        fake_broker.clients[cid] = _ClientSession(
            client_id=cid, writer=None, peer_ip="192.168.1.177"  # type: ignore[arg-type]
        )
    monkeypatch.setattr(showcase_mqtt_gateway, "embedded_broker", fake_broker)

    old_sessions = dict(batch_manager.sessions)
    old_meta = dict(_session_client_meta)
    batch_manager.sessions.clear()
    _session_client_meta.clear()

    try:
        # 1. Register 4 Showcase engines for 1 physical RPi (192.168.1.177) via MQTT config
        for idx, dom in enumerate(("games", "movies", "series", "anime")):
            dmd_id = f"rpi_{dom}_128x32"
            await isolated_orch.handle_mqtt_client_config(
                dmd_id,
                {
                    "dmd_id": dmd_id,
                    "client_ip": "192.168.1.177",
                    "udp_ip": "192.168.1.177",
                    "_peer_ip": "192.168.1.177",
                    "udp_port": 4210 + idx,
                    "width": 128,
                    "height": 32,
                    "video_mode": "auto_action",
                    "color_boost": "auto",
                    "domains": {dom: {"lang": "fr"}},
                },
            )

        # 2. Register 1 physical ESP32 (192.168.1.188) via HTTP Consume-Once config
        await isolated_orch.handle_mqtt_client_config(
            "esp32_games_128x32",
            {
                "dmd_id": "esp32_games_128x32",
                "client_ip": "192.168.1.188",
                "_peer_ip": "192.168.1.188",
                "width": 128,
                "height": 32,
                "video_mode": "panorama",
                "color_boost": "pixel_art",
                "domains": {"games": {"lang": "fr"}},
            },
        )
        isolated_orch.client_transports[("esp32_games_128x32", "games")] = "HTTP"

        # Wait until all 5 slots have both Ready (N) and Standby (N+1) packages prepared
        for _ in range(80):
            if isolated_orch.ready_slots_count >= 5 and isolated_orch.standby_slots_count >= 5:
                break
            await asyncio.sleep(0.02)

        assert isolated_orch.ready_slots_count == 5
        assert isolated_orch.standby_slots_count == 5

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify steady-state pre-warmed telemetry BEFORE claiming
            h1 = (await client.get("/api/v1/health")).json()
            assert h1["mqtt_connected_clients"] == 2, (
                f"Expected 2 physical DMD boards (1 RPi + 1 ESP32), got {h1['mqtt_connected_clients']}"
            )
            assert h1["mqtt_tcp_sessions"] == 4
            assert h1["registered_dmd_slots"] == 5
            assert h1["ready_slots"] == 5
            assert h1["standby_slots"] == 5
            assert h1["completed_jobs_total"] >= 10
            assert h1["avg_job_duration_s"] >= 0.0
            assert h1["active_broadcasts"] == 0
            assert sum(h1["seen_history_counts"].values()) >= 10

            # 3. Claim Slot N on esp32_games_128x32 AND start UDP stream on rpi_movies_128x32 (2 boards playing!)
            claimed_games = await isolated_orch.claim_next_package("esp32_games_128x32", "games")
            assert claimed_games is not None
            await isolated_orch.handle_mqtt_client_event(
                "rpi_movies_128x32",
                {
                    "event": "start_stream",
                    "domain": "movies",
                    "udp_ip": "192.168.1.177",
                    "_peer_ip": "192.168.1.177",
                    "udp_port": 4211,
                },
            )

            h2 = (await client.get("/api/v1/health")).json()
            assert h2["active_udp_streams"] == 1
            assert h2["active_broadcasts"] == 2

            cfg2 = (await client.get("/api/v1/admin/config")).json()
            by_key = {(c["dmd_id"], c["domain"]): c for c in cfg2["clients"]}
            assert len(by_key) == 5

            games_row = by_key[("esp32_games_128x32", "games")]
            assert games_row["is_playing"] is True
            assert games_row["playing_title"] == claimed_games.candidate.title
            assert games_row["ready_title"] is not None  # Standby promoted immediately to Ready!

            movies_row = by_key[("rpi_movies_128x32", "movies")]
            assert movies_row["udp_streaming"] is True
            assert movies_row["is_playing"] is True
            assert movies_row["playing_title"] is not None
            saved_movies_title = movies_row["playing_title"]

            # 4. Stop both streams -> active playback resets to 0 while historical last_played_title remains
            await isolated_orch.handle_mqtt_client_event(
                "esp32_games_128x32",
                {"event": "stop_stream", "domain": "games", "item_id": claimed_games.item_id},
            )
            await isolated_orch.handle_mqtt_client_event(
                "rpi_movies_128x32",
                {"event": "stop_stream", "domain": "movies"},
            )

            h3 = (await client.get("/api/v1/health")).json()
            assert h3["active_udp_streams"] == 0
            assert h3["active_broadcasts"] == 0

            cfg3 = (await client.get("/api/v1/admin/config")).json()
            by_key3 = {(c["dmd_id"], c["domain"]): c for c in cfg3["clients"]}
            assert by_key3[("esp32_games_128x32", "games")]["is_playing"] is False
            assert by_key3[("esp32_games_128x32", "games")]["playing_title"] is None
            assert by_key3[("esp32_games_128x32", "games")]["last_played_title"] == claimed_games.candidate.title
            assert by_key3[("rpi_movies_128x32", "movies")]["udp_streaming"] is False
            assert by_key3[("rpi_movies_128x32", "movies")]["is_playing"] is False
            assert by_key3[("rpi_movies_128x32", "movies")]["last_played_title"] == saved_movies_title
    finally:
        for t_task in list(isolated_orch._prep_tasks.values()):
            t_task.cancel()
        batch_manager.sessions.clear()
        batch_manager.sessions.update(old_sessions)
        _session_client_meta.clear()
        _session_client_meta.update(old_meta)


@pytest.mark.asyncio
async def test_static_http_session_records_completed_jobs_and_excludes_webui_tester(
    tmp_path: Path, monkeypatch
):
    """
    Verify static (`include_video=False`) HTTP polling sessions increment `completed_jobs_total`
    and appear in `/api/v1/admin/config` for real DMD clients while excluding `webui_tester`.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "static_data"))
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "static_cache")
    sample_png = _make_sample_png_bytes()
    monkeypatch.setattr(
        "app.scheduler.asset_builder._download_bytes",
        lambda url, timeout=5.0: sample_png if url else None,
    )

    async def _fake_fetch_random(req, exclude_ids=None):
        return [
            RawMediaCandidate(
                id="games_static_sf2",
                domain="games",
                title="Street Fighter II",
                year="1991",
                badge="ARCADE",
                badge_color="#FF6D00",
                subtitle="Fighting • Capcom",
                rating="9.5",
                synopsis="World Warrior arcade classic.",
                lang=req.lang,
                poster_url="https://example.org/sf2.png",
                source_used="arcadeitalia",
            )
        ]

    monkeypatch.setattr(batch_manager.providers["games"], "fetch_random_candidates", _fake_fetch_random)

    old_sessions = dict(batch_manager.sessions)
    old_meta = dict(_session_client_meta)
    old_completed = consume_once_orchestrator._completed_jobs_total
    old_duration = consume_once_orchestrator._total_job_duration_s
    batch_manager.sessions.clear()
    _session_client_meta.clear()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            h_before = (await client.get("/api/v1/health")).json()
            jobs_before = h_before["completed_jobs_total"]

            # 1. WebUI Tester random preview -> must NOT count as a connected DMD client!
            r_tester = await client.get(
                "/api/v1/games/random",
                params={"client_id": "webui_tester", "count": 1, "include_video": "false"},
            )
            assert r_tester.status_code == 200

            h_after_tester = (await client.get("/api/v1/health")).json()
            cfg_after_tester = (await client.get("/api/v1/admin/config")).json()
            assert all(c["dmd_id"] != "webui_tester" for c in cfg_after_tester["clients"])
            assert h_after_tester["registered_dmd_slots"] == h_before["registered_dmd_slots"]

            # 2. Real ESP32 static HTTP session -> MUST increment completed_jobs_total & register slot!
            r_esp = await client.post(
                "/api/v1/games/session",
                json={
                    "client_id": "esp32_arcade_cab",
                    "count": 1,
                    "width": 128,
                    "height": 32,
                    "include_video": False,
                },
            )
            assert r_esp.status_code == 200

            h_after_esp = (await client.get("/api/v1/health")).json()
            assert h_after_esp["completed_jobs_total"] >= jobs_before + 1
            assert h_after_esp["registered_dmd_slots"] >= h_before["registered_dmd_slots"] + 1

            cfg_after_esp = (await client.get("/api/v1/admin/config")).json()
            esp_rows = [c for c in cfg_after_esp["clients"] if c["dmd_id"] == "esp32_arcade_cab"]
            assert len(esp_rows) == 1
            assert esp_rows[0]["transport"] == "HTTP"
            assert esp_rows[0]["ready_title"] == "Street Fighter II"
    finally:
        batch_manager.sessions.clear()
        batch_manager.sessions.update(old_sessions)
        _session_client_meta.clear()
        _session_client_meta.update(old_meta)
        consume_once_orchestrator._completed_jobs_total = old_completed
        consume_once_orchestrator._total_job_duration_s = old_duration


def test_batch_manager_survives_closed_event_loop_transitions(tmp_path: Path, monkeypatch):
    """
    Regression test for Python 3.11 + `pytest-asyncio` function-scoped event loops:
    1. Starting `batch_manager` on `loop_a`, cancelling tasks + closing `loop_a`, then calling
       `await batch_manager.stop()` on `loop_b` must NOT raise `RuntimeError: Event loop is closed`.
    2. Closing `loop_a` while `_worker_tasks` are still pending (`done() == False`), then calling
       `await batch_manager.stop()` or `batch_manager.start()` on `loop_b` must safely discard
       dead-loop tasks and spawn live workers bound to `loop_b`.
    """
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "loop_cache")
    monkeypatch.setattr(settings, "use_process_pool", False)

    # Case 1: Tasks cancelled before loop closes (Python 3.11 asyncio.Runner.close() behavior)
    loop_a = asyncio.new_event_loop()
    try:
        async def _start_and_cancel() -> None:
            batch_manager.start()
            assert len(batch_manager._worker_tasks) >= 1
            for t in batch_manager._worker_tasks:
                t.cancel()
            await asyncio.gather(*batch_manager._worker_tasks, return_exceptions=True)

        loop_a.run_until_complete(_start_and_cancel())
    finally:
        loop_a.close()

    assert all(t.done() for t in batch_manager._worker_tasks)
    assert batch_manager._worker_tasks[0].get_loop().is_closed()

    loop_b = asyncio.new_event_loop()
    try:
        loop_b.run_until_complete(batch_manager.stop())
        assert batch_manager._worker_tasks == []
        assert batch_manager._running is False
    finally:
        loop_b.close()

    # Case 2: Loop closed abruptly while worker tasks are still pending (`done() == False`)
    loop_c = asyncio.new_event_loop()
    try:
        async def _start_leave_pending() -> None:
            batch_manager.start()
            await asyncio.sleep(0)

        loop_c.run_until_complete(_start_leave_pending())
    finally:
        loop_c.close()

    for orphan_task in batch_manager._worker_tasks:
        orphan_task._log_destroy_pending = False  # Intentional closed-loop pending task test

    assert len(batch_manager._worker_tasks) >= 1
    assert not batch_manager._worker_tasks[0].done()
    assert batch_manager._worker_tasks[0].get_loop().is_closed()

    loop_d = asyncio.new_event_loop()
    try:
        async def _restart_and_stop_on_fresh_loop() -> None:
            # Direct start() without prior stop() must replace dead-loop workers cleanly
            batch_manager.start()
            assert batch_manager._loop is loop_d
            assert len(batch_manager._worker_tasks) >= 1
            assert all(t.get_loop() is loop_d and not t.done() for t in batch_manager._worker_tasks)
            await batch_manager.stop()
            assert batch_manager._worker_tasks == []

        loop_d.run_until_complete(_restart_and_stop_on_fresh_loop())
    finally:
        loop_d.close()


@pytest.mark.asyncio
async def test_realtime_admin_config_updates_workers_video_limits_and_logging(
    tmp_path: Path, monkeypatch
):
    """
    Verify real-time WebUI runtime configuration (`GET/POST /api/v1/admin/config`) hot-applies:
    1. Dynamic worker pool scaling (`max_concurrent_jobs`: `1 -> 4 -> 1`) and executor mode
       (`use_process_pool`: `True -> False`) in `SessionBatchManager` & `ConsumeOnceOrchestrator`
       without restarting the server.
    2. Global video ceiling enforcement (`video_max_duration_limit_s`, `video_max_fps_limit`),
       automatically clamping registered DMD client criteria and invalidating oversized
       unconsumed standby/ready slots while preserving in-flight (`consumed=True`) streams.
    3. Panorama duration, FFmpeg thread cap, queue size, default video/color modes, mDNS,
       and live root/`dmd_showcase.*` logger levels, persisted cleanly in `server_config.json`.
    """
    import logging
    from app.domain.entities import PanelGeometry
    from app.infrastructure.persistence.server_config_store import (
        RUNTIME_SETTINGS_KEYS,
        server_config_store,
    )

    data_dir = tmp_path / "rt_data"
    cache_dir = tmp_path / "rt_cache"
    monkeypatch.setenv("DMD_DATA_DIR", str(data_dir))
    for k in RUNTIME_SETTINGS_KEYS:
        monkeypatch.setattr(settings, k, getattr(settings, k))
    monkeypatch.setattr(settings, "cache_dir", cache_dir)
    monkeypatch.setattr(settings, "use_process_pool", False)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 1)

    root_logger = logging.getLogger()
    orig_root_level = root_logger.level

    png_bytes = _make_sample_png_bytes()

    class _FakeChain:
        async def acquire_unseen_candidates(self, criteria, count=1, extra_exclude_ids=None):
            return [
                MediaCandidate(
                    id="rt_game_1",
                    domain="games",
                    title="OutRun",
                    year="1986",
                    badge="ARCADE",
                    badge_color="#FF6D00",
                    subtitle="Sega AM2",
                    rating="8.8",
                    synopsis="Classic arcade racer.",
                    source_id="arcadeitalia",
                    poster_url="http://mock/poster.png",
                    video_url="http://mock/video.mp4",
                )
            ]

    class _FakeTranscoder:
        def __init__(self) -> None:
            self.seen_durations: List[float] = []
            self.seen_fps: List[int] = []

        async def transcode_candidate(self, cand, crit: FilterCriteria, geom, item_dir: Path):
            self.seen_durations.append(crit.video_max_duration_s)
            self.seen_fps.append(crit.video_fps)
            item_dir.mkdir(parents=True, exist_ok=True)
            stream_file = item_dir / "unified_sequence.rgb565.stream"
            stream_file.write_bytes(b"DMDS" + b"\x00" * 12 + b"\x11\x22" * (geom.width * geom.height))
            (item_dir / "poster.png").write_bytes(png_bytes)
            return {
                "poster_rgb565_b64": "AA==",
                "has_panoramas": True,
                "has_video": True,
                "stream_frames": 60,
                "stream_fps": crit.video_fps,
                "stream_duration_s": crit.video_max_duration_s,
                "unified_stream_path": str(stream_file),
            }

    class _NoopMqtt:
        def set_handlers(self, **_kw):
            pass

        async def publish_json(self, topic: str, data: dict, retain: bool = False) -> None:
            pass

    class _NoopStreamer:
        active_streams_count = 0

        def get_active_stream(self, _dmd_id: str):
            return None

        async def stop_stream(self, _dmd_id: str) -> None:
            pass

    fake_transcoder = _FakeTranscoder()
    isolated_co = ConsumeOnceOrchestrator(
        chain_service=_FakeChain(),  # type: ignore[arg-type]
        seen_repo=JsonSeenHistoryRepository(file_path=data_dir / "seen_history.json"),
        transcoder=fake_transcoder,  # type: ignore[arg-type]
        streamer=_NoopStreamer(),  # type: ignore[arg-type]
        mqtt_gateway=_NoopMqtt(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("app.main.consume_once_orchestrator", isolated_co)

    try:
        batch_manager.start()
        assert len(batch_manager._worker_tasks) == 1

        # Register a DMD client originally requesting 18.0s @ 24fps (under default 30s / 30fps ceiling)
        geom = PanelGeometry(width=128, height=32)
        crit_init = isolated_co._parse_domain_criteria(
            "games",
            {"video_max_duration_s": 18.0, "video_fps": 24},
        )
        await isolated_co.register_client_domain("esp32_games_128x32", "games", geom, crit_init)
        for _ in range(30):
            if ("esp32_games_128x32", "games") in isolated_co.ready_packages:
                break
            await asyncio.sleep(0.02)
        assert ("esp32_games_128x32", "games") in isolated_co.ready_packages
        assert fake_transcoder.seen_durations[-1] == 18.0
        assert fake_transcoder.seen_fps[-1] == 24

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. GET /api/v1/admin/config exposes all real-time tuning fields + CPU metadata
            r_get = await client.get("/api/v1/admin/config")
            assert r_get.status_code == 200
            cfg0 = r_get.json()["config"]
            assert cfg0["max_concurrent_jobs"] == 1
            assert cfg0["effective_max_workers"] == 1
            assert cfg0["detected_cpu_cores"] >= 1
            assert cfg0["video_max_duration_limit_s"] == 30.0
            assert cfg0["video_max_fps_limit"] == 30
            assert cfg0["panorama_duration_s"] == 4.0

            # 2. Hot-scale workers UP to 4 and tighten video ceiling to 5.5s @ 12 FPS + DEBUG logging
            custom_cache = str(tmp_path / "hot_cache_v2")
            r_post = await client.post(
                "/api/v1/admin/config",
                json={
                    "max_concurrent_jobs": 4,
                    "use_process_pool": False,
                    "ffmpeg_threads_per_job": 2,
                    "worker_nice_increment": 5,
                    "max_queue_size": 64,
                    "video_max_duration_limit_s": 5.5,
                    "server_master_clip_max_s": 6.0,
                    "video_max_fps_limit": 12,
                    "panorama_duration_s": 2.5,
                    "ytdlp_max_filesize_mb": 35,
                    "default_video_mode": "panorama",
                    "default_color_boost": "pixel_art",
                    "session_ttl_seconds": 1800,
                    "batch_ttl_seconds": 900,
                    "cache_dir": custom_cache,
                    "log_level": "DEBUG",
                    "mdns_enabled": False,
                    "mdns_hostname": "arcadedmd",
                    "trust_proxy_headers": False,
                },
            )
            assert r_post.status_code == 200
            saved_body = r_post.json()
            assert saved_body["status"] == "saved"
            applied = saved_body["applied_live"]
            assert applied["effective_max_workers"] == 4
            assert applied["video_max_duration_limit_s"] == 5.5
            assert applied["video_max_fps_limit"] == 12
            assert applied["invalidated_slots"] == 1
            assert applied["log_level"] == "DEBUG"

            # Verify live worker pool scaled immediately to 4 workers on the running loop
            assert len(batch_manager._worker_tasks) == 4
            assert all(not t.done() for t in batch_manager._worker_tasks)
            assert root_logger.level == logging.DEBUG
            assert Path(custom_cache).exists()

            # Verify /api/v1/health reflects new live worker count & video ceilings immediately
            h_live = (await client.get("/api/v1/health")).json()
            assert h_live["max_workers"] == 4
            assert h_live["video_max_duration_limit_s"] == 5.5
            assert h_live["video_max_fps_limit"] == 12
            assert h_live["use_process_pool"] is False

            # Verify the registered DMD client had its criteria clamped to 5.5s @ 12fps and re-prepared!
            for _ in range(30):
                if (
                    ("esp32_games_128x32", "games") in isolated_co.ready_packages
                    and isolated_co.ready_packages[("esp32_games_128x32", "games")].card.stream_fps == 12
                ):
                    break
                await asyncio.sleep(0.02)
            _g, clamped_crit = isolated_co.client_configs[("esp32_games_128x32", "games")]
            assert clamped_crit.video_max_duration_s == 5.5
            assert clamped_crit.video_fps == 12
            assert fake_transcoder.seen_durations[-1] == 5.5
            assert fake_transcoder.seen_fps[-1] == 12

            # Verify new clients omitting video_mode/color_boost inherit updated server defaults
            crit_defaults = isolated_co._parse_domain_criteria("movies", {})
            assert crit_defaults.video_mode == "panorama"
            assert crit_defaults.color_boost == "pixel_art"
            assert crit_defaults.video_max_duration_s == 5.5
            assert crit_defaults.video_fps == 12

            # 3. Hot-scale workers DOWN from 4 -> 1 without dropping server state
            r_scale_down = await client.post(
                "/api/v1/admin/config",
                json={"max_concurrent_jobs": 1, "log_level": "INFO"},
            )
            assert r_scale_down.status_code == 200
            await asyncio.sleep(0.02)
            assert len(batch_manager._worker_tasks) == 1
            assert isolated_co._get_transcode_semaphore().limit == 1
            assert root_logger.level == logging.INFO

            # Verify persistence on disk (`server_config.json`)
            persisted = json.loads(server_config_store.file_path.read_text(encoding="utf-8"))
            assert persisted["max_concurrent_jobs"] == 1
            assert persisted["video_max_duration_limit_s"] == 5.5
            assert persisted["video_max_fps_limit"] == 12
            assert persisted["panorama_duration_s"] == 2.5
            assert persisted["ffmpeg_threads_per_job"] == 2
    finally:
        root_logger.setLevel(orig_root_level)
        for task in list(isolated_co._prep_tasks.values()):
            isolated_co._safe_cancel_task(task)
        await batch_manager.stop()


def test_no_user_facing_ddd_jargon_in_ui_readmes_or_openapi():
    """
    Ensure architectural 'DDD' jargon never leaks into end-user surfaces
    (WebUI HTML/I18N, OpenAPI/Swagger descriptions, READMEs, QuickStart, ESP32 guides,
    Dockerfile, pyproject.toml, or CI release notes), staying strictly in ARCHITECTURE/DEVELOPER docs.
    """
    repo_root = Path(__file__).resolve().parent.parent
    ddd_re = re.compile(r"\bddd\b", re.IGNORECASE)

    assert not ddd_re.search(ADMIN_DASHBOARD_HTML), "Found 'DDD' in ADMIN_DASHBOARD_HTML"
    openapi_text = json.dumps(app.openapi(), ensure_ascii=False)
    assert not ddd_re.search(openapi_text), "Found 'DDD' in live app.openapi() schema"

    user_facing_files = [
        repo_root / "README.md",
        repo_root / "README_FR.md",
        repo_root / "README_ES.md",
        repo_root / "Dockerfile",
        repo_root / "pyproject.toml",
        repo_root / ".github" / "workflows" / "ci.yml",
        repo_root / "docs" / "QUICKSTART.md",
        repo_root / "docs" / "QUICKSTART_FR.md",
        repo_root / "docs" / "QUICKSTART_ES.md",
        repo_root / "docs" / "ESP32_INTEGRATION.md",
        repo_root / "docs" / "ESP32_INTEGRATION_FR.md",
        repo_root / "docs" / "ESP32_INTEGRATION_ES.md",
        repo_root / "docs" / "openapi.yaml",
        repo_root / "docs" / "openapi.json",
    ]
    for path in user_facing_files:
        assert path.exists(), f"Expected user-facing file missing: {path}"
        content = path.read_text(encoding="utf-8")
        assert not ddd_re.search(content), f"Found user-facing 'DDD' jargon in {path.name}"


def test_passive_rotation_tag_picker_and_studio_domain_profiles_ui_coherence():
    """
    Verify coherent separation between Tab 1 (visual Multi-Select Tag Picker for rotation slots,
    zero raw CSV inputs or duplicated framing/color/video columns) and Tab 4 (4-Domain Studio
    Profile Switcher with bidirectional jump links).
    """
    html = ADMIN_DASHBOARD_HTML
    assert 'id="tag-picker-modal"' in html
    assert "openTagPickerModal(" in html
    assert "applyTagPickerModal(" in html
    assert 'id="studio-dom-tabs"' in html
    assert "selectStudioDomainProfile(" in html
    assert "openStudioForDmdDomainFromEditor(" in html
    assert "returnToDmdRotationEditor(" in html

    for removed_legacy_cls in ("pl-systems", "pl-genres", "pl-vmode", "pl-cboost", "pl-vdur"):
        assert removed_legacy_cls not in html, f"Legacy duplicated input '{removed_legacy_cls}' still present in HTML"


def test_mode1_readonly_mqtt_inspector_and_studio_save_guard_in_webui():
    """
    Verify WebUI renders the greyed-out Read-Only MQTT/HTTP Inspector banner & raw JSON payload viewer
    for Mode 1 DMDs (`config_source == 'client'`), and disables Studio profile-save buttons while keeping
    Simulation Queue injection active.
    """
    html = ADMIN_DASHBOARD_HTML
    for required_token in (
        'id="prof-readonly-banner"',
        'id="prof-mqtt-json-wrap"',
        'id="prof-mqtt-json-pre"',
        'id="btn-save-prof"',
        'id="btn-del-prof"',
        'id="btn-st-top-save"',
        'id="btn-st-top-save-all"',
        'id="btn-st-sec3-save"',
        'id="btn-st-sec3-save-all"',
        "applyProfileEditorReadOnlyUi(",
        "isTargetDmdClientManaged(",
        "syncStudioSaveButtonsState(",
        "summarizeLiveClientDomainBadges(",
    ):
        assert required_token in html, f"Missing Mode 1 read-only inspector token in WebUI: {required_token}"


def test_update_checker_semver_and_os_asset_matching():
    """Verify SemVer parsing, SNAPSHOT exclusion, runtime OS detection, and OS installer matching."""
    assert parse_semver_key("1.0.0") == (1, 0, 0, 1)
    assert parse_semver_key("v1.2.3") == (1, 2, 3, 1)
    assert parse_semver_key("1.0.0.dev0") == (1, 0, 0, 0)
    assert parse_semver_key("v1.1.0-SNAPSHOT") == (1, 1, 0, 0)
    assert parse_semver_key("invalid") is None

    assert is_newer_release("1.0.0", "v1.1.0") is True
    assert is_newer_release("1.0.0", "1.0.0") is False
    assert is_newer_release("1.2.0", "v1.1.9") is False
    assert is_newer_release("1.0.0", "v1.2.0-SNAPSHOT") is False
    assert is_newer_release("1.0.0.dev0", "v1.0.0") is True

    assert detect_runtime_platform(sys_platform="darwin", machine="arm64", is_docker=False) == (
        "macos",
        "arm64",
        "macOS (Apple Silicon)",
    )
    assert detect_runtime_platform(sys_platform="win32", machine="AMD64", is_docker=False) == (
        "windows",
        "x64",
        "Windows (x64)",
    )
    assert detect_runtime_platform(sys_platform="linux", machine="x86_64", is_docker=False) == (
        "linux",
        "x64",
        "Linux (x64)",
    )
    assert detect_runtime_platform(sys_platform="linux", machine="aarch64", is_docker=True) == (
        "docker",
        "arm64",
        "Docker (arm64)",
    )

    sample_assets = [
        {
            "name": "DMD_Media_Showcase_macOS_arm64.dmg",
            "browser_download_url": "https://github.com/red77290/dmd_media_showcase/releases/download/v1.2.0/DMD_Media_Showcase_macOS_arm64.dmg",
        },
        {
            "name": "DMD_Media_Showcase_macOS_x64.dmg",
            "browser_download_url": "https://github.com/red77290/dmd_media_showcase/releases/download/v1.2.0/DMD_Media_Showcase_macOS_x64.dmg",
        },
        {
            "name": "DMD_Media_Showcase_Windows_x64.zip",
            "browser_download_url": "https://github.com/red77290/dmd_media_showcase/releases/download/v1.2.0/DMD_Media_Showcase_Windows_x64.zip",
        },
        {
            "name": "DMD_Media_Showcase_Linux_x64.tar.gz",
            "browser_download_url": "https://github.com/red77290/dmd_media_showcase/releases/download/v1.2.0/DMD_Media_Showcase_Linux_x64.tar.gz",
        },
        {
            "name": "evil_untrusted.dmg",
            "browser_download_url": "https://evil.example.com/evil_untrusted.dmg",
        },
    ]

    assert select_os_release_asset(sample_assets, "macos", "arm64")[0] == "DMD_Media_Showcase_macOS_arm64.dmg"
    assert select_os_release_asset(sample_assets, "macos", "x64")[0] == "DMD_Media_Showcase_macOS_x64.dmg"
    assert select_os_release_asset(sample_assets, "windows", "x64")[0] == "DMD_Media_Showcase_Windows_x64.zip"
    assert select_os_release_asset(sample_assets, "linux", "x64")[0] == "DMD_Media_Showcase_Linux_x64.tar.gz"
    assert select_os_release_asset(sample_assets, "docker", "arm64") == (None, None)


@pytest.mark.asyncio
async def test_update_check_endpoint_and_webui_banner(monkeypatch):
    """Verify `/api/v1/system/update-check` caching, 404/offline resilience, and WebUI notification banner."""
    html = ADMIN_DASHBOARD_HTML
    for token in (
        'id="update-banner"',
        'id="update-banner-asset-link"',
        'id="update-banner-release-link"',
        "checkSystemUpdate(",
        "renderUpdateBanner(",
        "dismissUpdateBanner(",
    ):
        assert token in html, f"Missing update notification banner token in WebUI: {token}"

    calls = {"count": 0, "mode": "newer"}

    class _DummyResponse:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    class _DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            calls["count"] += 1
            if calls["mode"] == "404":
                return _DummyResponse(404, {"message": "Not Found"})
            if calls["mode"] == "offline":
                raise RuntimeError("Simulated offline network")
            return _DummyResponse(
                200,
                {
                    "tag_name": "v2.1.0",
                    "draft": False,
                    "prerelease": False,
                    "html_url": "https://github.com/red77290/dmd_media_showcase/releases/tag/v2.1.0",
                    "published_at": "2026-10-26T08:00:00Z",
                    "assets": [
                        {
                            "name": "DMD_Media_Showcase_macOS_arm64.dmg",
                            "browser_download_url": "https://github.com/red77290/dmd_media_showcase/releases/download/v2.1.0/DMD_Media_Showcase_macOS_arm64.dmg",
                        }
                    ],
                },
            )

    monkeypatch.setattr(
        "app.application.update_checker.detect_runtime_platform",
        lambda: ("macos", "arm64", "macOS (Apple Silicon)"),
    )
    checker = ReleaseUpdateChecker(
        current_version="1.0.0",
        client_factory=lambda **kwargs: _DummyClient(),
    )
    monkeypatch.setattr("app.main.release_update_checker", checker)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        r1 = await client.get("/api/v1/system/update-check")
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["status"] == "update_available"
        assert d1["update_available"] is True
        assert d1["latest_version"] == "2.1.0"
        assert d1["runtime_os"] == "macos"
        assert d1["asset_name"] == "DMD_Media_Showcase_macOS_arm64.dmg"
        assert calls["count"] == 1

        # Cached second call does not hit GitHub again
        r2 = await client.get("/api/v1/system/update-check")
        assert r2.status_code == 200
        assert calls["count"] == 1

        # 404 (no stable release yet) handled cleanly
        calls["mode"] = "404"
        r3 = await client.get("/api/v1/system/update-check?force=true")
        assert r3.status_code == 200
        assert r3.json()["status"] == "no_release_yet"
        assert r3.json()["update_available"] is False

        # Offline network error handled cleanly without 500
        calls["mode"] = "offline"
        r4 = await client.get("/api/v1/system/update-check?force=true")
        assert r4.status_code == 200
        assert r4.json()["status"] == "unreachable"
        assert r4.json()["update_available"] is False




