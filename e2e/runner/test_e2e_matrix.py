"""
End-to-End Automated Containerized Matrix Suite (`E2E-01` .. `E2E-08`).

Validates real OS scripts & daemons (`Recalbox`, `Batocera`, `RetroPie`)
against real `ArcadeMatrix` engines (`ArcadeMatrix` ESP32 C++ and `ArcadeMatrix_RPi` Rust)
and a live offline `dmd_media_showcase` instance inside an air-gapped Docker network.
"""

from __future__ import annotations

import struct
import time
from typing import Any, Callable, Dict, Tuple

import httpx
import pytest

Color = Tuple[int, int, int]

SHOWCASE_URL = "http://dmd-showcase:8890"
AM_ESP32_URL = "http://dmd-arcadematrix-esp32"
AM_RPI_URL = "http://dmd-arcadematrix-rpi"

OS_URLS = {
    "recalbox": "http://os-recalbox:9000",
    "batocera": "http://os-batocera:9000",
    "retropie": "http://os-retropie:9000",
}

EXPECTED_QUADRANTS: Dict[str, Tuple[Color, Color, Color]] = {
    "mslug": ((255, 0, 0), (0, 255, 0), (0, 80, 255)),
    "smw": ((255, 255, 0), (0, 255, 255), (255, 0, 255)),
    "sonic": ((0, 120, 255), (255, 255, 255), (0, 220, 80)),
}


def unpack_rgb565_pixel(fb_raw: bytes, x: int, y: int, width: int = 128) -> Color:
    assert len(fb_raw) == width * 32 * 2, f"Unexpected framebuffer size: {len(fb_raw)}"
    px16 = struct.unpack_from("<H", fb_raw, (y * width + x) * 2)[0]
    r8 = ((px16 >> 11) & 0x1F) * 255 // 31
    g8 = ((px16 >> 5) & 0x3F) * 255 // 63
    b8 = (px16 & 0x1F) * 255 // 31
    return (r8, g8, b8)


def color_matches(actual: Color, expected: Color, tol: int = 28) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


def framebuffer_matches_rom(fb_raw: bytes, rom: str) -> Tuple[bool, Dict[str, Color]]:
    exp_tl, exp_tr, exp_bot = EXPECTED_QUADRANTS[rom]
    got_tl = unpack_rgb565_pixel(fb_raw, 16, 6)
    got_tr = unpack_rgb565_pixel(fb_raw, 112, 6)
    got_bot = unpack_rgb565_pixel(fb_raw, 32, 24)
    ok = (
        color_matches(got_tl, exp_tl)
        and color_matches(got_tr, exp_tr)
        and color_matches(got_bot, exp_bot)
    )
    return ok, {"tl": got_tl, "tr": got_tr, "bot": got_bot}


def poll_until(
    predicate: Callable[[], Tuple[bool, Any]],
    timeout_s: float = 20.0,
    interval_s: float = 0.2,
    desc: str = "condition",
) -> Any:
    deadline = time.monotonic() + timeout_s
    last_val: Any = None
    while time.monotonic() < deadline:
        ok, val = predicate()
        last_val = val
        if ok:
            return val
        time.sleep(interval_s)
    raise AssertionError(f"Timed out after {timeout_s}s waiting for {desc}; last={last_val!r}")


@pytest.fixture(scope="module")
def http() -> httpx.Client:
    with httpx.Client(timeout=15.0) as client:
        yield client


def reset_showcase_audit(http: httpx.Client) -> None:
    r = http.post(f"{SHOWCASE_URL}/api/v1/e2e/audit/reset")
    assert r.status_code == 200


def get_showcase_audit(http: httpx.Client) -> Dict[str, Any]:
    r = http.get(f"{SHOWCASE_URL}/api/v1/e2e/audit")
    assert r.status_code == 200
    return r.json()["events"]


def check_queue_delete(http: httpx.Client, target_id: str) -> Tuple[bool, Dict[str, Any]]:
    audit = get_showcase_audit(http)
    found = any(d["dmd_id"] == target_id for d in audit["queue_deletes"])
    return found, audit


def os_install(http: httpx.Client, os_name: str, dmd_type: str, dmd_host: str) -> Dict[str, Any]:
    r = http.post(
        f"{OS_URLS[os_name]}/install",
        json={"dmd_type": dmd_type, "dmd_host": dmd_host},
    )
    assert r.status_code == 200, f"OS install failed on {os_name}: {r.text}"
    return r.json()


def os_event(
    http: httpx.Client,
    os_name: str,
    action: str,
    system: str,
    rom: str,
    title: str = "",
) -> Dict[str, Any]:
    r = http.post(
        f"{OS_URLS[os_name]}/event",
        json={"action": action, "system": system, "rom": rom, "title": title},
    )
    assert r.status_code == 200, f"OS event failed on {os_name}: {r.text}"
    return r.json()


# ==============================================================================
# GROUP A: ArcadeMatrix (ESP32 Firmware C++ Engine) × Recalbox / Batocera / RetroPie
# ==============================================================================


def _run_arcadematrix_esp32_full_flow(
    request: pytest.FixtureRequest,
    http: httpx.Client,
    scenario_id: str,
    os_name: str,
    system: str,
    rom: str,
    title: str,
    check_browse_first: bool = False,
) -> None:
    ev: Dict[str, Any] = {
        "id": scenario_id,
        "dmd": "ArcadeMatrix (ESP32)",
        "os": os_name,
        "game": f"{system}/{rom}",
        "marquee_verified": False,
        "showcase_verified": False,
        "cleanup_verified": False,
    }
    request.node.e2e_evidence = ev

    reset_showcase_audit(http)
    http.post(
        f"{AM_ESP32_URL}/api/e2e/config",
        json={"use_showcase_broker": True, "ingame_showcase_enabled": True},
    )

    bt = http.get(f"{AM_ESP32_URL}/api/mqtt/broker-target").json()
    assert bt["source"] == "showcase"
    assert bt["port"] == 1883
    ev["broker_target"] = f"{bt['host']}:{bt['port']} ({bt['source']})"

    os_install(http, os_name, "arcadematrix_esp32", "dmd-arcadematrix-esp32")

    before = http.get(f"{AM_ESP32_URL}/api/e2e/state").json()
    sync_cid = before["sync_client_id"]
    ev["sync_client_id"] = sync_cid

    if check_browse_first:
        os_event(http, os_name, "browse", system, rom, title)
        samples = poll_until(
            lambda: framebuffer_matches_rom(
                http.get(f"{AM_ESP32_URL}/api/e2e/framebuffer.raw").content, rom
            ),
            timeout_s=5.0,
            desc=f"{scenario_id} browse marquee pixels",
        )
        mid = http.get(f"{AM_ESP32_URL}/api/e2e/state").json()
        assert mid["active_source"] == "MQTT"
        assert mid["showcase_enqueues"] == before["showcase_enqueues"]
        ev["browse_marquee_rgb"] = samples

    before_start = http.get(f"{AM_ESP32_URL}/api/e2e/state").json()
    t0 = time.monotonic()
    os_event(http, os_name, "start", system, rom, title)

    # 1. Immediate local marquee displayed while Showcase renders in background
    marquee_samples = poll_until(
        lambda: framebuffer_matches_rom(
            http.get(f"{AM_ESP32_URL}/api/e2e/framebuffer.raw").content, rom
        ),
        timeout_s=4.0,
        desc=f"{scenario_id} immediate in-game marquee pixels",
    )
    ev["marquee_latency_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
    ev["marquee_rgb_samples"] = marquee_samples
    ev["marquee_verified"] = True

    # 2. Wait for full Showcase DMDZ v2 stream cycle (Card + Panorama + Gameplay Video) to complete
    after_stream = poll_until(
        lambda: (
            (st := http.get(f"{AM_ESP32_URL}/api/e2e/state").json())[
                "showcase_streams_completed"
            ]
            > before_start["showcase_streams_completed"],
            st,
        ),
        timeout_s=45.0,
        interval_s=0.35,
        desc=f"{scenario_id} Showcase DMDZ v2 stream completion",
    )
    assert after_stream["last_mqtt_topic"] == f"system/playing/{os_name}"
    assert after_stream["sd_sync_cache_staged_ever"] is True
    assert after_stream["presented_stream_frames"] >= 10
    assert after_stream["distinct_stream_crcs"] >= 3

    audit = get_showcase_audit(http)
    q_posts = [q for q in audit["queue_posts"] if q["dmd_id"] == sync_cid]
    s_gets = [s for s in audit["stream_gets"] if s["domain"] == "games" and s["status_code"] == 200]
    assert len(q_posts) >= 1, f"Expected Showcase queue POST for {sync_cid}, got {audit}"
    q_payload = q_posts[-1]["payload"] or {}
    assert q_payload.get("domain") == "games"
    assert q_payload.get("include_card_intro") is True
    assert q_payload.get("include_video") is True
    assert len(s_gets) >= 1, f"Expected Showcase DMDZ stream GET, got {audit}"

    # 3. Verify clean return to active game marquee after `once` Showcase cycle
    poll_until(
        lambda: framebuffer_matches_rom(
            http.get(f"{AM_ESP32_URL}/api/e2e/framebuffer.raw").content, rom
        ),
        timeout_s=5.0,
        desc=f"{scenario_id} post-Showcase marquee restoration",
    )
    ev["showcase_stream_codec"] = s_gets[-1].get("codec") or "dmdz2"
    ev["presented_stream_frames"] = after_stream["presented_stream_frames"]
    ev["distinct_stream_crcs"] = after_stream["distinct_stream_crcs"]
    ev["showcase_verified"] = True

    # 4. Game exit -> DELETE /queue + SD cache cleanup + return to ROTATION
    os_event(http, os_name, "stop", system, rom, title)
    stopped_state = poll_until(
        lambda: (
            (st := http.get(f"{AM_ESP32_URL}/api/e2e/state").json())["active_source"]
            == "ROTATION"
            and st["is_playing"] is False,
            st,
        ),
        timeout_s=8.0,
        desc=f"{scenario_id} return to ROTATION on game stop",
    )
    assert stopped_state["sd_sync_cache_exists"] is False

    audit_after = poll_until(
        lambda: check_queue_delete(http, sync_cid),
        timeout_s=5.0,
        desc=f"{scenario_id} Showcase DELETE /queue audit",
    )
    assert any(d["dmd_id"] == sync_cid for d in audit_after["queue_deletes"])
    ev["cleanup_verified"] = True


def test_e2e_01_arcadematrix_esp32_recalbox(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    _run_arcadematrix_esp32_full_flow(
        request, http, "E2E-01", "recalbox", "neogeo", "mslug", "Metal Slug", check_browse_first=True
    )


def test_e2e_02_arcadematrix_esp32_batocera(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    _run_arcadematrix_esp32_full_flow(
        request, http, "E2E-02", "batocera", "snes", "smw", "Super Mario World"
    )


def test_e2e_03_arcadematrix_esp32_retropie(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    _run_arcadematrix_esp32_full_flow(
        request, http, "E2E-03", "retropie", "megadrive", "sonic", "Sonic the Hedgehog"
    )


def test_e2e_04_arcadematrix_esp32_local_broker_fallback(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    ev: Dict[str, Any] = {
        "id": "E2E-04",
        "dmd": "ArcadeMatrix (ESP32)",
        "os": "batocera (local broker fallback)",
        "game": "neogeo/mslug",
        "marquee_verified": False,
        "showcase_verified": "N/A (local broker fallback mode)",
        "cleanup_verified": False,
    }
    request.node.e2e_evidence = ev

    try:
        http.post(
            f"{AM_ESP32_URL}/api/e2e/config",
            json={"use_showcase_broker": False, "ingame_showcase_enabled": False},
        )
        bt = http.get(f"{AM_ESP32_URL}/api/mqtt/broker-target").json()
        assert bt["source"] == "local"
        ev["broker_target"] = f"{bt['host']}:{bt['port']} ({bt['source']})"

        os_install(http, "batocera", "arcadematrix_esp32", "dmd-arcadematrix-esp32")
        os_event(http, "batocera", "start", "neogeo", "mslug", "Metal Slug")

        samples = poll_until(
            lambda: framebuffer_matches_rom(
                http.get(f"{AM_ESP32_URL}/api/e2e/framebuffer.raw").content, "mslug"
            ),
            timeout_s=5.0,
            desc="E2E-04 local broker marquee pixels",
        )
        ev["marquee_rgb_samples"] = samples
        ev["marquee_verified"] = True

        os_event(http, "batocera", "stop", "neogeo", "mslug", "Metal Slug")
        poll_until(
            lambda: (
                (st := http.get(f"{AM_ESP32_URL}/api/e2e/state").json())["active_source"]
                == "ROTATION",
                st,
            ),
            timeout_s=5.0,
            desc="E2E-04 stop return to ROTATION",
        )
        ev["cleanup_verified"] = True
    finally:
        http.post(
            f"{AM_ESP32_URL}/api/e2e/config",
            json={"use_showcase_broker": True, "ingame_showcase_enabled": True},
        )


# ==============================================================================
# GROUP B: ArcadeMatrix_RPi (Native Rust Engine) × Recalbox / Batocera / RetroPie
# ==============================================================================


def _run_arcadematrix_rpi_full_flow(
    request: pytest.FixtureRequest,
    http: httpx.Client,
    scenario_id: str,
    os_name: str,
    system: str,
    rom: str,
    title: str,
    check_browse_first: bool = False,
) -> None:
    ev: Dict[str, Any] = {
        "id": scenario_id,
        "dmd": "ArcadeMatrix_RPi (Rust)",
        "os": os_name,
        "game": f"{system}/{rom}",
        "marquee_verified": False,
        "showcase_verified": False,
        "cleanup_verified": False,
    }
    request.node.e2e_evidence = ev

    reset_showcase_audit(http)

    bt = http.get(f"{AM_RPI_URL}/api/mqtt/broker-target").json()
    assert bt["source"] == "showcase"
    assert bt["port"] == 1883
    ev["broker_target"] = f"{bt['host']}:{bt['port']} ({bt['source']})"

    os_install(http, os_name, "arcadematrix_rpi", "dmd-arcadematrix-rpi")

    before = http.get(f"{AM_RPI_URL}/api/e2e/state").json()
    sync_cid = before["sync_client_id"]
    ev["sync_client_id"] = sync_cid

    if check_browse_first:
        os_event(http, os_name, "browse", system, rom, title)
        samples = poll_until(
            lambda: framebuffer_matches_rom(
                http.get(f"{AM_RPI_URL}/api/e2e/framebuffer.raw").content, rom
            ),
            timeout_s=5.0,
            desc=f"{scenario_id} RPi browse marquee pixels",
        )
        mid = http.get(f"{AM_RPI_URL}/api/e2e/state").json()
        assert mid["forced_mode"] == "Marquee"
        assert mid["showcase_enqueues"] == before["showcase_enqueues"]
        ev["browse_marquee_rgb"] = samples

    before_start = http.get(f"{AM_RPI_URL}/api/e2e/state").json()
    t0 = time.monotonic()
    os_event(http, os_name, "start", system, rom, title)

    marquee_samples = poll_until(
        lambda: framebuffer_matches_rom(
            http.get(f"{AM_RPI_URL}/api/e2e/framebuffer.raw").content, rom
        ),
        timeout_s=5.0,
        desc=f"{scenario_id} RPi immediate marquee pixels",
    )
    ev["marquee_latency_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
    ev["marquee_rgb_samples"] = marquee_samples
    ev["marquee_verified"] = True

    # Wait for RPi to load the Showcase DMDZ v2 stream and enter InGameShowcase
    loaded_state = poll_until(
        lambda: (
            (st := http.get(f"{AM_RPI_URL}/api/e2e/state").json())["showcase_streams_loaded"]
            > before_start["showcase_streams_loaded"],
            st,
        ),
        timeout_s=45.0,
        interval_s=0.35,
        desc=f"{scenario_id} RPi Showcase stream loaded",
    )
    assert loaded_state["last_mqtt_topic"] == f"system/playing/{os_name}"

    # Wait for `once` Showcase stream playback to finish and restore game Marquee
    finished_state = poll_until(
        lambda: (
            (st := http.get(f"{AM_RPI_URL}/api/e2e/state").json())["ingame_showcase_active"]
            is False
            and st["forced_mode"] == "Marquee",
            st,
        ),
        timeout_s=35.0,
        interval_s=0.35,
        desc=f"{scenario_id} RPi once-cycle Showcase completion",
    )
    assert finished_state["distinct_crcs"] >= 20

    audit = get_showcase_audit(http)
    q_posts = [q for q in audit["queue_posts"] if q["dmd_id"] == sync_cid]
    s_gets = [s for s in audit["stream_gets"] if s["domain"] == "games" and s["status_code"] == 200]
    assert len(q_posts) >= 1 and (q_posts[-1]["payload"] or {}).get("domain") == "games"
    assert len(s_gets) >= 1
    ev["showcase_stream_codec"] = s_gets[-1].get("codec") or "dmdz2"
    ev["distinct_crcs"] = finished_state["distinct_crcs"]
    ev["showcase_verified"] = True

    # Game exit (`stopped`) -> cancels remote sync queue (`DELETE /queue`) and keeps menu marquee clean
    os_event(http, os_name, "stop", system, rom, title)
    stopped_state = poll_until(
        lambda: (
            (st := http.get(f"{AM_RPI_URL}/api/e2e/state").json())["last_status"] == "stopped"
            and st["ingame_showcase_active"] is False,
            st,
        ),
        timeout_s=8.0,
        desc=f"{scenario_id} RPi stopped state",
    )
    assert stopped_state["ingame_showcase_active"] is False

    audit_after = poll_until(
        lambda: check_queue_delete(http, sync_cid),
        timeout_s=5.0,
        desc=f"{scenario_id} RPi Showcase DELETE /queue audit",
    )
    assert any(d["dmd_id"] == sync_cid for d in audit_after["queue_deletes"])
    ev["cleanup_verified"] = True


def test_e2e_05_arcadematrix_rpi_recalbox(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    _run_arcadematrix_rpi_full_flow(
        request, http, "E2E-05", "recalbox", "neogeo", "mslug", "Metal Slug", check_browse_first=True
    )


def test_e2e_06_arcadematrix_rpi_batocera(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    _run_arcadematrix_rpi_full_flow(
        request, http, "E2E-06", "batocera", "snes", "smw", "Super Mario World"
    )


def test_e2e_07_arcadematrix_rpi_retropie(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    _run_arcadematrix_rpi_full_flow(
        request, http, "E2E-07", "retropie", "megadrive", "sonic", "Sonic the Hedgehog"
    )


def test_e2e_08_arcadematrix_rpi_rapid_game_preemption(
    request: pytest.FixtureRequest, http: httpx.Client
) -> None:
    ev: Dict[str, Any] = {
        "id": "E2E-08",
        "dmd": "ArcadeMatrix_RPi (Rust)",
        "os": "batocera (rapid preemption mslug -> smw)",
        "game": "neogeo/mslug -> snes/smw",
        "marquee_verified": False,
        "showcase_verified": False,
        "cleanup_verified": False,
    }
    request.node.e2e_evidence = ev

    reset_showcase_audit(http)
    os_install(http, "batocera", "arcadematrix_rpi", "dmd-arcadematrix-rpi")
    before = http.get(f"{AM_RPI_URL}/api/e2e/state").json()
    sync_cid = before["sync_client_id"]

    # Launch mslug then immediately preempt with smw 350ms later
    os_event(http, "batocera", "start", "neogeo", "mslug", "Metal Slug")
    time.sleep(0.35)
    os_event(http, "batocera", "start", "snes", "smw", "Super Mario World")

    samples = poll_until(
        lambda: framebuffer_matches_rom(
            http.get(f"{AM_RPI_URL}/api/e2e/framebuffer.raw").content, "smw"
        ),
        timeout_s=5.0,
        desc="E2E-08 preempted smw marquee pixels",
    )
    ev["marquee_rgb_samples"] = samples
    ev["marquee_verified"] = True

    poll_until(
        lambda: (
            (st := http.get(f"{AM_RPI_URL}/api/e2e/state").json())["showcase_streams_loaded"]
            > before["showcase_streams_loaded"]
            and st["last_game"] == "smw",
            st,
        ),
        timeout_s=45.0,
        interval_s=0.35,
        desc="E2E-08 smw Showcase stream loaded",
    )
    ev["showcase_verified"] = True

    os_event(http, "batocera", "stop", "snes", "smw", "Super Mario World")
    poll_until(
        lambda: check_queue_delete(http, sync_cid),
        timeout_s=6.0,
        desc="E2E-08 DELETE /queue cleanup",
    )
    ev["cleanup_verified"] = True

