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
