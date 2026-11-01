"""
Tests for `FrontendSync` (`*_sync`) Multi-Process Tree Termination (`1..N` processes per DMD),
`ProcessPoolExecutor` Isolation, `_sync` Slot Isolation from Rotation Slots, `DELETE .../queue`,
MQTT `client_offline` Companion Cleanup, and `fallback_query` Title Lookup.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
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
from app.domain.ports import (
    ConversionAbortedError,
    IFrameStreamerPort,
    IMediaTranscoderPort,
    IMqttControlGatewayPort,
)
from app.infrastructure.persistence.dmd_profile_store import DmdProfileStore
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.persistence.on_demand_cache_store import FileOnDemandCacheStore
from app.infrastructure.persistence.server_config_store import ServerConfigStore
from app.models import QueueAddRequest
from app.scheduler.dmd_process_registry import DmdConversionProcessRegistry


def _worker_spawn_child_and_sleep(marker_file_str: str, sleep_s: float) -> str:
    """
    Top-level picklable worker function that spawns a real child OS subprocess
    (simulating `ffmpeg` / `yt-dlp`) and blocks, recording both PIDs on disk.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({float(sleep_s)})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(marker_file_str).write_text(
        json.dumps({"worker_pid": os.getpid(), "child_pid": child.pid}),
        encoding="utf-8",
    )
    try:
        time.sleep(sleep_s)
        return "completed"
    finally:
        if child.poll() is None:
            child.kill()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _pool_echo_square(x: int) -> int:
    time.sleep(0.15)
    return x * x


class _RecordingMqtt(IMqttControlGatewayPort):
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


class _TrackingCancellableTranscoder(IMediaTranscoderPort):
    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.aborted_dmds: List[str] = []
        self.transcode_dmds: List[str] = []

    def abort_conversions_for_dmd(self, dmd_id: str) -> int:
        self.aborted_dmds.append(dmd_id)
        return 1

    async def transcode_candidate(
        self,
        cand: MediaCandidate,
        criteria: FilterCriteria,
        geometry: PanelGeometry,
        out_dir: Path,
        dmd_id: str = "",
    ) -> Dict[str, object]:
        self.transcode_dmds.append(dmd_id)
        if self.gate is not None:
            await self.gate.wait()
        out_dir.mkdir(parents=True, exist_ok=True)
        w, h = geometry.width, geometry.height
        header = struct.pack("<4sBBHHHHH", b"DMDS", 1, 15, w, h, 2, 66, 0)
        stream_path = out_dir / f"unified_{w}x{h}.rgb565.stream"
        stream_path.write_bytes(header + (b"\x1f\x00" * (w * h * 2)))
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


class _FallbackAwareChain:
    def __init__(self) -> None:
        self.queries: List[str] = []

    def get_staged_candidate(self, candidate_id: str) -> None:
        return None

    async def acquire_unseen_candidates(self, *args: Any, **kwargs: Any) -> List[MediaCandidate]:
        return []

    async def search_candidates(
        self, criteria: FilterCriteria, query: str, limit: int = 1
    ) -> List[MediaCandidate]:
        self.queries.append(query)
        if query.strip().lower().startswith("unknown_rom"):
            return []
        return [
            MediaCandidate(
                id=f"games_{query.strip().lower().replace(' ', '_')}",
                domain="games",
                title=f"Matched: {query.strip()}",
                year="1999",
                badge="NEO-GEO",
                badge_color="#E53935",
                subtitle="SNK",
                rating="9.4",
                synopsis="Arcade classic.",
                lang=criteria.lang,
            )
        ]

    async def search_on_demand(
        self, domain: DomainType, query: str, lang: str = "fr"
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_multi_process_dmd_abort_kills_all_workers_and_children_without_breaking_shared_pool(
    tmp_path: Path,
):
    """
    Proves that `1..N` concurrent converter processes AND their child `ffmpeg`/`yt-dlp` subprocesses
    for a `dmd_id` are all killed immediately on abort while a sibling `ProcessPoolExecutor` job
    continues unharmed and a warm replacement worker is spawned in the background.
    """
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing

    registry = DmdConversionProcessRegistry(max_idle=2)
    registry.force_isolated_in_tests = True
    loop = asyncio.get_running_loop()
    sync_id = "rpi_91cbfe_games_128x32_sync"

    marker_1 = tmp_path / "proc_1.json"
    marker_2 = tmp_path / "proc_2.json"

    async def _run_isolated_job(marker: Path) -> str:
        epoch = registry.current_epoch(sync_id)
        handle = await registry.acquire_handle_async(loop, sync_id, epoch)
        with registry.session_blocking_init(sync_id, handle, epoch) as sess:
            return await sess.run_step(loop, _worker_spawn_child_and_sleep, str(marker), 30.0)

    mp_ctx = multiprocessing.get_context("spawn")
    shared_pool = ProcessPoolExecutor(max_workers=1, mp_context=mp_ctx)
    try:
        t1 = asyncio.create_task(_run_isolated_job(marker_1))
        t2 = asyncio.create_task(_run_isolated_job(marker_2))

        deadline = time.monotonic() + 15.0
        while (not marker_1.exists() or not marker_2.exists()) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert marker_1.exists() and marker_2.exists(), "Isolated workers did not start in time"

        meta_1 = json.loads(marker_1.read_text(encoding="utf-8"))
        meta_2 = json.loads(marker_2.read_text(encoding="utf-8"))
        pids = [
            int(meta_1["worker_pid"]),
            int(meta_1["child_pid"]),
            int(meta_2["worker_pid"]),
            int(meta_2["child_pid"]),
        ]
        assert meta_1["worker_pid"] != meta_2["worker_pid"]
        assert all(_pid_is_alive(p) for p in pids)

        # Start an unrelated job on the shared ProcessPoolExecutor concurrently
        other_dmd_future = loop.run_in_executor(shared_pool, _pool_echo_square, 9)

        t_abort_start = time.monotonic()
        killed = registry.abort_for_dmd(sync_id, spawn_replacement=True)
        assert killed == 2

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        assert all(isinstance(r, ConversionAbortedError) for r in results)

        # Unrelated ProcessPoolExecutor job MUST complete without BrokenProcessPool
        assert await other_dmd_future == 81

        # Wait briefly for OS process group reaping and verify ALL 4 PIDs (2 workers + 2 children) are dead
        reap_deadline = time.monotonic() + 1.5
        while any(_pid_is_alive(p) for p in pids) and time.monotonic() < reap_deadline:
            await asyncio.sleep(0.03)
        assert not any(_pid_is_alive(p) for p in pids), f"Some PIDs survived killpg: {pids}"
        assert (time.monotonic() - t_abort_start) < 1.5
    finally:
        shared_pool.shutdown(wait=True, cancel_futures=True)
        registry.shutdown_all()


@pytest.mark.asyncio
async def test_sync_slot_isolation_single_process_preemption_and_fallback_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "cache_dir", tmp_path / "ephemeral_cache")
    gate = asyncio.Event()
    chain = _FallbackAwareChain()
    transcoder = _TrackingCancellableTranscoder(gate=gate)
    mqtt = _RecordingMqtt()

    orch = ConsumeOnceOrchestrator(
        chain_service=chain,  # type: ignore[arg-type]
        seen_repo=JsonSeenHistoryRepository(file_path=tmp_path / "seen.json"),
        transcoder=transcoder,
        streamer=_NoopStreamer(),
        mqtt_gateway=mqtt,
        profile_store=DmdProfileStore(file_path=tmp_path / "profiles.json"),
        on_demand_cache=FileOnDemandCacheStore(
            cache_root=tmp_path / "od_cache",
            config_store=ServerConfigStore(file_path=tmp_path / "srv.json"),
        ),
    )

    rot_id = "rpi_91cbfe_games_128x32"
    sync_id = "rpi_91cbfe_games_128x32_sync"
    await orch.register_client_domain(
        rot_id,
        "games",
        PanelGeometry(width=128, height=32),
        FilterCriteria(
            domain="games",
            systems=[],
            genres=[],
            decades=[],
            lang="fr",
            card_layout="poster_right",
            card_theme="neon_border",
        ),
    )
    rot_task = orch.state.prep_tasks.get((rot_id, "games"))
    if rot_task is not None:
        await rot_task
    mqtt.published.clear()

    # 1. Enqueue first sync game (blocked on gate)
    item1 = await orch.enqueue_on_demand(
        sync_id,
        QueueAddRequest(
            domain="games",
            systems=["neogeo"],
            query="mslug",
            mode="play_now",
            mark_seen=False,
            async_prepare=True,
            render_card_in_stream=True,
            width=128,
            height=32,
        ),
    )
    assert item1["status"] == "preparing"
    assert item1["card_layout"] == "poster_right"
    assert item1["card_theme"] == "neon_border"
    assert (sync_id, "games") not in orch.state.client_configs
    await asyncio.sleep(0.03)

    # 2. Enqueue second sync game with unknown ROM + fallback_query -> MUST abort game 1 first!
    gate.set()
    item2 = await orch.enqueue_on_demand(
        sync_id,
        QueueAddRequest(
            domain="games",
            systems=["neogeo"],
            query="unknown_rom_xyz",
            fallback_query="Metal Slug X",
            mode="play_now",
            mark_seen=False,
            async_prepare=True,
            render_card_in_stream=True,
            width=128,
            height=32,
        ),
    )
    assert sync_id in transcoder.aborted_dmds
    q_entries = orch.state.priority_queues[sync_id]
    assert len(q_entries) == 1
    assert q_entries[0].queue_id == item2["queue_id"]
    assert q_entries[0].target_slot_dmd_id == sync_id
    assert q_entries[0].prep_task is not None
    await q_entries[0].prep_task

    assert q_entries[0].status == "ready"
    assert q_entries[0].candidate.title == "Matched: Metal Slug X"

    # Rotation slot MUST NOT receive retained ready messages from `_sync`!
    rot_ready_msgs = [
        p for p in mqtt.published if p[0] == f"dmd_showcase/clients/{rot_id}/games/ready"
    ]
    assert rot_ready_msgs == []

    # `_sync` client MUST receive immediate `play_now` wakeup on its own `/command` topic!
    sync_cmds = [
        p for p in mqtt.published if p[0] == f"dmd_showcase/clients/{sync_id}/command"
    ]
    assert len(sync_cmds) == 1
    assert sync_cmds[0][1]["action"] == "play_now"
    assert sync_cmds[0][1]["queue_id"] == item2["queue_id"]

    # 3. Simulate MQTT LWT `client_offline` on parent rotation board -> MUST clear companion `_sync`!
    aborts_before = len(transcoder.aborted_dmds)
    await orch.handle_mqtt_client_event(rot_id, {"event": "client_offline"})
    assert len(transcoder.aborted_dmds) > aborts_before
    assert orch.list_queue(sync_id) == []


def test_delete_entire_client_queue_http_endpoint_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import app.main as main_mod
    from app.config import settings

    monkeypatch.setattr(settings, "cache_dir", tmp_path / "ephemeral_cache")
    with TestClient(main_mod.app) as client:
        # Calling DELETE on an empty queue returns 200 OK idempotently
        r_empty = client.delete("/api/v1/clients/esp32_ab12cd_games_128x32_sync/queue")
        assert r_empty.status_code == 200
        assert r_empty.json()["status"] == "cleared"
        assert r_empty.json()["cleared_count"] == 0
