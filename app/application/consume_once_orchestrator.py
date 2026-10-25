"""
Application Service: `ConsumeOnceOrchestrator`.

Coordinates:
1. Stateless DMD client registration via MQTT (`dmd_showcase/clients/{dmd_id}/config`, `retain=True`)
   or HTTP (`POST /api/v1/{domain}/config`), with immediate invalidation of stale slots on config change.
2. Background `N+1` preparation of 1 unseen `PreparedShowcasePackage` per `(dmd_id, domain)` slot:
   - Starts preparing item `N+1` as soon as item `N` enters `start_card`, eliminating inter-item pauses.
3. Strict Consume-Once locking on `start_card` (MQTT) or `GET /api/v1/{domain}/next` (HTTP):
   - Marks candidate ID consumed in `seen_history.json` immediately.
   - Publishes `ready: false` (`retain=True`) on MQTT so it can never be replayed.
4. Real-time UDP streaming via `ffmpeg -re` (`start_stream` event) to the DMD's real LAN `client_ip`
   (bypassing Docker bridge NAT `172.x.0.1`) and immediate disk purge on `stream_eof`.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace as dc_replace
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Deque, Dict, List, Literal, Optional, Set, Tuple

from app.application.source_chain_service import DomainSourceChainService, source_chain_service
from app.config import settings
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.domain.ports.seen_repository import ISeenHistoryRepository
from app.domain.taxonomy import resolve_canonical_genre, resolve_canonical_system
from app.infrastructure.mqtt.showcase_mqtt_gateway import ShowcaseMqttGateway, showcase_mqtt_gateway
from app.infrastructure.persistence.dmd_profile_store import (
    DmdProfileStore,
    VALID_CARD_FONTS,
    VALID_CARD_LAYOUTS,
    VALID_CARD_THEMES,
    dmd_profile_store,
)
from app.infrastructure.persistence.json_seen_repository import seen_repository
from app.infrastructure.streaming.ffmpeg_udp_streamer import udp_clock_streamer
from app.infrastructure.transcoder.dmd_converter_adapter import (
    dmd_transcoder_adapter,
    merge_rgb565_streams,
)
from app.models import (
    QueueAddRequest,
    StudioCardPreviewRequest,
    StudioFullPreviewRequest,
)
from app.scheduler.asset_builder import (
    _parse_hex_rgb,
    build_fast_studio_card_preview_sync,
)

logger = logging.getLogger("dmd_showcase.application.orchestrator")

VALID_DOMAINS: Tuple[DomainType, ...] = ("games", "movies", "series", "anime")
VALID_SCROLL_SPEEDS = {
    "slow",
    "normal",
    "fast",
    "turbo",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
}


_PER_ENGINE_DMD_ID_RE = re.compile(
    r"^(?P<prefix>rpi|esp32|http)_(?P<domain>games|movies|series|anime)_(?P<geom>\d+x\d+)(?:_\d+)?$",
    re.IGNORECASE,
)


def strip_mqtt_gen_suffix(client_id: str) -> str:
    """Normalize an MQTT client ID (e.g. `rpi_games_128x32_3`) back to its logical `dmd_id`."""
    raw = str(client_id or "").strip()
    m = _PER_ENGINE_DMD_ID_RE.match(raw)
    if m:
        return f"{m.group('prefix').lower()}_{m.group('domain').lower()}_{m.group('geom').lower()}"
    return raw


def _is_usable_physical_lan_ip(ip: str) -> bool:
    """
    Return True when `ip` identifies a physical DMD on the LAN (excluding loopback,
    wildcard, test placeholders, and Docker bridge `.1` NAT gateways like `172.18.0.1`).
    """
    val = str(ip or "").strip().lower()
    if not val or val in (
        "0.0.0.0",
        "127.0.0.1",
        "::1",
        "localhost",
        "unknown",
        "auto",
        "testclient",
    ):
        return False
    if val.startswith("127."):
        return False
    parts = val.split(".")
    if len(parts) == 4 and parts[0] == "172" and parts[3] == "1":
        try:
            second = int(parts[1])
            if 16 <= second <= 31:
                return False
        except ValueError:
            pass
    return True


def canonical_dmd_board_key(dmd_id: str, client_ip: str = "") -> str:
    """
    Compute a stable deduplication key representing one physical DMD matrix board:
    - Both `ArcadeMatrix` (ESP32) and `ArcadeMatrix_RPi` instantiate up to 4 per-domain
      runners (`rpi_games_128x32`, `rpi_movies_128x32`, ...) on the same physical board.
    - Grouping by real LAN `client_ip` (when non-loopback / non-Docker-NAT) or by
      `{platform}_{WxH}` ensures 1 physical board with 4 Showcase engines is counted as
      1 DMD client with 4 domain slots rather than 4 separate boards.
    """
    if _is_usable_physical_lan_ip(client_ip):
        return f"ip:{client_ip.strip()}"
    raw = str(dmd_id or "").strip()
    m = _PER_ENGINE_DMD_ID_RE.match(raw)
    if m:
        return f"board:{m.group('prefix').lower()}_{m.group('geom').lower()}"
    return f"id:{raw or 'default_dmd'}"


def _select_best_udp_ip(
    declared_ip: Optional[str],
    cached_ip: Optional[str],
    peer_ip: Optional[str],
) -> str:
    """
    Resolve the real LAN IP of the DMD client for UDP `DMDF` streaming:
    - Always prefer the explicit `client_ip` / `udp_ip` declared by the DMD inside its MQTT payload
      so Docker bridge NAT gateways (`172.17.0.1`, `172.18.0.1`, `host.docker.internal`) are bypassed.
    """
    for candidate in (declared_ip, cached_ip, peer_ip):
        ip = str(candidate or "").strip()
        if ip and ip not in ("0.0.0.0", "auto", "unknown"):
            return ip
    return "127.0.0.1"


class _DynamicAsyncLimiter:
    """
    Loop-bound concurrency gate supporting instant live resizing (`set_limit`) without
    aborting in-flight transcodes or stranding queued coroutines.
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._in_use = 0
        self._waiters: Deque[asyncio.Future[None]] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, new_limit: int) -> None:
        self._limit = max(1, int(new_limit))
        self._wake_up()

    def _wake_up(self) -> None:
        while self._in_use < self._limit and self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                self._in_use += 1
                fut.set_result(None)

    async def acquire(self) -> None:
        if self._in_use < self._limit and not self._waiters:
            self._in_use += 1
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        except BaseException:
            if not fut.done():
                try:
                    self._waiters.remove(fut)
                except ValueError:
                    pass
            else:
                self._in_use = max(0, self._in_use - 1)
                self._wake_up()
            raise

    def release(self) -> None:
        self._in_use = max(0, self._in_use - 1)
        self._wake_up()


@dataclass
class StudioPreviewEntry:
    """Cached full Studio simulation artifact allowing 0 ms promotion into an active DMD's queue."""

    preview_id: str
    domain: DomainType
    candidate: MediaCandidate
    criteria: FilterCriteria
    geometry: PanelGeometry
    preview_dir: Path
    files_map: Dict[str, object]
    chapters: List[Dict[str, object]]
    created_at: float = field(default_factory=time.time)


@dataclass
class QueuedOnDemandEntry:
    """Priority On-Demand item queued for a specific DMD (`enqueue_next` or `play_now`)."""

    queue_id: str
    dmd_id: str
    target_slot_dmd_id: str
    domain: DomainType
    candidate: MediaCandidate
    criteria: FilterCriteria
    geometry: PanelGeometry
    mode: Literal["enqueue_next", "play_now"] = "enqueue_next"
    mark_seen: bool = True
    from_studio_sim: bool = False
    status: Literal["queued", "preparing", "ready", "playing"] = "queued"
    created_at: float = field(default_factory=time.time)
    package: Optional[PreparedShowcasePackage] = None
    prep_task: Optional[asyncio.Task[None]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "dmd_id": self.dmd_id,
            "domain": self.domain,
            "candidate_id": self.candidate.id,
            "item_id": self.package.item_id if self.package else "",
            "title": self.candidate.title,
            "year": self.candidate.year,
            "badge": self.candidate.badge,
            "lang": self.criteria.lang,
            "mode": self.mode,
            "mark_seen": self.mark_seen,
            "from_studio_sim": self.from_studio_sim,
            "card_layout": self.criteria.card_layout,
            "card_theme": self.criteria.card_theme,
            "status": self.status,
            "created_at": self.created_at,
        }


class ConsumeOnceOrchestrator:
    """
    Stateless Consume-Once orchestrator managing per-DMD, per-domain prepared slots,
    server-managed passive DMD playlists (Modes 2A & 2B), and On-Demand priority queues.
    """

    def __init__(
        self,
        chain_service: Optional[DomainSourceChainService] = None,
        seen_repo: Optional[ISeenHistoryRepository] = None,
        transcoder: Optional[IMediaTranscoderPort] = None,
        streamer: Optional[IFrameStreamerPort] = None,
        mqtt_gateway: Optional[ShowcaseMqttGateway] = None,
        profile_store: Optional[DmdProfileStore] = None,
    ) -> None:
        self.chain_service = chain_service or source_chain_service
        self.seen_repo = seen_repo or seen_repository
        self.transcoder = transcoder or dmd_transcoder_adapter
        self.streamer = streamer or udp_clock_streamer
        self.mqtt = mqtt_gateway or showcase_mqtt_gateway
        self.profile_store = profile_store or dmd_profile_store

        # Per `(dmd_id, domain)` configuration, ready slot (N), standby slot (N+1), active playing slot, and prep task
        self.client_configs: Dict[Tuple[str, DomainType], Tuple[PanelGeometry, FilterCriteria]] = {}
        self.client_udp_ips: Dict[str, str] = {}
        self.client_udp_ports: Dict[str, int] = {}
        self.client_online: Dict[str, bool] = {}
        self.client_transports: Dict[Tuple[str, DomainType], str] = {}
        self.client_last_seen: Dict[Tuple[str, DomainType], float] = {}
        self.last_played_titles: Dict[Tuple[str, DomainType], str] = {}
        self.last_claimed_until: Dict[Tuple[str, DomainType], float] = {}
        self.ready_packages: Dict[Tuple[str, DomainType], PreparedShowcasePackage] = {}
        self.standby_packages: Dict[Tuple[str, DomainType], PreparedShowcasePackage] = {}
        self.active_packages: Dict[str, PreparedShowcasePackage] = {}
        self.package_mark_seen: Dict[str, bool] = {}
        self.priority_item_ids: Set[str] = set()
        self.priority_queues: Dict[str, Deque[QueuedOnDemandEntry]] = {}
        self.studio_previews: Dict[str, StudioPreviewEntry] = {}
        self._prep_tasks: Dict[Tuple[str, DomainType], asyncio.Task[None]] = {}

        # Server-managed passive DMD director state (Modes 2A & 2B)
        self._server_managed_ids: Set[str] = set()
        self._director_tasks: Dict[str, asyncio.Task[None]] = {}
        self._director_wake_events: Dict[str, asyncio.Event] = {}
        self._director_play_abort_events: Dict[str, asyncio.Event] = {}
        self._director_skip_gap: Set[str] = set()
        self._director_prefetched: Dict[str, PreparedShowcasePackage] = {}
        self._director_prefetch_tasks: Dict[str, asyncio.Task[None]] = {}
        self._director_playlist_cursors: Dict[str, Tuple[int, int]] = {}

        self._transcode_sem: Optional[_DynamicAsyncLimiter] = None
        self._transcode_sem_loop: Optional[asyncio.AbstractEventLoop] = None
        self._active_workers: int = 0
        self._queued_jobs: int = 0
        self._fairness_waiting_jobs: int = 0
        self._completed_jobs_total: int = 0
        self._total_job_duration_s: float = 0.0
        self.mqtt.set_handlers(
            on_client_config=self.handle_mqtt_client_config,
            on_client_event=self.handle_mqtt_client_event,
            on_client_hello=self.handle_client_hello,
        )

    @property
    def active_workers(self) -> int:
        return self._active_workers

    @property
    def queue_depth(self) -> int:
        return self._queued_jobs + self._fairness_waiting_jobs

    @property
    def completed_jobs_total(self) -> int:
        return self._completed_jobs_total

    @property
    def total_job_duration_s(self) -> float:
        return self._total_job_duration_s

    @property
    def avg_job_duration_s(self) -> float:
        if self._completed_jobs_total <= 0:
            return 0.0
        return round(self._total_job_duration_s / self._completed_jobs_total, 2)

    @property
    def ready_slots_count(self) -> int:
        return len(self.ready_packages) + len(self._director_prefetched)

    @property
    def standby_slots_count(self) -> int:
        return len(self.standby_packages)

    def distinct_board_keys(self) -> Set[str]:
        keys = {
            canonical_dmd_board_key(dmd_id, self.client_udp_ips.get(dmd_id, ""))
            for (dmd_id, _dom) in self.client_configs
        }
        for prof in self.profile_store.list_profiles():
            if prof.get("config_source") == "server" and (
                self.client_online.get(prof["dmd_id"], False) or prof.get("always_stream_udp", False)
            ):
                keys.add(canonical_dmd_board_key(prof["dmd_id"], prof.get("udp_ip") or self.client_udp_ips.get(prof["dmd_id"], "")))
        return keys

    @property
    def distinct_dmd_count(self) -> int:
        return len(self.distinct_board_keys())

    @property
    def active_broadcasts_count(self) -> int:
        now = time.time()
        playing_boards: Set[str] = set()
        for pkg in self.active_packages.values():
            if pkg.consumed:
                playing_boards.add(
                    canonical_dmd_board_key(pkg.dmd_id, self.client_udp_ips.get(pkg.dmd_id, ""))
                )
        for (dmd_id, _dom), until_ts in list(self.last_claimed_until.items()):
            if now < until_ts:
                playing_boards.add(
                    canonical_dmd_board_key(dmd_id, self.client_udp_ips.get(dmd_id, ""))
                )
        udp_cnt = getattr(self.streamer, "active_streams_count", 0)
        return max(udp_cnt, len(playing_boards))

    def record_completed_job(self, duration_s: float, count: int = 1) -> None:
        if count <= 0:
            return
        self._completed_jobs_total += count
        self._total_job_duration_s += max(0.0, float(duration_s))

    def record_http_client(
        self,
        dmd_id: str,
        domain: DomainType,
        client_ip: Optional[str] = None,
    ) -> None:
        key = (dmd_id, domain)
        self.client_transports.setdefault(key, "HTTP")
        self.client_last_seen[key] = time.time()
        ip = str(client_ip or "").strip()
        if ip and ip not in ("0.0.0.0", "unknown", "testclient"):
            self.client_udp_ips.setdefault(dmd_id, ip)

    @staticmethod
    def _is_task_live(
        task: Optional[asyncio.Task[Any]],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> bool:
        if task is None or task.done():
            return False
        try:
            t_loop = task.get_loop()
        except Exception:
            return False
        if t_loop is None or t_loop.is_closed():
            return False
        if loop is not None and t_loop is not loop:
            return False
        return True

    @staticmethod
    def _safe_cancel_task(task: Optional[asyncio.Task[Any]]) -> None:
        if task is None or task.done():
            return
        try:
            t_loop = task.get_loop()
            if t_loop is not None and not t_loop.is_closed():
                task.cancel()
        except RuntimeError:
            pass

    def _get_transcode_semaphore(self) -> _DynamicAsyncLimiter:
        loop = asyncio.get_running_loop()
        target = max(1, settings.effective_max_workers)
        if self._transcode_sem is None or self._transcode_sem_loop is not loop:
            self._transcode_sem = _DynamicAsyncLimiter(target)
            self._transcode_sem_loop = loop
        elif self._transcode_sem.limit != target:
            self._transcode_sem.set_limit(target)
        return self._transcode_sem

    def reconfigure_concurrency(self, max_workers: Optional[int] = None) -> int:
        """Resize the live Consume-Once transcode concurrency gate in-place without dropping queued tasks."""
        target = max(1, int(max_workers if max_workers is not None else settings.effective_max_workers))
        if (
            self._transcode_sem is not None
            and self._transcode_sem_loop is not None
            and not self._transcode_sem_loop.is_closed()
        ):
            self._transcode_sem.set_limit(target)
        return target

    async def apply_runtime_limits(self) -> int:
        """
        Enforce updated server-wide `video_max_duration_limit_s` and `video_max_fps_limit` across all
        registered DMD slots in real time, invalidating oversized unconsumed N / N+1 packages while
        letting any currently playing (`consumed=True`) broadcast complete without interruption.
        """
        max_dur_ceiling = max(1.5, float(settings.video_max_duration_limit_s))
        max_fps_ceiling = max(6, int(settings.video_max_fps_limit))
        invalidated = 0

        for key, (geom, crit) in list(self.client_configs.items()):
            dmd_id, domain = key
            clamped_dur = max(1.5, min(max_dur_ceiling, float(crit.video_max_duration_s)))
            clamped_fps = max(6, min(max_fps_ceiling, int(crit.video_fps)))
            crit_changed = (
                abs(clamped_dur - float(crit.video_max_duration_s)) > 1e-6
                or clamped_fps != int(crit.video_fps)
            )
            ready_pkg = self.ready_packages.get(key)
            standby_pkg = self.standby_packages.get(key)
            ready_exceeds = bool(
                ready_pkg is not None
                and ready_pkg.card.has_video
                and (
                    ready_pkg.card.stream_fps > max_fps_ceiling
                    or ready_pkg.card.stream_duration_s > max_dur_ceiling + 16.0
                )
            )
            standby_exceeds = bool(
                standby_pkg is not None
                and standby_pkg.card.has_video
                and (
                    standby_pkg.card.stream_fps > max_fps_ceiling
                    or standby_pkg.card.stream_duration_s > max_dur_ceiling + 16.0
                )
            )
            if not (crit_changed or ready_exceeds or standby_exceeds):
                continue

            invalidated += 1
            new_crit = dc_replace(
                crit,
                video_max_duration_s=clamped_dur,
                video_fps=clamped_fps,
            )
            self.client_configs[key] = (geom, new_crit)
            old_task = self._prep_tasks.pop(key, None)
            self._safe_cancel_task(old_task)

            stale_standby = self.standby_packages.pop(key, None)
            if stale_standby is not None and not stale_standby.consumed:
                self.active_packages.pop(stale_standby.item_id, None)
                shutil.rmtree(stale_standby.item_dir, ignore_errors=True)

            stale_ready = self.ready_packages.pop(key, None)
            if stale_ready is not None and not stale_ready.consumed:
                self.active_packages.pop(stale_ready.item_id, None)
                shutil.rmtree(stale_ready.item_dir, ignore_errors=True)

            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {"ready": False, "item_id": "", "domain": domain, "status": "reconfiguring"},
                retain=True,
            )
            self._schedule_preparation(dmd_id, domain)

        return invalidated

    def describe_connected_clients(self) -> List[Dict[str, Any]]:
        """Return a summary of currently registered DMD clients, persisted server profiles, and queue slots."""
        out: List[Dict[str, Any]] = []
        now = time.time()
        profiles_by_id = {p["dmd_id"]: p for p in self.profile_store.list_profiles()}
        seen_dmd_ids: Set[str] = set()

        for (dmd_id, domain), (geom, crit) in sorted(self.client_configs.items()):
            seen_dmd_ids.add(dmd_id)
            key = (dmd_id, domain)
            prof = profiles_by_id.get(dmd_id)
            ready_pkg = self.ready_packages.get(key)
            if ready_pkg is None and dmd_id in self._director_prefetched:
                pf = self._director_prefetched[dmd_id]
                if pf.domain == domain:
                    ready_pkg = pf
            standby_pkg = self.standby_packages.get(key)
            prep_task = self._prep_tasks.get(key) or self._director_prefetch_tasks.get(dmd_id)
            consumed_pkg = next(
                (
                    p
                    for p in self.active_packages.values()
                    if p.dmd_id == dmd_id and p.domain == domain and p.consumed
                ),
                None,
            )
            stream_meta = (
                self.streamer.get_active_stream(dmd_id)
                if hasattr(self.streamer, "get_active_stream")
                else None
            )
            is_domain_streaming = bool(
                stream_meta
                and (
                    consumed_pkg is None
                    or stream_meta.get("item_id") == consumed_pkg.item_id
                )
            )
            is_playing_now = bool(
                is_domain_streaming
                or consumed_pkg is not None
                or now < self.last_claimed_until.get(key, 0.0)
            )
            last_title = self.last_played_titles.get(key)
            playing_title = (
                consumed_pkg.card.title
                if consumed_pkg is not None
                else (last_title if is_playing_now else None)
            )
            last_seen = self.client_last_seen.get(key)
            q_len = len(self.list_queue(dmd_id))
            cfg_source = str(prof.get("config_source") if prof else ("server" if dmd_id in self._server_managed_ids else "client"))
            supports_local_card = bool(prof.get("supports_local_card", True)) if prof else (not crit.render_card_in_stream)
            out.append(
                {
                    "dmd_id": dmd_id,
                    "display_name": str(prof.get("display_name") or dmd_id) if prof else dmd_id,
                    "domain": domain,
                    "config_source": cfg_source,
                    "supports_local_card": supports_local_card,
                    "always_stream_udp": bool(prof.get("always_stream_udp", False)) if prof else False,
                    "paused": bool(prof.get("paused", False)) if prof else False,
                    "online": self.client_online.get(dmd_id, True),
                    "transport": self.client_transports.get(
                        key,
                        "UDP-PASSIVE" if (cfg_source == "server" and not supports_local_card) else "MQTT+UDP",
                    ),
                    "client_ip": self.client_udp_ips.get(dmd_id) or (str(prof.get("udp_ip") or "") if prof else ""),
                    "udp_port": self.client_udp_ports.get(dmd_id, int(prof.get("udp_port") or 4210) if prof else 4210),
                    "width": geom.width,
                    "height": geom.height,
                    "lang": str(prof.get("lang") or crit.lang) if prof else crit.lang,
                    "video_mode": crit.video_mode,
                    "color_boost": crit.color_boost,
                    "video_max_duration_s": crit.video_max_duration_s,
                    "video_fps": crit.video_fps,
                    "systems": list(crit.systems),
                    "genres": list(crit.genres),
                    "decades": list(crit.decades),
                    "active_title": playing_title or last_title,
                    "playing_title": playing_title,
                    "last_played_title": last_title,
                    "is_playing": is_playing_now,
                    "udp_streaming": is_domain_streaming,
                    "ready_title": ready_pkg.card.title if ready_pkg else None,
                    "standby_title": standby_pkg.card.title if standby_pkg else None,
                    "preparing": self._is_task_live(prep_task),
                    "queue_length": q_len,
                    "last_seen_ago_s": int(max(0.0, now - last_seen)) if last_seen else None,
                }
            )

        # Include persisted profiles that have not yet populated `client_configs`
        for dmd_id, prof in sorted(profiles_by_id.items()):
            if dmd_id in seen_dmd_ids:
                continue
            enabled_slots = [s for s in (prof.get("playlist") or []) if s.get("enabled", True)]
            first_slot = enabled_slots[0] if enabled_slots else ((prof.get("playlist") or [{}])[0])
            dom = str(first_slot.get("domain") or "games")
            pf_pkg = self._director_prefetched.get(dmd_id)
            out.append(
                {
                    "dmd_id": dmd_id,
                    "display_name": str(prof.get("display_name") or dmd_id),
                    "domain": dom,
                    "config_source": str(prof.get("config_source") or "server"),
                    "supports_local_card": bool(prof.get("supports_local_card", True)),
                    "always_stream_udp": bool(prof.get("always_stream_udp", False)),
                    "paused": bool(prof.get("paused", False)),
                    "online": self.client_online.get(dmd_id, False),
                    "transport": "UDP-PASSIVE" if not prof.get("supports_local_card", True) else "MQTT+UDP",
                    "client_ip": self.client_udp_ips.get(dmd_id) or str(prof.get("udp_ip") or ""),
                    "udp_port": int(prof.get("udp_port") or 4210),
                    "width": int(prof.get("width") or 128),
                    "height": int(prof.get("height") or 32),
                    "lang": str(prof.get("lang") or "fr"),
                    "video_mode": str(first_slot.get("video_mode") or "auto_action"),
                    "color_boost": first_slot.get("color_boost") or "auto",
                    "video_max_duration_s": float(first_slot.get("video_max_duration_s") or 8.0),
                    "video_fps": int(first_slot.get("video_fps") or 15),
                    "systems": list(first_slot.get("systems") or []),
                    "genres": list(first_slot.get("genres") or []),
                    "decades": list(first_slot.get("decades") or []),
                    "active_title": None,
                    "playing_title": None,
                    "last_played_title": None,
                    "is_playing": False,
                    "udp_streaming": False,
                    "ready_title": pf_pkg.card.title if pf_pkg else None,
                    "standby_title": None,
                    "preparing": self._is_task_live(self._director_prefetch_tasks.get(dmd_id)),
                    "queue_length": len(self.list_queue(dmd_id)),
                    "last_seen_ago_s": None,
                }
            )
        return out

    def _parse_domain_criteria(
        self, domain: DomainType, raw_cfg: Dict[str, Any], top_lang: str = "fr"
    ) -> FilterCriteria:
        lang = str(raw_cfg.get("lang") or top_lang or "fr").strip().lower()
        if lang in ("", "system", "auto"):
            lang = (top_lang if top_lang not in ("", "system", "auto") else "fr").lower()

        raw_systems = raw_cfg.get("systems")
        if isinstance(raw_systems, str):
            sys_tokens = [s.strip() for s in raw_systems.split(",") if s.strip()]
        elif isinstance(raw_systems, list):
            sys_tokens = [str(s).strip() for s in raw_systems if str(s).strip()]
        else:
            sys_tokens = ["arcade", "mame", "neogeo", "cps1", "cps2", "snes", "megadrive"]

        systems: List[str] = []
        for s in sys_tokens:
            if s.lower() == "all":
                continue
            canon_s = resolve_canonical_system(s)
            if canon_s and canon_s not in systems:
                systems.append(canon_s)

        raw_genres = raw_cfg.get("genres")
        if isinstance(raw_genres, str):
            genre_tokens = [g.strip() for g in raw_genres.split(",") if g.strip()]
        elif isinstance(raw_genres, list):
            genre_tokens = [str(g).strip() for g in raw_genres if str(g).strip()]
        else:
            genre_tokens = []

        genres: List[str] = []
        for g in genre_tokens:
            if g.lower() == "all":
                continue
            canon_g = resolve_canonical_genre(domain, g)
            if canon_g and canon_g not in genres:
                genres.append(canon_g)

        raw_decades = raw_cfg.get("decades")
        if isinstance(raw_decades, str):
            decades = [d.strip() for d in raw_decades.split(",") if d.strip() and d.strip().lower() != "all"]
        elif isinstance(raw_decades, list):
            decades = [str(d).strip() for d in raw_decades if str(d).strip() and str(d).strip().lower() != "all"]
        else:
            decades = []

        speed = str(raw_cfg.get("text_scroll_speed") or "normal").strip().lower()
        if speed not in VALID_SCROLL_SPEEDS:
            speed = "normal"

        default_cb = str(settings.default_color_boost or "auto").strip().lower()
        if default_cb not in ("auto", "pixel_art", "anime", "cinema"):
            default_cb = "auto"
        raw_cb = raw_cfg.get("color_boost")
        if raw_cb is None:
            color_boost_val = default_cb
        elif isinstance(raw_cb, bool):
            color_boost_val = "auto" if raw_cb else "cinema"
        else:
            cb_str = str(raw_cb).strip().lower()
            if cb_str == "":
                color_boost_val = default_cb
            elif cb_str in ("true", "1", "yes", "on", "auto"):
                color_boost_val = "auto"
            elif cb_str in ("false", "0", "no", "off", "none"):
                color_boost_val = "cinema"
            elif cb_str in ("pixel_art", "anime", "cinema"):
                color_boost_val = cb_str
            else:
                color_boost_val = default_cb

        default_vm = str(settings.default_video_mode or "auto_action").strip().lower()
        if default_vm not in ("auto_action", "panorama", "center_crop"):
            default_vm = "auto_action"
        vm_raw = str(raw_cfg.get("video_mode") or "").strip().lower()
        video_mode_val = (
            vm_raw if vm_raw in ("auto_action", "panorama", "center_crop") else default_vm
        )

        max_dur_ceiling = max(1.5, float(settings.video_max_duration_limit_s))
        req_dur = float(raw_cfg.get("video_max_duration_s") or 8.0)
        eff_video_dur = max(1.5, min(max_dur_ceiling, req_dur))

        max_fps_ceiling = max(6, int(settings.video_max_fps_limit))
        req_fps = int(raw_cfg.get("video_fps") or 15)
        eff_video_fps = max(6, min(max_fps_ceiling, req_fps))

        card_layout_raw = str(raw_cfg.get("card_layout") or "classic_split").strip().lower()
        card_layout_val = card_layout_raw if card_layout_raw in VALID_CARD_LAYOUTS else "classic_split"
        card_theme_raw = str(raw_cfg.get("card_theme") or "tinted").strip().lower()
        card_theme_val = card_theme_raw if card_theme_raw in VALID_CARD_THEMES else "tinted"
        card_font_raw = str(raw_cfg.get("card_font") or "arcade_pixel").strip().lower()
        card_font_val = card_font_raw if card_font_raw in VALID_CARD_FONTS else "arcade_pixel"
        card_accent_val = str(raw_cfg.get("card_accent_color") or "auto").strip() or "auto"
        hold_raw = raw_cfg.get("card_hold_s")
        card_hold_val = max(0.2, min(5.0, float(hold_raw if hold_raw is not None else 0.9)))
        pan_dur_raw = raw_cfg.get("panorama_duration_s")
        pan_dur_val = max(
            1.5,
            min(15.0, float(pan_dur_raw if pan_dur_raw is not None else settings.panorama_duration_s)),
        )

        return FilterCriteria(
            domain=domain,
            lang=lang,
            systems=systems,
            genres=genres,
            decades=decades,
            min_rating=float(raw_cfg.get("min_rating") or 6.0),
            video_type=str(raw_cfg.get("video_type") or "any"),
            include_poster=bool(raw_cfg.get("show_poster", raw_cfg.get("include_poster", True))),
            include_synopsis=bool(raw_cfg.get("show_synopsis", raw_cfg.get("include_synopsis", True))),
            text_scroll_speed=speed,  # type: ignore[arg-type]
            include_card_intro=bool(raw_cfg.get("include_card_intro", True)),
            card_layout=card_layout_val,
            card_theme=card_theme_val,
            card_font=card_font_val,
            card_accent_color=card_accent_val,
            show_badge=bool(raw_cfg.get("show_badge", True)),
            show_year=bool(raw_cfg.get("show_year", True)),
            show_rating=bool(raw_cfg.get("show_rating", True)),
            card_hold_s=card_hold_val,
            jacket_panorama=bool(raw_cfg.get("jacket_panorama", True)),
            marquee_panorama=bool(raw_cfg.get("marquee_panorama", False)),
            snapshot_panorama_count=int(raw_cfg.get("snapshot_panorama_count", 0)),
            panorama_duration_s=pan_dur_val,
            include_video=bool(raw_cfg.get("show_video", raw_cfg.get("include_video", True))),
            video_max_duration_s=eff_video_dur,
            video_fps=eff_video_fps,
            video_mode=video_mode_val,
            color_boost=color_boost_val,
            render_card_in_stream=bool(raw_cfg.get("render_card_in_stream", False)),
        )

    async def handle_client_hello(
        self,
        dmd_id: str,
        payload: Dict[str, Any],
        detected_ip: str = "",
    ) -> Dict[str, Any]:
        """
        Handle passive or hybrid DMD `hello` announcement over MQTT (`dmd_showcase/clients/{dmd_id}/hello`)
        or HTTP (`POST /api/v1/clients/hello`). Persists/updates hardware coordinates in `dmd_profiles.json`
        without overwriting curated playlists or broadcast language, and wakes the ServerManagedDirector.
        """
        declared_ip = str(payload.get("udp_ip") or payload.get("client_ip") or "").strip()
        peer_ip = str(payload.get("_peer_ip") or detected_ip or "").strip()
        best_ip = _select_best_udp_ip(declared_ip, self.client_udp_ips.get(dmd_id), peer_ip)
        if best_ip and best_ip not in ("127.0.0.1", "testclient"):
            self.client_udp_ips[dmd_id] = best_ip
        elif declared_ip:
            self.client_udp_ips[dmd_id] = declared_ip

        if payload.get("udp_port"):
            self.client_udp_ports[dmd_id] = max(1, min(65535, int(payload["udp_port"])))
        self.client_online[dmd_id] = True

        prof = self.profile_store.ensure_from_hello(dmd_id, payload, detected_ip=best_ip)
        now_ts = time.time()
        for slot in prof.get("playlist") or []:
            if slot.get("enabled", True) and slot.get("domain") in VALID_DOMAINS:
                self.client_last_seen[(dmd_id, slot["domain"])] = now_ts  # type: ignore[index]

        if prof.get("config_source") == "server":
            await self.sync_server_managed_profiles()
            self._wake_director(dmd_id)
        return prof

    async def handle_mqtt_client_config(self, dmd_id: str, payload: Dict[str, Any]) -> None:
        """
        Handle retained `dmd_showcase/clients/{dmd_id}/config` message published by a DMD
        at boot or when WebUI settings are saved.
        """
        if payload.get("_is_hello") or str(payload.get("config_source") or "").strip().lower() == "server":
            await self.handle_client_hello(dmd_id, payload)
            return

        # If a persisted server-managed profile explicitly overrides this DMD to `server` mode,
        # honor the server profile rather than letting client defaults clobber it.
        existing_prof = self.profile_store.get_profile(dmd_id)
        if existing_prof is not None and existing_prof.get("config_source") == "server":
            await self.handle_client_hello(dmd_id, payload)
            return

        self.client_online[dmd_id] = True
        width = int(payload.get("width") or 128)
        height = int(payload.get("height") or 32)
        top_lang = str(payload.get("lang") or "fr")
        geometry = PanelGeometry(width=width, height=height)

        declared_ip = str(payload.get("client_ip") or payload.get("udp_ip") or "").strip()
        peer_ip = str(payload.get("_peer_ip") or "").strip()
        best_ip = _select_best_udp_ip(declared_ip, self.client_udp_ips.get(dmd_id), peer_ip)
        if best_ip:
            self.client_udp_ips[dmd_id] = best_ip

        # If the DMD rebooted or reconnected mid-broadcast, stop any orphaned UDP stream
        # and purge any pre-reboot consumed package that was waiting for `start_stream`.
        await self._cleanup_abandoned_active_packages(dmd_id)

        if payload.get("unregister"):
            for dom_key in VALID_DOMAINS:
                self.client_configs.pop((dmd_id, dom_key), None)
                self.client_transports.pop((dmd_id, dom_key), None)
                self.client_last_seen.pop((dmd_id, dom_key), None)
                self.last_played_titles.pop((dmd_id, dom_key), None)
                self.last_claimed_until.pop((dmd_id, dom_key), None)
                t = self._prep_tasks.pop((dmd_id, dom_key), None)
                self._safe_cancel_task(t)
            return

        # If this physical DMD (identified by its LAN `best_ip`) rotated to a sibling
        # Showcase domain (e.g. `rpi_series_128x32` -> `rpi_games_128x32`), stop any
        # active real-time UDP stream of the previous domain on the same IP, while
        # preserving background slot preparation so all 4 rotation domains become ready
        # and publish `ready: true` (`retain=True`) on the broker!
        if best_ip and best_ip != "127.0.0.1":
            for other_dmd_id, other_ip in list(self.client_udp_ips.items()):
                if other_dmd_id != dmd_id and other_ip == best_ip:
                    await self.streamer.stop_stream(other_dmd_id)

        now_ts = time.time()
        engines_map = payload.get("domains") or payload.get("engines")
        if isinstance(engines_map, dict):
            for dom_key, dom_cfg in engines_map.items():
                if dom_key in VALID_DOMAINS and isinstance(dom_cfg, dict):
                    dom: DomainType = dom_key  # type: ignore[assignment]
                    self.client_transports[(dmd_id, dom)] = "MQTT+UDP"
                    self.client_last_seen[(dmd_id, dom)] = now_ts
                    criteria = self._parse_domain_criteria(dom, dom_cfg, top_lang=top_lang)
                    await self.register_client_domain(dmd_id, dom, geometry, criteria)
        else:
            dom_raw = str(payload.get("domain") or "games")
            if dom_raw in VALID_DOMAINS:
                dom_single: DomainType = dom_raw  # type: ignore[assignment]
                self.client_transports[(dmd_id, dom_single)] = "MQTT+UDP"
                self.client_last_seen[(dmd_id, dom_single)] = now_ts
                criteria = self._parse_domain_criteria(dom_single, payload, top_lang=top_lang)
                await self.register_client_domain(dmd_id, dom_single, geometry, criteria)

    async def _cleanup_abandoned_active_packages(self, dmd_id: str) -> None:
        """
        Stop any active `ffmpeg -re` UDP stream for `dmd_id` and purge any `consumed=True`
        package in `self.active_packages` that was waiting for `start_stream` before a DMD reboot.
        """
        await self.streamer.stop_stream(dmd_id)
        for dom_key in VALID_DOMAINS:
            self.last_claimed_until.pop((dmd_id, dom_key), None)
        abandoned_ids = [
            item_id
            for item_id, pkg in list(self.active_packages.items())
            if pkg.dmd_id == dmd_id and pkg.consumed
        ]
        for item_id in abandoned_ids:
            pkg = self.active_packages.pop(item_id, None)
            if pkg is not None:
                shutil.rmtree(pkg.item_dir, ignore_errors=True)
                logger.info(
                    "[ORCHESTRATOR] Purged abandoned media files for '%s' ('%s')",
                    item_id,
                    pkg.candidate.title,
                )

    async def register_client_domain(
        self,
        dmd_id: str,
        domain: DomainType,
        geometry: PanelGeometry,
        criteria: FilterCriteria,
    ) -> None:
        key = (dmd_id, domain)
        old_pair = self.client_configs.get(key)
        self.client_configs[key] = (geometry, criteria)

        if old_pair is not None and old_pair != (geometry, criteria):
            logger.info(
                "[ORCHESTRATOR] Retained config changed for dmd='%s' domain='%s' "
                "(%dx%d, lang=%s, systems=%s, genres=%s, decades=%s, speed=%s) -> invalidating stale slot",
                dmd_id,
                domain,
                geometry.width,
                geometry.height,
                criteria.lang,
                criteria.systems,
                criteria.genres,
                criteria.decades,
                criteria.text_scroll_speed,
            )
            old_task = self._prep_tasks.pop(key, None)
            self._safe_cancel_task(old_task)
            stale_standby = self.standby_packages.pop(key, None)
            if stale_standby is not None:
                self.active_packages.pop(stale_standby.item_id, None)
                shutil.rmtree(stale_standby.item_dir, ignore_errors=True)
            stale_pkg = self.ready_packages.pop(key, None)
            if stale_pkg is not None:
                self.active_packages.pop(stale_pkg.item_id, None)
                shutil.rmtree(stale_pkg.item_dir, ignore_errors=True)
            for active_id, active_pkg in list(self.active_packages.items()):
                if active_pkg.dmd_id == dmd_id and active_pkg.domain == domain:
                    self.active_packages.pop(active_id, None)
                    shutil.rmtree(active_pkg.item_dir, ignore_errors=True)
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {"ready": False, "item_id": "", "domain": domain, "status": "reconfiguring"},
                retain=True,
            )
            self._schedule_preparation(dmd_id, domain)
            return

        existing_ready = self.ready_packages.get(key)
        if existing_ready is not None and not existing_ready.consumed:
            # Re-publish the retained `ready: true` message so a freshly rebooted DMD
            # is guaranteed to receive the prepared card immediately upon re-registering.
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {
                    "ready": True,
                    "item_id": existing_ready.item_id,
                    "domain": domain,
                    "card": existing_ready.card.to_dict(),
                },
                retain=True,
            )
            self._schedule_preparation(dmd_id, domain)
            return

        if old_pair is None and key not in self.ready_packages:
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {"ready": False, "item_id": "", "domain": domain, "status": "preparing"},
                retain=True,
            )

        if (key not in self.ready_packages or key not in self.standby_packages) and (
            not self._is_task_live(self._prep_tasks.get(key))
        ):
            if old_pair is None:
                logger.info(
                    "[ORCHESTRATOR] Registered dmd='%s' domain='%s' (%dx%d, lang=%s, systems=%s, genres=%s, decades=%s, speed=%s)",
                    dmd_id,
                    domain,
                    geometry.width,
                    geometry.height,
                    criteria.lang,
                    criteria.systems,
                    criteria.genres,
                    criteria.decades,
                    criteria.text_scroll_speed,
                )
            self._schedule_preparation(dmd_id, domain)

    def _schedule_preparation(self, dmd_id: str, domain: DomainType) -> None:
        if dmd_id in self._server_managed_ids:
            return
        key = (dmd_id, domain)
        if key in self.ready_packages and key in self.standby_packages:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        existing = self._prep_tasks.get(key)
        if self._is_task_live(existing, loop):
            return
        self._safe_cancel_task(existing)
        self._prep_tasks[key] = loop.create_task(self._prepare_slot_task(dmd_id, domain))

    async def _prepare_slot_task(self, dmd_id: str, domain: DomainType) -> None:
        key = (dmd_id, domain)
        try:
            while key in self.client_configs and (
                key not in self.ready_packages or key not in self.standby_packages
            ):
                # Multi-domain fairness: if this domain already has its Slot N (`ready_packages`)
                # published (`retain=True`), yield before building Slot N+1 (`standby_packages`)
                # whenever any sibling rotation domain is still preparing its initial Slot N.
                if key in self.ready_packages:
                    sibling_missing_ready = any(
                        other_k != key
                        and other_k not in self.ready_packages
                        and self._is_task_live(self._prep_tasks.get(other_k))
                        for other_k in list(self.client_configs.keys())
                    )
                    if sibling_missing_ready:
                        self._fairness_waiting_jobs += 1
                        try:
                            await asyncio.sleep(0.05)
                        finally:
                            self._fairness_waiting_jobs = max(0, self._fairness_waiting_jobs - 1)
                        continue

                cfg_pair = self.client_configs.get(key)
                if not cfg_pair:
                    return
                geometry, criteria = cfg_pair

                # Exclude any currently active/in-flight/standby candidate IDs so N+1 is guaranteed distinct from N
                active_exclude = {
                    pkg.candidate.id
                    for pkg in self.active_packages.values()
                    if pkg.domain == domain
                }
                for slot_pkg in list(self.ready_packages.values()) + list(self.standby_packages.values()):
                    if slot_pkg.domain == domain:
                        active_exclude.add(slot_pkg.candidate.id)

                chosen_pkg: Optional[PreparedShowcasePackage] = None
                sem = self._get_transcode_semaphore()
                self._queued_jobs += 1
                queued_decremented = False
                try:
                    await sem.acquire()
                    self._queued_jobs = max(0, self._queued_jobs - 1)
                    queued_decremented = True
                    self._active_workers += 1
                    t_job_start = time.monotonic()
                    try:
                        candidates = await self.chain_service.acquire_unseen_candidates(
                            criteria, count=1, extra_exclude_ids=active_exclude
                        )
                        if candidates:
                            cand = candidates[0]
                            active_exclude.add(cand.id)
                            item_id = f"pkg_{domain}_{uuid.uuid4().hex[:8]}_{cand.id}"
                            item_dir = settings.cache_dir / "consume_once" / dmd_id / domain / item_id
                            files_map = await self.transcoder.transcode_candidate(
                                cand, criteria, geometry, item_dir
                            )
                        else:
                            cand = None
                            item_id = ""
                            item_dir = settings.cache_dir
                            files_map = {}
                    finally:
                        elapsed_s = max(0.0, time.monotonic() - t_job_start)
                        self._active_workers = max(0, self._active_workers - 1)
                        if candidates:
                            self._completed_jobs_total += 1
                            self._total_job_duration_s += elapsed_s
                        sem.release()
                finally:
                    if not queued_decremented:
                        self._queued_jobs = max(0, self._queued_jobs - 1)

                if self.client_configs.get(key) != (geometry, criteria):
                    if candidates and cand is not None:
                        shutil.rmtree(item_dir, ignore_errors=True)
                    continue

                if candidates and cand is not None:
                    poster_w, poster_h = geometry.resolved_poster_dims()

                    card = ShowcaseCard(
                        item_id=item_id,
                        domain=domain,
                        title=cand.title,
                        year=cand.year,
                        badge=cand.badge,
                        badge_color=cand.badge_color,
                        subtitle=cand.subtitle,
                        rating=cand.rating,
                        synopsis=cand.synopsis if criteria.include_synopsis else "",
                        lang=criteria.lang,
                        source_used=cand.source_id,
                        poster_width=poster_w,
                        poster_height=poster_h,
                        poster_rgb565_b64=str(files_map.get("poster_rgb565_b64") or ""),
                        text_scroll_speed=criteria.text_scroll_speed,
                        has_panoramas=bool(files_map.get("has_panoramas")),
                        has_video=bool(files_map.get("has_video")),
                        stream_frames=int(files_map.get("stream_frames") or 0),
                        stream_fps=int(files_map.get("stream_fps") or criteria.video_fps),
                        stream_duration_s=float(files_map.get("stream_duration_s") or 0.0),
                    )
                    unified_path_str = str(files_map.get("unified_stream_path") or "")
                    chosen_pkg = PreparedShowcasePackage(
                        item_id=item_id,
                        domain=domain,
                        dmd_id=dmd_id,
                        candidate=cand,
                        card=card,
                        geometry=geometry,
                        item_dir=item_dir,
                        unified_stream_path=Path(unified_path_str) if unified_path_str else None,
                        files_map=files_map,
                    )

                if chosen_pkg is None:
                    if key not in self.ready_packages:
                        await self.mqtt.publish_json(
                            f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                            {"ready": False, "item_id": "", "status": "empty_catalog"},
                            retain=True,
                        )
                    loop = asyncio.get_running_loop()

                    async def _delayed_retry() -> None:
                        await asyncio.sleep(5.0)
                        if key in self.client_configs:
                            self._schedule_preparation(dmd_id, domain)

                    loop.create_task(_delayed_retry())
                    return

                # Ensure item_dir still exists in case the last fallback candidate was kept
                if not chosen_pkg.item_dir.exists():
                    return

                self.active_packages[chosen_pkg.item_id] = chosen_pkg
                if key not in self.ready_packages:
                    self.ready_packages[key] = chosen_pkg
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                        {
                            "ready": True,
                            "item_id": chosen_pkg.item_id,
                            "domain": domain,
                            "card": chosen_pkg.card.to_dict(),
                        },
                        retain=True,
                    )
                    logger.info(
                        "[ORCHESTRATOR] Prepared Consume-Once slot for dmd='%s' domain='%s' -> '%s' (source=%s, has_video=%s, frames=%d)",
                        dmd_id,
                        domain,
                        chosen_pkg.candidate.title,
                        chosen_pkg.candidate.source_id,
                        chosen_pkg.card.has_video,
                        chosen_pkg.card.stream_frames,
                    )
                else:
                    self.standby_packages[key] = chosen_pkg
                    logger.info(
                        "[ORCHESTRATOR] Prepared N+1 standby slot for dmd='%s' domain='%s' -> '%s' (source=%s, has_video=%s, frames=%d)",
                        dmd_id,
                        domain,
                        chosen_pkg.candidate.title,
                        chosen_pkg.candidate.source_id,
                        chosen_pkg.card.has_video,
                        chosen_pkg.card.stream_frames,
                    )
        except asyncio.CancelledError:
            logger.info("[ORCHESTRATOR] Cancelled outdated preparation task for %s/%s", dmd_id, domain)
        except Exception as exc:
            logger.error("[ORCHESTRATOR] Failed preparing slot for %s/%s: %s", dmd_id, domain, exc)

    async def claim_next_package(
        self, dmd_id: str, domain: DomainType, item_id: Optional[str] = None
    ) -> Optional[PreparedShowcasePackage]:
        """
        Atomically claim and lock the prepared package for `(dmd_id, domain)`:
        1. Marks the candidate ID as consumed in `seen_history.json`.
        2. If a pre-encoded N+1 standby package (`self.standby_packages`) is already available,
           promotes it immediately to `self.ready_packages` and publishes `ready: true` (`retain=True`)
           so there is zero waiting between items; otherwise publishes `ready: false` (`retain=True`).
        3. Immediately schedules background preparation to keep the 2-slot pipeline full.
        """
        key = (dmd_id, domain)
        pkg = self.ready_packages.pop(key, None)
        if pkg is None and item_id:
            pkg = self.active_packages.get(item_id)
            if pkg and pkg.consumed:
                return pkg
        if pkg is None:
            self._schedule_preparation(dmd_id, domain)
            return None

        now_ts = time.time()
        pkg.consumed = True
        self.last_played_titles[key] = pkg.card.title
        self.client_last_seen[key] = now_ts
        board_key = canonical_dmd_board_key(dmd_id, self.client_udp_ips.get(dmd_id, ""))
        for other_key in list(self.last_claimed_until.keys()):
            if other_key != key:
                other_board = canonical_dmd_board_key(
                    other_key[0], self.client_udp_ips.get(other_key[0], "")
                )
                if other_board == board_key:
                    self.last_claimed_until.pop(other_key, None)
        display_window_s = max(10.0, float(pkg.card.stream_duration_s or 0.0) + 8.0)
        self.last_claimed_until[key] = now_ts + display_window_s
        should_mark_seen = self.package_mark_seen.pop(pkg.item_id, True)
        self.priority_item_ids.discard(pkg.item_id)
        if should_mark_seen:
            self.seen_repo.mark_consumed(domain, pkg.candidate.id)

        # Remove matching priority queue entry if this package came from an On-Demand queue
        self._pop_claimed_priority_entry(dmd_id, pkg.item_id)

        # Promote next ready priority queue entry for `(dmd_id, domain)` first, before random standby!
        next_priority_pkg = self._take_next_ready_priority_package_for_slot(dmd_id, domain)
        if next_priority_pkg is not None:
            self.ready_packages[key] = next_priority_pkg
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {
                    "ready": True,
                    "item_id": next_priority_pkg.item_id,
                    "domain": domain,
                    "card": next_priority_pkg.card.to_dict(),
                },
                retain=True,
            )
        else:
            standby_pkg = self.standby_packages.pop(key, None)
            if standby_pkg is not None and not standby_pkg.consumed:
                self.ready_packages[key] = standby_pkg
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                    {
                        "ready": True,
                        "item_id": standby_pkg.item_id,
                        "domain": domain,
                        "card": standby_pkg.card.to_dict(),
                    },
                    retain=True,
                )
                logger.info(
                    "[ORCHESTRATOR] Promoted N+1 standby slot '%s' ('%s') to ready for dmd='%s' domain='%s'",
                    standby_pkg.item_id,
                    standby_pkg.candidate.title,
                    dmd_id,
                    domain,
                )
            else:
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                    {"ready": False, "item_id": pkg.item_id, "domain": domain},
                    retain=True,
                )
        # Keep the 2-slot (ready + standby) background pipeline full!
        self._schedule_preparation(dmd_id, domain)
        return pkg

    async def purge_consumed_package(self, item_id: str) -> None:
        """Delete all disk files of a consumed package and ensure the next slot is prepared."""
        self.package_mark_seen.pop(item_id, None)
        self.priority_item_ids.discard(item_id)
        pkg = self.active_packages.pop(item_id, None)
        if pkg is not None:
            shutil.rmtree(pkg.item_dir, ignore_errors=True)
            logger.info(
                "[ORCHESTRATOR] Purged consumed media files for '%s' ('%s')",
                item_id,
                pkg.candidate.title,
            )
            abort_ev = self._director_play_abort_events.get(pkg.dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            self._schedule_preparation(pkg.dmd_id, pkg.domain)

    async def handle_mqtt_client_event(self, dmd_id: str, event_payload: Dict[str, Any]) -> None:
        """
        Handle real-time DMD events on `dmd_showcase/clients/{dmd_id}/events`
        or `dmd_showcase/clients/{dmd_id}/{domain}/events`:
        - `start_card`   : locks/consumes item `N` immediately (`ready=False` retained + `seen_history.json`)
                           and triggers `N+1` background preparation.
        - `start_stream` : launches `ffmpeg -re` UDP stream to the DMD's LAN `udp_ip:udp_port`.
        - `stop_stream`  : stops active `ffmpeg -re` UDP stream and purges the consumed item.
        """
        ev_type = str(event_payload.get("event") or event_payload.get("action") or "")
        dom_raw = str(event_payload.get("domain") or "games")
        item_id = str(event_payload.get("item_id") or "")
        domain: DomainType = dom_raw if dom_raw in VALID_DOMAINS else "games"  # type: ignore[assignment]

        declared_ip = str(event_payload.get("udp_ip") or event_payload.get("client_ip") or "").strip()
        if declared_ip and declared_ip not in ("0.0.0.0", "127.0.0.1"):
            self.client_udp_ips[dmd_id] = declared_ip
        if (dmd_id, domain) in self.client_configs:
            self.client_transports[(dmd_id, domain)] = "MQTT+UDP"
            self.client_last_seen[(dmd_id, domain)] = time.time()

        if ev_type == "start_card":
            pkg = await self.claim_next_package(dmd_id, domain, item_id=item_id)
            logger.info(
                "[ORCHESTRATOR] Event 'start_card' from dmd='%s' domain='%s' item='%s' -> claimed=%s (N+1 background prep triggered)",
                dmd_id,
                domain,
                item_id,
                pkg.candidate.title if pkg else "none",
            )
            if pkg and pkg.card.stream_frames == 0:
                await self.purge_consumed_package(pkg.item_id)

        elif ev_type == "start_stream":
            pkg = self.active_packages.get(item_id)
            if pkg is None:
                pkg = await self.claim_next_package(dmd_id, domain, item_id=item_id)
            if pkg is None or not pkg.unified_stream_path or not pkg.unified_stream_path.exists():
                logger.warning(
                    "[ORCHESTRATOR] Event 'start_stream' from dmd='%s' for '%s', but stream file is missing -> sending stream_eof",
                    dmd_id,
                    item_id,
                )
                eof_msg = {"event": "stream_eof", "item_id": item_id, "domain": domain}
                await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/{domain}/events", eof_msg, retain=False)
                await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/stream_state", eof_msg, retain=False)
                if item_id:
                    await self.purge_consumed_package(item_id)
                return

            if not pkg.consumed:
                await self.claim_next_package(dmd_id, domain, item_id=item_id)

            udp_ip = _select_best_udp_ip(
                declared_ip,
                self.client_udp_ips.get(dmd_id),
                str(event_payload.get("_peer_ip") or ""),
            )
            udp_port = int(event_payload.get("udp_port") or 4210)
            logger.info(
                "[ORCHESTRATOR] Event 'start_stream' from dmd='%s' item='%s' ('%s') -> streaming %d frames @ %dfps to UDP %s:%d",
                dmd_id,
                pkg.item_id,
                pkg.candidate.title,
                pkg.card.stream_frames,
                pkg.card.stream_fps,
                udp_ip,
                udp_port,
            )
            started_msg = {"event": "stream_started", "item_id": pkg.item_id, "domain": domain}
            await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/{domain}/events", started_msg, retain=False)
            await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/stream_state", started_msg, retain=False)

            async def _on_stream_eof(client_dmd_id: str, finished_item_id: str, _ok: bool) -> None:
                self.last_claimed_until.pop((client_dmd_id, domain), None)
                eof_msg = {"event": "stream_eof", "item_id": finished_item_id, "domain": domain}
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{client_dmd_id}/{domain}/events",
                    eof_msg,
                    retain=False,
                )
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{client_dmd_id}/stream_state",
                    eof_msg,
                    retain=False,
                )
                await self.purge_consumed_package(finished_item_id)

            await self.streamer.start_udp_clock_stream(
                dmd_id=dmd_id,
                item_id=pkg.item_id,
                stream_path=pkg.unified_stream_path,
                width=pkg.geometry.width,
                height=pkg.geometry.height,
                fps=pkg.card.stream_fps,
                udp_ip=udp_ip,
                udp_port=udp_port,
                on_eof=_on_stream_eof,
            )

        elif ev_type == "stop_stream":
            logger.info("[ORCHESTRATOR] Event 'stop_stream' from dmd='%s' item='%s'", dmd_id, item_id)
            self.last_claimed_until.pop((dmd_id, domain), None)
            await self.streamer.stop_stream(dmd_id)
            if item_id:
                await self.purge_consumed_package(item_id)
            else:
                consumed_ids = [
                    aid
                    for aid, pkg in list(self.active_packages.items())
                    if pkg.dmd_id == dmd_id and pkg.domain == domain and pkg.consumed
                ]
                for aid in consumed_ids:
                    await self.purge_consumed_package(aid)

        elif ev_type == "client_offline":
            logger.info(
                "[ORCHESTRATOR] DMD client '%s' disconnected -> stopping active UDP streams (preserving retained slot prep)",
                dmd_id,
            )
            self.client_online[dmd_id] = False
            abort_ev = self._director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            await self._cleanup_abandoned_active_packages(dmd_id)

        elif ev_type == "client_online":
            self.client_online[dmd_id] = True
            self._wake_director(dmd_id)

        elif ev_type in ("card_done", "item_done"):
            if item_id:
                await self.purge_consumed_package(item_id)
            abort_ev = self._director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()

    # ── Priority On-Demand Queue & Server-Managed Passive Modes (2A / 2B) ─────

    def _wake_director(self, dmd_id: str) -> None:
        ev = self._director_wake_events.get(dmd_id)
        if ev is not None:
            ev.set()

    def _resolve_target_slot_dmd_id(self, dmd_id: str, domain: DomainType) -> str:
        """
        For Mode 1 multi-engine boards (e.g. `esp32_games_128x32` + `esp32_movies_128x32`),
        map `(dmd_id, domain)` to the matching per-domain runner ID when registered.
        """
        if (dmd_id, domain) in self.client_configs or dmd_id in self._server_managed_ids:
            return dmd_id
        m = _PER_ENGINE_DMD_ID_RE.match(dmd_id)
        if m:
            sibling = f"{m.group('prefix').lower()}_{domain}_{m.group('geom').lower()}"
            if (sibling, domain) in self.client_configs:
                return sibling
        board_key = canonical_dmd_board_key(dmd_id, self.client_udp_ips.get(dmd_id, ""))
        for (other_id, other_dom) in self.client_configs:
            if other_dom == domain:
                other_board = canonical_dmd_board_key(other_id, self.client_udp_ips.get(other_id, ""))
                if other_board == board_key:
                    return other_id
        return dmd_id

    def _pop_claimed_priority_entry(self, slot_dmd_id: str, item_id: str) -> None:
        for q in self.priority_queues.values():
            for entry in list(q):
                if (
                    entry.package is not None
                    and entry.package.item_id == item_id
                    and entry.target_slot_dmd_id == slot_dmd_id
                ):
                    try:
                        q.remove(entry)
                    except ValueError:
                        pass
                    return

    def _take_next_ready_priority_package_for_slot(
        self, slot_dmd_id: str, domain: DomainType
    ) -> Optional[PreparedShowcasePackage]:
        for q in self.priority_queues.values():
            for entry in q:
                if (
                    entry.target_slot_dmd_id == slot_dmd_id
                    and entry.domain == domain
                    and entry.status == "ready"
                    and entry.package is not None
                    and not entry.package.consumed
                ):
                    return entry.package
        return None

    async def _build_package_from_candidate(
        self,
        dmd_id: str,
        domain: DomainType,
        cand: MediaCandidate,
        geometry: PanelGeometry,
        criteria: FilterCriteria,
    ) -> Optional[PreparedShowcasePackage]:
        item_id = f"pkg_{domain}_{uuid.uuid4().hex[:8]}_{cand.id}"
        item_dir = settings.cache_dir / "consume_once" / dmd_id / domain / item_id
        sem = self._get_transcode_semaphore()
        self._queued_jobs += 1
        queued_decremented = False
        try:
            await sem.acquire()
            self._queued_jobs = max(0, self._queued_jobs - 1)
            queued_decremented = True
            self._active_workers += 1
            t_start = time.monotonic()
            try:
                files_map = await self.transcoder.transcode_candidate(
                    cand, criteria, geometry, item_dir
                )
            finally:
                elapsed_s = max(0.0, time.monotonic() - t_start)
                self._active_workers = max(0, self._active_workers - 1)
                self._completed_jobs_total += 1
                self._total_job_duration_s += elapsed_s
                sem.release()
        finally:
            if not queued_decremented:
                self._queued_jobs = max(0, self._queued_jobs - 1)

        if not item_dir.exists():
            return None

        poster_w, poster_h = geometry.resolved_poster_dims()
        card = ShowcaseCard(
            item_id=item_id,
            domain=domain,
            title=cand.title,
            year=cand.year,
            badge=cand.badge,
            badge_color=cand.badge_color,
            subtitle=cand.subtitle,
            rating=cand.rating,
            synopsis=cand.synopsis if criteria.include_synopsis else "",
            lang=criteria.lang,
            source_used=cand.source_id,
            poster_width=poster_w,
            poster_height=poster_h,
            poster_rgb565_b64=str(files_map.get("poster_rgb565_b64") or ""),
            text_scroll_speed=criteria.text_scroll_speed,
            has_panoramas=bool(files_map.get("has_panoramas")),
            has_video=bool(files_map.get("has_video")),
            stream_frames=int(files_map.get("stream_frames") or 0),
            stream_fps=int(files_map.get("stream_fps") or criteria.video_fps),
            stream_duration_s=float(files_map.get("stream_duration_s") or 0.0),
        )
        unified_path_str = str(files_map.get("unified_stream_path") or "")
        return PreparedShowcasePackage(
            item_id=item_id,
            domain=domain,
            dmd_id=dmd_id,
            candidate=cand,
            card=card,
            geometry=geometry,
            item_dir=item_dir,
            unified_stream_path=Path(unified_path_str) if unified_path_str else None,
            files_map=files_map,
        )

    def list_queue(self, dmd_id: str) -> List[Dict[str, Any]]:
        q = self.priority_queues.get(dmd_id)
        if not q:
            return []
        return [entry.to_dict() for entry in list(q)]

    async def remove_queue_item(self, dmd_id: str, queue_id: str) -> bool:
        q = self.priority_queues.get(dmd_id)
        if not q:
            return False
        target: Optional[QueuedOnDemandEntry] = None
        for entry in list(q):
            if entry.queue_id == queue_id:
                target = entry
                q.remove(entry)
                break
        if target is None:
            return False

        self._safe_cancel_task(target.prep_task)
        if target.package is not None:
            pkg = target.package
            self.package_mark_seen.pop(pkg.item_id, None)
            self.priority_item_ids.discard(pkg.item_id)
            slot_key = (target.target_slot_dmd_id, target.domain)
            if self.ready_packages.get(slot_key) is pkg:
                self.ready_packages.pop(slot_key, None)
                standby_pkg = self.standby_packages.pop(slot_key, None)
                if standby_pkg is not None and not standby_pkg.consumed:
                    self.ready_packages[slot_key] = standby_pkg
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{target.target_slot_dmd_id}/{target.domain}/ready",
                        {
                            "ready": True,
                            "item_id": standby_pkg.item_id,
                            "domain": target.domain,
                            "card": standby_pkg.card.to_dict(),
                        },
                        retain=True,
                    )
                else:
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{target.target_slot_dmd_id}/{target.domain}/ready",
                        {"ready": False, "item_id": "", "domain": target.domain},
                        retain=True,
                    )
                self._schedule_preparation(target.target_slot_dmd_id, target.domain)
            if not pkg.consumed:
                self.active_packages.pop(pkg.item_id, None)
                shutil.rmtree(pkg.item_dir, ignore_errors=True)
        return True

    def _prune_studio_previews(self, max_entries: int = 12, ttl_s: float = 1800.0) -> None:
        now = time.time()
        expired = [
            pid
            for pid, p in list(self.studio_previews.items())
            if (now - p.created_at) > ttl_s or not p.preview_dir.exists()
        ]
        for pid in expired:
            entry = self.studio_previews.pop(pid, None)
            if entry is not None:
                shutil.rmtree(entry.preview_dir, ignore_errors=True)
        while len(self.studio_previews) > max_entries:
            oldest_pid = min(
                self.studio_previews.keys(),
                key=lambda k: self.studio_previews[k].created_at,
            )
            entry = self.studio_previews.pop(oldest_pid, None)
            if entry is not None:
                shutil.rmtree(entry.preview_dir, ignore_errors=True)

    def _resolve_studio_candidate_fallback(
        self, req: StudioCardPreviewRequest
    ) -> MediaCandidate:
        if req.candidate_id:
            staged = self.chain_service.get_staged_candidate(req.candidate_id)
            if staged is not None:
                return staged
        palette = _parse_hex_rgb(req.badge_color)
        return MediaCandidate(
            id=req.candidate_id or f"studio_{req.domain}_{uuid.uuid4().hex[:6]}",
            domain=req.domain,
            title=req.title or "Super Street Fighter II Turbo",
            year=req.year or "1994",
            badge=req.badge or req.domain.upper(),
            badge_color=req.badge_color or "#FF6D00",
            subtitle=req.subtitle or "",
            rating=req.rating or "",
            synopsis=req.synopsis or "",
            lang=req.lang or "fr",
            poster_url=req.poster_url,
            jacket_url=req.poster_url,
            source_id="studio_preview",
            theme_palette=palette,
        )

    async def build_studio_card_preview(
        self, req: StudioCardPreviewRequest
    ) -> Tuple[bytes, List[Dict[str, object]]]:
        """
        Fast (< 100 ms) Card-only `DMDS` preview bypassing the heavy video transcode semaphore.
        """
        cand = self._resolve_studio_candidate_fallback(req)
        geom = PanelGeometry(width=req.width, height=req.height)
        poster_w, poster_h = geom.resolved_poster_dims()
        loop = asyncio.get_running_loop()
        dmds_bytes, n_frames, eff_fps, dur_s = await loop.run_in_executor(
            None,
            build_fast_studio_card_preview_sync,
            str(settings.cache_dir),
            geom.width,
            geom.height,
            req.fps,
            cand.title,
            cand.year,
            cand.badge,
            cand.badge_color,
            cand.subtitle,
            cand.rating,
            cand.synopsis,
            cand.resolved_jacket_url(),
            poster_w,
            poster_h,
            req.include_card_intro,
            req.card_layout,
            req.card_theme,
            req.card_font,
            req.card_accent_color,
            req.include_poster,
            req.show_badge,
            req.show_year,
            req.show_rating,
            req.include_synopsis,
            req.text_scroll_speed,
            req.card_hold_s,
            req.color_boost,
        )
        chapters: List[Dict[str, object]] = [
            {
                "id": "card",
                "label": "1. Card Info",
                "start_frame": 0,
                "frames": n_frames,
                "duration_s": dur_s,
            }
        ]
        return dmds_bytes, chapters

    async def build_studio_full_preview(
        self, req: StudioFullPreviewRequest
    ) -> Tuple[bytes, str, List[Dict[str, object]]]:
        """
        Build the full customized `DMDS` sequence (`Card + Panoramas + 0..5 Snapshots + Video`)
        and cache it under `preview_id` so clicking 'Add Simulation to Active DMD Queue'
        promotes it in 0 ms without re-transcoding.
        """
        self._prune_studio_previews()
        raw_dict = {**req.model_dump(), "render_card_in_stream": True}
        criteria = self._parse_domain_criteria(req.domain, raw_dict, top_lang=req.lang)
        geom = PanelGeometry(width=req.width, height=req.height)

        cand: Optional[MediaCandidate] = None
        if req.candidate_id:
            cand = self.chain_service.get_staged_candidate(req.candidate_id)
        if cand is None and req.query.strip():
            found = await self.chain_service.search_candidates(
                criteria=criteria, query=req.query.strip(), limit=1
            )
            if found:
                cand = found[0]
        if cand is None:
            cand = self._resolve_studio_candidate_fallback(req)

        preview_id = f"sim_{uuid.uuid4().hex[:12]}"
        preview_dir = settings.cache_dir / "studio_previews" / preview_id
        sem = self._get_transcode_semaphore()
        self._queued_jobs += 1
        queued_decremented = False
        try:
            await sem.acquire()
            self._queued_jobs = max(0, self._queued_jobs - 1)
            queued_decremented = True
            self._active_workers += 1
            t0 = time.monotonic()
            try:
                files_map = await self.transcoder.transcode_candidate(
                    cand, criteria, geom, preview_dir
                )
            finally:
                elapsed = max(0.0, time.monotonic() - t0)
                self._active_workers = max(0, self._active_workers - 1)
                self._completed_jobs_total += 1
                self._total_job_duration_s += elapsed
                sem.release()
        finally:
            if not queued_decremented:
                self._queued_jobs = max(0, self._queued_jobs - 1)

        unified_path = Path(str(files_map.get("unified_stream_path") or ""))
        if not unified_path.exists() or int(files_map.get("stream_frames") or 0) == 0:
            # Ensure a valid non-empty DMDS stream even if all segments were toggled OFF
            dmds_bytes, fallback_chapters = await self.build_studio_card_preview(req)
            unified_path = preview_dir / f"unified_{geom.width}x{geom.height}.rgb565.stream"
            preview_dir.mkdir(parents=True, exist_ok=True)
            unified_path.write_bytes(dmds_bytes)
            files_map["unified_stream_path"] = str(unified_path)
            files_map["stream_frames"] = int(fallback_chapters[0]["frames"])  # type: ignore[arg-type]
            files_map["stream_duration_s"] = float(fallback_chapters[0]["duration_s"])  # type: ignore[arg-type]
            files_map["stream_chapters"] = fallback_chapters

        chapters = list(files_map.get("stream_chapters") or [])  # type: ignore[arg-type]
        entry = StudioPreviewEntry(
            preview_id=preview_id,
            domain=req.domain,
            candidate=cand,
            criteria=criteria,
            geometry=geom,
            preview_dir=preview_dir,
            files_map=files_map,
            chapters=chapters,
        )
        self.studio_previews[preview_id] = entry
        self._prune_studio_previews()
        return unified_path.read_bytes(), preview_id, chapters

    def _clone_package_from_studio_preview(
        self,
        prev: StudioPreviewEntry,
        target_slot_dmd_id: str,
        render_card_in_stream: bool,
    ) -> Optional[PreparedShowcasePackage]:
        if not prev.preview_dir.exists():
            return None
        item_id = f"pkg_{prev.domain}_{uuid.uuid4().hex[:8]}_{prev.candidate.id}"
        item_dir = settings.cache_dir / "consume_once" / target_slot_dmd_id / prev.domain / item_id
        item_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prev.preview_dir, item_dir, dirs_exist_ok=True)

        files_map = dict(prev.files_map)
        unified_name = f"unified_{prev.geometry.width}x{prev.geometry.height}.rgb565.stream"
        unified_path = item_dir / unified_name

        # If target DMD renders its own local Card (Mode 1 / 2A), strip the server-rendered Card segment in < 2 ms
        if not render_card_in_stream and files_map.get("card_stream_rgb565"):
            non_card_paths: List[Path] = []
            for pname in files_map.get("panorama_streams_rgb565") or []:  # type: ignore[union-attr]
                pp = item_dir / str(pname)
                if pp.exists():
                    non_card_paths.append(pp)
            vname = str(files_map.get("video_stream_rgb565") or "")
            if vname and (item_dir / vname).exists():
                non_card_paths.append(item_dir / vname)
            if non_card_paths:
                eff_fps = int(files_map.get("stream_fps") or prev.criteria.video_fps or 15)
                n_frames, dur_s = merge_rgb565_streams(
                    non_card_paths,
                    unified_path,
                    prev.geometry.width,
                    prev.geometry.height,
                    eff_fps,
                )
                files_map["stream_frames"] = n_frames
                files_map["stream_duration_s"] = dur_s

        files_map["unified_stream_path"] = str(unified_path)
        poster_w, poster_h = prev.geometry.resolved_poster_dims()
        card = ShowcaseCard(
            item_id=item_id,
            domain=prev.domain,
            title=prev.candidate.title,
            year=prev.candidate.year,
            badge=prev.candidate.badge,
            badge_color=prev.candidate.badge_color,
            subtitle=prev.candidate.subtitle,
            rating=prev.candidate.rating,
            synopsis=prev.candidate.synopsis if prev.criteria.include_synopsis else "",
            lang=prev.criteria.lang,
            source_used=prev.candidate.source_id,
            poster_width=poster_w,
            poster_height=poster_h,
            poster_rgb565_b64=str(files_map.get("poster_rgb565_b64") or ""),
            text_scroll_speed=prev.criteria.text_scroll_speed,
            has_panoramas=bool(files_map.get("has_panoramas")),
            has_video=bool(files_map.get("has_video")),
            stream_frames=int(files_map.get("stream_frames") or 0),
            stream_fps=int(files_map.get("stream_fps") or prev.criteria.video_fps),
            stream_duration_s=float(files_map.get("stream_duration_s") or 0.0),
        )
        return PreparedShowcasePackage(
            item_id=item_id,
            domain=prev.domain,
            dmd_id=target_slot_dmd_id,
            candidate=prev.candidate,
            card=card,
            geometry=prev.geometry,
            item_dir=item_dir,
            unified_stream_path=unified_path if unified_path.exists() else None,
            files_map=files_map,
        )

    async def _activate_ready_priority_entry(
        self,
        entry: QueuedOnDemandEntry,
        pkg: PreparedShowcasePackage,
        is_server_managed: bool,
    ) -> None:
        self.package_mark_seen[pkg.item_id] = entry.mark_seen
        self.priority_item_ids.add(pkg.item_id)
        self.active_packages[pkg.item_id] = pkg
        entry.package = pkg
        entry.status = "ready"

        if is_server_managed:
            if entry.mode == "play_now":
                self._director_skip_gap.add(entry.dmd_id)
                await self.streamer.stop_stream(entry.dmd_id)
                abort_ev = self._director_play_abort_events.get(entry.dmd_id)
                if abort_ev is not None:
                    abort_ev.set()
            self._wake_director(entry.dmd_id)
            return

        # Mode 1 (Client-Driven DMD): promote head priority item into `ready_packages`
        slot_key = (entry.target_slot_dmd_id, entry.domain)
        current_ready = self.ready_packages.get(slot_key)
        should_promote = (
            entry.mode == "play_now"
            or current_ready is None
            or current_ready.item_id not in self.priority_item_ids
        )
        if should_promote:
            if current_ready is not None and not current_ready.consumed:
                if current_ready.item_id not in self.priority_item_ids:
                    old_standby = self.standby_packages.get(slot_key)
                    if old_standby is not None and not old_standby.consumed:
                        self.active_packages.pop(old_standby.item_id, None)
                        shutil.rmtree(old_standby.item_dir, ignore_errors=True)
                    self.standby_packages[slot_key] = current_ready

            self.ready_packages[slot_key] = pkg
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/{entry.domain}/ready",
                {
                    "ready": True,
                    "item_id": pkg.item_id,
                    "domain": entry.domain,
                    "on_demand": True,
                    "card": pkg.card.to_dict(),
                },
                retain=True,
            )

        if entry.mode == "play_now":
            await self.streamer.stop_stream(entry.target_slot_dmd_id)
            eof_msg = {
                "event": "stream_eof",
                "action": "preempt_play_now",
                "item_id": pkg.item_id,
                "domain": entry.domain,
            }
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/{entry.domain}/events",
                eof_msg,
                retain=False,
            )
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/stream_state",
                eof_msg,
                retain=False,
            )
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/command",
                {
                    "action": "play_now",
                    "domain": entry.domain,
                    "item_id": pkg.item_id,
                    "card": pkg.card.to_dict(),
                },
                retain=False,
            )

    async def enqueue_on_demand(self, dmd_id: str, req: QueueAddRequest) -> Dict[str, Any]:
        """
        Enqueue (`mode='enqueue_next'`) or immediately preempt (`mode='play_now'`) a specific
        media item on `dmd_id` using:
        - `preview_id`: 0 ms zero-retranscode promotion of an already-rendered Studio simulation,
        - `candidate_id`: exact staged candidate from `/search`, or
        - `{domain, query, systems, lang, mark_seen}`: 1-shot lookup for `FrontendSync` / webhooks.
        """
        cached_sim = self.studio_previews.get(req.preview_id) if req.preview_id else None
        eff_domain: DomainType = cached_sim.domain if cached_sim is not None else req.domain

        prof = self.profile_store.get_profile(dmd_id)
        is_server_managed = bool(
            (prof and prof.get("config_source") == "server") or dmd_id in self._server_managed_ids
        )
        target_slot_dmd_id = (
            dmd_id if is_server_managed else self._resolve_target_slot_dmd_id(dmd_id, eff_domain)
        )

        # Resolve geometry and base criteria from profile or registered client config
        existing_cfg = self.client_configs.get((target_slot_dmd_id, eff_domain))
        if existing_cfg is None:
            for (oid, _odom), pair in self.client_configs.items():
                if oid == target_slot_dmd_id or oid == dmd_id:
                    existing_cfg = pair
                    break

        if prof is not None:
            base_w = int(req.width or prof.get("width") or 128)
            base_h = int(req.height or prof.get("height") or 32)
            base_lang = str(req.lang or prof.get("lang") or "fr").strip().lower()
            matching_slot = next(
                (s for s in (prof.get("playlist") or []) if s.get("domain") == eff_domain),
                {},
            )
            raw_merged: Dict[str, Any] = {
                **(prof.get("card_style") or {}),
                **matching_slot,
            }
            render_card = bool(
                is_server_managed and not bool(prof.get("supports_local_card", True))
            )
        elif existing_cfg is not None:
            base_geom, base_crit = existing_cfg
            base_w = int(req.width or base_geom.width)
            base_h = int(req.height or base_geom.height)
            base_lang = str(req.lang or base_crit.lang or "fr").strip().lower()
            raw_merged = {
                "systems": list(base_crit.systems),
                "genres": list(base_crit.genres),
                "decades": list(base_crit.decades),
                "min_rating": base_crit.min_rating,
                "video_type": base_crit.video_type,
                "include_card_intro": base_crit.include_card_intro,
                "card_layout": base_crit.card_layout,
                "card_theme": base_crit.card_theme,
                "card_font": base_crit.card_font,
                "card_accent_color": base_crit.card_accent_color,
                "show_badge": base_crit.show_badge,
                "show_year": base_crit.show_year,
                "show_rating": base_crit.show_rating,
                "card_hold_s": base_crit.card_hold_s,
                "include_poster": base_crit.include_poster,
                "include_synopsis": base_crit.include_synopsis,
                "text_scroll_speed": base_crit.text_scroll_speed,
                "jacket_panorama": base_crit.jacket_panorama,
                "marquee_panorama": base_crit.marquee_panorama,
                "snapshot_panorama_count": base_crit.snapshot_panorama_count,
                "panorama_duration_s": base_crit.panorama_duration_s,
                "include_video": base_crit.include_video,
                "video_max_duration_s": base_crit.video_max_duration_s,
                "video_fps": base_crit.video_fps,
                "video_mode": base_crit.video_mode,
                "color_boost": base_crit.color_boost,
            }
            render_card = base_crit.render_card_in_stream
        else:
            base_w = int(req.width or (cached_sim.geometry.width if cached_sim else 128))
            base_h = int(req.height or (cached_sim.geometry.height if cached_sim else 32))
            base_lang = str(
                req.lang
                or (cached_sim.criteria.lang if cached_sim else settings.default_lang)
                or "fr"
            ).strip().lower()
            raw_merged = {}
            render_card = bool(cached_sim is not None)

        if cached_sim is not None:
            # Inherit full Studio simulation settings unless explicitly overridden on the queue request
            raw_merged.update(
                {
                    "include_card_intro": cached_sim.criteria.include_card_intro,
                    "card_layout": cached_sim.criteria.card_layout,
                    "card_theme": cached_sim.criteria.card_theme,
                    "card_font": cached_sim.criteria.card_font,
                    "card_accent_color": cached_sim.criteria.card_accent_color,
                    "show_badge": cached_sim.criteria.show_badge,
                    "show_year": cached_sim.criteria.show_year,
                    "show_rating": cached_sim.criteria.show_rating,
                    "card_hold_s": cached_sim.criteria.card_hold_s,
                    "include_poster": cached_sim.criteria.include_poster,
                    "include_synopsis": cached_sim.criteria.include_synopsis,
                    "text_scroll_speed": cached_sim.criteria.text_scroll_speed,
                    "jacket_panorama": cached_sim.criteria.jacket_panorama,
                    "marquee_panorama": cached_sim.criteria.marquee_panorama,
                    "snapshot_panorama_count": cached_sim.criteria.snapshot_panorama_count,
                    "panorama_duration_s": cached_sim.criteria.panorama_duration_s,
                    "include_video": cached_sim.criteria.include_video,
                    "video_max_duration_s": cached_sim.criteria.video_max_duration_s,
                    "video_fps": cached_sim.criteria.video_fps,
                    "video_mode": cached_sim.criteria.video_mode,
                    "color_boost": cached_sim.criteria.color_boost,
                }
            )

        # Apply explicit request overrides
        raw_merged["lang"] = base_lang
        raw_merged["render_card_in_stream"] = render_card
        if req.systems:
            raw_merged["systems"] = req.systems
        elif req.query.strip():
            raw_merged["systems"] = []
        if req.genres:
            raw_merged["genres"] = req.genres
        elif req.query.strip():
            raw_merged["genres"] = []
        if req.decades:
            raw_merged["decades"] = req.decades
        elif req.query.strip():
            raw_merged["decades"] = []
        if req.video_type and req.video_type != "any":
            raw_merged["video_type"] = req.video_type
        for field_name in (
            "include_card_intro",
            "card_layout",
            "card_theme",
            "card_font",
            "card_accent_color",
            "show_badge",
            "show_year",
            "show_rating",
            "card_hold_s",
            "include_poster",
            "include_synopsis",
            "text_scroll_speed",
            "jacket_panorama",
            "marquee_panorama",
            "snapshot_panorama_count",
            "panorama_duration_s",
            "include_video",
            "video_max_duration_s",
            "video_fps",
            "video_mode",
            "color_boost",
        ):
            override_val = getattr(req, field_name, None)
            if override_val is not None:
                raw_merged[field_name] = override_val

        geometry = PanelGeometry(width=base_w, height=base_h)
        criteria = self._parse_domain_criteria(eff_domain, raw_merged, top_lang=base_lang)

        # Ensure a Mode 1 slot exists if the client was not yet registered for this domain
        if not is_server_managed and (target_slot_dmd_id, eff_domain) not in self.client_configs:
            self.client_configs[(target_slot_dmd_id, eff_domain)] = (geometry, criteria)

        from_studio = bool(
            cached_sim is not None
            or req.preview_id
            or req.card_layout is not None
            or req.card_theme is not None
        )

        # Fast-Path (0 ms Zero-Retranscode): reuse Studio full-preview artifact when geometry matches!
        if (
            cached_sim is not None
            and cached_sim.preview_dir.exists()
            and cached_sim.geometry.width == geometry.width
            and cached_sim.geometry.height == geometry.height
        ):
            cloned_pkg = self._clone_package_from_studio_preview(
                cached_sim,
                target_slot_dmd_id=target_slot_dmd_id,
                render_card_in_stream=render_card,
            )
            if cloned_pkg is not None:
                entry = QueuedOnDemandEntry(
                    queue_id=f"q_{uuid.uuid4().hex[:10]}",
                    dmd_id=dmd_id,
                    target_slot_dmd_id=target_slot_dmd_id,
                    domain=eff_domain,
                    candidate=cached_sim.candidate,
                    criteria=criteria,
                    geometry=geometry,
                    mode=req.mode,
                    mark_seen=req.mark_seen,
                    from_studio_sim=True,
                    status="ready",
                    package=cloned_pkg,
                )
                q = self.priority_queues.setdefault(dmd_id, deque())
                if req.mode == "play_now":
                    q.appendleft(entry)
                else:
                    q.append(entry)
                await self._activate_ready_priority_entry(
                    entry, cloned_pkg, is_server_managed=is_server_managed
                )
                logger.info(
                    "[ORCHESTRATOR] Promoted Studio simulation '%s' ('%s') into '%s' queue in 0ms (zero re-transcode)",
                    cached_sim.preview_id,
                    cached_sim.candidate.title,
                    dmd_id,
                )
                return entry.to_dict()

        # Resolve MediaCandidate (cached Studio sim OR staged candidate_id OR 1-shot query)
        cand: Optional[MediaCandidate] = cached_sim.candidate if cached_sim is not None else None
        if cand is None and req.candidate_id:
            cand = self.chain_service.get_staged_candidate(req.candidate_id)
            if cand is not None and req.lang and cand.lang != criteria.lang and req.query:
                localized = await self.chain_service.search_candidates(
                    criteria=criteria, query=req.query, limit=3
                )
                cand = next(
                    (c for c in localized if c.id == req.candidate_id),
                    localized[0] if localized else cand,
                )

        if cand is None:
            if req.candidate_id and not req.query.strip():
                raise ValueError(
                    f"Candidate '{req.candidate_id}' expired or not found; please run /search again."
                )
            found = await self.chain_service.search_candidates(
                criteria=criteria,
                query=req.query,
                limit=1,
            )
            if not found and req.query.strip():
                one = await self.chain_service.search_on_demand(
                    domain=eff_domain, query=req.query.strip(), lang=criteria.lang
                )
                if one is not None:
                    found = [one]
            if not found:
                raise ValueError(
                    f"No matching media found in '{eff_domain}' for query='{req.query}' (lang='{criteria.lang}')."
                )
            cand = found[0]

        entry = QueuedOnDemandEntry(
            queue_id=f"q_{uuid.uuid4().hex[:10]}",
            dmd_id=dmd_id,
            target_slot_dmd_id=target_slot_dmd_id,
            domain=eff_domain,
            candidate=cand,
            criteria=criteria,
            geometry=geometry,
            mode=req.mode,
            mark_seen=req.mark_seen,
            from_studio_sim=from_studio,
            status="preparing",
        )
        q = self.priority_queues.setdefault(dmd_id, deque())
        if req.mode == "play_now":
            q.appendleft(entry)
        else:
            q.append(entry)

        loop = asyncio.get_running_loop()
        entry.prep_task = loop.create_task(
            self._prepare_priority_entry(entry, is_server_managed=is_server_managed)
        )
        try:
            await asyncio.wait_for(asyncio.shield(entry.prep_task), timeout=0.15)
        except asyncio.TimeoutError:
            pass
        return entry.to_dict()

    async def _prepare_priority_entry(
        self,
        entry: QueuedOnDemandEntry,
        is_server_managed: bool,
    ) -> None:
        try:
            pkg = await self._build_package_from_candidate(
                dmd_id=entry.target_slot_dmd_id,
                domain=entry.domain,
                cand=entry.candidate,
                geometry=entry.geometry,
                criteria=entry.criteria,
            )
            q = self.priority_queues.get(entry.dmd_id)
            if pkg is None or q is None or entry not in q:
                if pkg is not None:
                    shutil.rmtree(pkg.item_dir, ignore_errors=True)
                return

            await self._activate_ready_priority_entry(
                entry, pkg, is_server_managed=is_server_managed
            )
        except asyncio.CancelledError:
            logger.info("[ORCHESTRATOR] Cancelled priority queue prep '%s'", entry.queue_id)
        except Exception as exc:
            logger.error("[ORCHESTRATOR] Failed preparing priority queue item '%s': %s", entry.queue_id, exc)

    async def start_server_managed_profiles(self) -> None:
        """Start background `ServerManagedDirector` loops for all `config_source='server'` DMD profiles."""
        await self.sync_server_managed_profiles()

    async def stop_server_managed_profiles(self) -> None:
        """Stop all active `ServerManagedDirector` loops and UDP streams cleanly on shutdown."""
        for dmd_id, task in list(self._director_tasks.items()):
            self._safe_cancel_task(task)
            pf_task = self._director_prefetch_tasks.pop(dmd_id, None)
            self._safe_cancel_task(pf_task)
            await self.streamer.stop_stream(dmd_id)
        self._director_tasks.clear()
        self._server_managed_ids.clear()

    async def sync_server_managed_profiles(self) -> None:
        """Synchronize running `ServerManagedDirector` tasks with `dmd_profiles.json`."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        profiles = self.profile_store.list_profiles()
        desired_server_ids = {
            p["dmd_id"] for p in profiles if p.get("config_source") == "server"
        }

        # Stop directors for profiles removed or switched back to `client` mode
        for dmd_id in list(self._server_managed_ids - desired_server_ids):
            self._server_managed_ids.discard(dmd_id)
            task = self._director_tasks.pop(dmd_id, None)
            self._safe_cancel_task(task)
            pf_task = self._director_prefetch_tasks.pop(dmd_id, None)
            self._safe_cancel_task(pf_task)
            pf_pkg = self._director_prefetched.pop(dmd_id, None)
            if pf_pkg is not None and not pf_pkg.consumed:
                self.active_packages.pop(pf_pkg.item_id, None)
                shutil.rmtree(pf_pkg.item_dir, ignore_errors=True)
            await self.streamer.stop_stream(dmd_id)

        # Start or wake directors for `server`-managed profiles
        for prof in profiles:
            dmd_id = prof["dmd_id"]
            if prof.get("udp_ip"):
                self.client_udp_ips[dmd_id] = str(prof["udp_ip"])
            self.client_udp_ports[dmd_id] = int(prof.get("udp_port") or 4210)
            if prof.get("config_source") != "server":
                continue

            self._server_managed_ids.add(dmd_id)
            # Cancel any competing Mode 1 prep tasks on this dmd_id
            for dom_k in VALID_DOMAINS:
                t = self._prep_tasks.pop((dmd_id, dom_k), None)
                self._safe_cancel_task(t)

            self._director_wake_events.setdefault(dmd_id, asyncio.Event())
            existing_task = self._director_tasks.get(dmd_id)
            if not self._is_task_live(existing_task, loop):
                self._safe_cancel_task(existing_task)
                self._director_tasks[dmd_id] = loop.create_task(
                    self._run_server_managed_loop(dmd_id)
                )
            else:
                self._wake_director(dmd_id)

    async def upsert_dmd_profile(self, dmd_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Save a DMD profile in `dmd_profiles.json`, invalidate incompatible prefetched media, and apply live."""
        prev = self.profile_store.get_profile(dmd_id)
        saved = self.profile_store.upsert_profile(dmd_id, data)
        if prev != saved:
            pf_task = self._director_prefetch_tasks.pop(dmd_id, None)
            self._safe_cancel_task(pf_task)
            pf_pkg = self._director_prefetched.pop(dmd_id, None)
            if pf_pkg is not None and not pf_pkg.consumed:
                self.active_packages.pop(pf_pkg.item_id, None)
                shutil.rmtree(pf_pkg.item_dir, ignore_errors=True)
        await self.sync_server_managed_profiles()
        self._wake_director(dmd_id)
        return saved

    async def delete_dmd_profile(self, dmd_id: str) -> bool:
        deleted = self.profile_store.delete_profile(dmd_id)
        await self.sync_server_managed_profiles()
        return deleted

    async def control_client(self, dmd_id: str, action: str) -> Dict[str, Any]:
        """Execute a live playback control (`skip`, `pause`, `resume`, `stop`) on `dmd_id`."""
        act = str(action or "").strip().lower()
        prof = self.profile_store.get_profile(dmd_id)

        if act == "pause":
            if prof is not None:
                self.profile_store.upsert_profile(dmd_id, {"paused": True})
            await self.streamer.stop_stream(dmd_id)
            abort_ev = self._director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/command",
                {"action": "pause", "dmd_id": dmd_id},
                retain=False,
            )
            self._wake_director(dmd_id)
            return {"dmd_id": dmd_id, "action": "pause", "paused": True}

        if act == "resume":
            if prof is not None:
                self.profile_store.upsert_profile(dmd_id, {"paused": False})
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/command",
                {"action": "resume", "dmd_id": dmd_id},
                retain=False,
            )
            self._wake_director(dmd_id)
            return {"dmd_id": dmd_id, "action": "resume", "paused": False}

        if act == "stop":
            if prof is not None:
                self.profile_store.upsert_profile(dmd_id, {"paused": True})
            await self._cleanup_abandoned_active_packages(dmd_id)
            abort_ev = self._director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/command",
                {"action": "stop", "dmd_id": dmd_id},
                retain=False,
            )
            self._wake_director(dmd_id)
            return {"dmd_id": dmd_id, "action": "stop", "paused": True}

        # Default: `skip`
        self._director_skip_gap.add(dmd_id)
        await self.streamer.stop_stream(dmd_id)
        abort_ev = self._director_play_abort_events.get(dmd_id)
        if abort_ev is not None:
            abort_ev.set()
        for dom_k in VALID_DOMAINS:
            sibling_id = self._resolve_target_slot_dmd_id(dmd_id, dom_k)
            await self.streamer.stop_stream(sibling_id)
            if (sibling_id, dom_k) in self.client_configs:
                eof_msg = {"event": "stream_eof", "action": "skip", "domain": dom_k}
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{sibling_id}/{dom_k}/events",
                    eof_msg,
                    retain=False,
                )
        await self.mqtt.publish_json(
            f"dmd_showcase/clients/{dmd_id}/command",
            {"action": "skip", "dmd_id": dmd_id},
            retain=False,
        )
        self._wake_director(dmd_id)
        return {"dmd_id": dmd_id, "action": "skip"}

    def _next_playlist_slot_criteria(
        self, dmd_id: str, prof: Dict[str, Any]
    ) -> Optional[Tuple[DomainType, PanelGeometry, FilterCriteria]]:
        enabled_slots = [
            s
            for s in (prof.get("playlist") or [])
            if s.get("enabled", True) and s.get("domain") in VALID_DOMAINS
        ]
        if not enabled_slots:
            return None

        slot_idx, used_in_slot = self._director_playlist_cursors.get(dmd_id, (0, 0))
        if slot_idx >= len(enabled_slots):
            slot_idx = 0
            used_in_slot = 0
        slot = enabled_slots[slot_idx]
        slot_count = max(1, int(slot.get("count") or 1))

        next_used = used_in_slot + 1
        if next_used >= slot_count:
            self._director_playlist_cursors[dmd_id] = ((slot_idx + 1) % len(enabled_slots), 0)
        else:
            self._director_playlist_cursors[dmd_id] = (slot_idx, next_used)

        dom: DomainType = slot["domain"]  # type: ignore[assignment]
        geometry = PanelGeometry(
            width=int(prof.get("width") or 128),
            height=int(prof.get("height") or 32),
        )
        base_card_style = dict(prof.get("card_style") or {})
        raw_slot = {
            **base_card_style,
            **slot,
            "lang": slot.get("lang") or prof.get("lang") or "fr",
            "render_card_in_stream": not bool(prof.get("supports_local_card", True)),
        }
        criteria = self._parse_domain_criteria(
            dom,
            raw_slot,
            top_lang=str(prof.get("lang") or "fr"),
        )
        return dom, geometry, criteria

    async def _prepare_next_playlist_package(
        self, dmd_id: str, prof: Dict[str, Any]
    ) -> Optional[PreparedShowcasePackage]:
        picked = self._next_playlist_slot_criteria(dmd_id, prof)
        if picked is None:
            return None
        domain, geometry, criteria = picked

        # Update client_configs for live WebUI telemetry visibility
        self.client_configs[(dmd_id, domain)] = (geometry, criteria)
        self.client_transports[(dmd_id, domain)] = (
            "UDP-PASSIVE" if criteria.render_card_in_stream else "MQTT+UDP"
        )

        active_exclude = {
            pkg.candidate.id
            for pkg in self.active_packages.values()
            if pkg.domain == domain
        }
        pf_existing = self._director_prefetched.get(dmd_id)
        if pf_existing is not None and pf_existing.domain == domain:
            active_exclude.add(pf_existing.candidate.id)

        candidates = await self.chain_service.acquire_unseen_candidates(
            criteria, count=1, extra_exclude_ids=active_exclude
        )
        if not candidates:
            return None
        pkg = await self._build_package_from_candidate(
            dmd_id=dmd_id,
            domain=domain,
            cand=candidates[0],
            geometry=geometry,
            criteria=criteria,
        )
        if pkg is not None:
            self.active_packages[pkg.item_id] = pkg
        return pkg

    async def _pop_next_ready_priority_for_director(
        self, dmd_id: str
    ) -> Optional[Tuple[QueuedOnDemandEntry, PreparedShowcasePackage]]:
        q = self.priority_queues.get(dmd_id)
        if not q:
            return None
        while q:
            entry = q[0]
            if entry.prep_task is not None and not entry.prep_task.done():
                try:
                    await entry.prep_task
                except Exception:
                    pass
            if not q or q[0] is not entry:
                continue
            q.popleft()
            if entry.package is not None and entry.package.item_dir.exists():
                entry.status = "playing"
                return entry, entry.package
        return None

    async def _run_server_managed_loop(self, dmd_id: str) -> None:
        """
        ServerManagedDirector coroutine driving passive ESP32/RPi receivers:
        - Mode 2B (`supports_local_card=False`): server renders Info Card + Panoramas + Video into
          RGB565 `DMDS` and pushes real-time UDP `DMDF` frames continuously to `udp_ip:udp_port`.
        - Mode 2A (`supports_local_card=True`): server orchestrates multi-domain playlist & On-Demand
          queue, sends localized Card JSON over MQTT `command`/`ready`, and streams UDP `DMDF` video.
        """
        wake_ev = self._director_wake_events.setdefault(dmd_id, asyncio.Event())
        try:
            while dmd_id in self._server_managed_ids:
                prof = self.profile_store.get_profile(dmd_id)
                if prof is None or prof.get("config_source") != "server":
                    return

                supports_local_card = bool(prof.get("supports_local_card", True))
                # Pre-warm 1 playlist slot even while waiting for DMD presence so boot/unpause is instant
                has_priority = bool(self.priority_queues.get(dmd_id))
                if not has_priority and dmd_id not in self._director_prefetched:
                    prefetched = await self._prepare_next_playlist_package(dmd_id, prof)
                    if prefetched is not None:
                        self._director_prefetched[dmd_id] = prefetched

                prof = self.profile_store.get_profile(dmd_id) or prof
                udp_ip = _select_best_udp_ip(
                    str(prof.get("udp_ip") or ""),
                    self.client_udp_ips.get(dmd_id),
                    "",
                )
                udp_port = int(prof.get("udp_port") or self.client_udp_ports.get(dmd_id, 4210))
                is_online = self.client_online.get(dmd_id, False) or bool(
                    prof.get("always_stream_udp", False)
                )
                can_play = (not bool(prof.get("paused", False))) and is_online

                if not can_play:
                    wake_ev.clear()
                    # Re-check condition after clear to avoid lost wakeup race
                    prof_recheck = self.profile_store.get_profile(dmd_id) or prof
                    still_blocked = bool(prof_recheck.get("paused", False)) or not (
                        self.client_online.get(dmd_id, False)
                        or bool(prof_recheck.get("always_stream_udp", False))
                    )
                    if still_blocked:
                        await wake_ev.wait()
                    continue

                # 1. Pop Priority On-Demand Queue item first, otherwise use prefetched playlist package
                prio_pair = await self._pop_next_ready_priority_for_director(dmd_id)
                if prio_pair is not None:
                    _entry, pkg = prio_pair
                else:
                    pkg = self._director_prefetched.pop(dmd_id, None)
                    if pkg is None or not pkg.item_dir.exists():
                        pkg = await self._prepare_next_playlist_package(dmd_id, prof)

                if pkg is None:
                    wake_ev.clear()
                    try:
                        await asyncio.wait_for(wake_ev.wait(), timeout=2.5)
                    except asyncio.TimeoutError:
                        pass
                    continue

                # 2. Launch N+1 background prefetch while item N plays
                loop = asyncio.get_running_loop()
                if (
                    dmd_id not in self._director_prefetched
                    and not self._is_task_live(self._director_prefetch_tasks.get(dmd_id), loop)
                ):
                    async def _bg_prefetch() -> None:
                        cur_prof = self.profile_store.get_profile(dmd_id)
                        if cur_prof and cur_prof.get("config_source") == "server":
                            nxt = await self._prepare_next_playlist_package(dmd_id, cur_prof)
                            if nxt is not None and dmd_id not in self._director_prefetched:
                                self._director_prefetched[dmd_id] = nxt

                    self._director_prefetch_tasks[dmd_id] = loop.create_task(_bg_prefetch())

                # 3. Broadcast item N according to Mode 2B (Passive UDP) vs Mode 2A (Hybrid Card + UDP)
                abort_ev = asyncio.Event()
                self._director_play_abort_events[dmd_id] = abort_ev
                now_ts = time.time()
                self.client_last_seen[(dmd_id, pkg.domain)] = now_ts
                self.last_played_titles[(dmd_id, pkg.domain)] = pkg.card.title

                if not supports_local_card:
                    # Mode 2B: 100% Passive UDP Framebuffer Receiver (Card Intro + Panoramas + Video)
                    pkg.consumed = True
                    should_mark = self.package_mark_seen.pop(pkg.item_id, True)
                    self.priority_item_ids.discard(pkg.item_id)
                    if should_mark:
                        self.seen_repo.mark_consumed(pkg.domain, pkg.candidate.id)

                    started_msg = {
                        "event": "stream_started",
                        "action": "play_stream",
                        "mode": "passive_udp",
                        "dmd_id": dmd_id,
                        "domain": pkg.domain,
                        "item_id": pkg.item_id,
                        "title": pkg.card.title,
                        "lang": pkg.card.lang,
                        "frames": pkg.card.stream_frames,
                        "fps": pkg.card.stream_fps,
                    }
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/command", started_msg, retain=False
                    )
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/stream_state", started_msg, retain=False
                    )

                    if (
                        pkg.unified_stream_path is not None
                        and pkg.unified_stream_path.exists()
                        and pkg.card.stream_frames > 0
                    ):
                        self.last_claimed_until[(dmd_id, pkg.domain)] = (
                            now_ts + max(3.0, float(pkg.card.stream_duration_s or 0.0) + 2.0)
                        )

                        async def _on_passive_eof(
                            client_dmd_id: str, finished_item_id: str, _ok: bool
                        ) -> None:
                            self.last_claimed_until.pop((client_dmd_id, pkg.domain), None)
                            eof_payload = {
                                "event": "stream_eof",
                                "item_id": finished_item_id,
                                "domain": pkg.domain,
                            }
                            await self.mqtt.publish_json(
                                f"dmd_showcase/clients/{client_dmd_id}/stream_state",
                                eof_payload,
                                retain=False,
                            )
                            abort_ev.set()

                        await self.streamer.start_udp_clock_stream(
                            dmd_id=dmd_id,
                            item_id=pkg.item_id,
                            stream_path=pkg.unified_stream_path,
                            width=pkg.geometry.width,
                            height=pkg.geometry.height,
                            fps=pkg.card.stream_fps,
                            udp_ip=udp_ip,
                            udp_port=udp_port,
                            on_eof=_on_passive_eof,
                        )
                        await abort_ev.wait()
                        await self.streamer.stop_stream(dmd_id)

                    await self.purge_consumed_package(pkg.item_id)
                else:
                    # Mode 2A: Hybrid Receiver (60 FPS local Info Card + UDP DMDF video stream)
                    slot_key = (dmd_id, pkg.domain)
                    self.ready_packages[slot_key] = pkg
                    card_dict = pkg.card.to_dict()
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/{pkg.domain}/ready",
                        {
                            "ready": True,
                            "item_id": pkg.item_id,
                            "domain": pkg.domain,
                            "card": card_dict,
                        },
                        retain=True,
                    )
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/command",
                        {
                            "action": "play_card",
                            "mode": "hybrid",
                            "dmd_id": dmd_id,
                            "domain": pkg.domain,
                            "item_id": pkg.item_id,
                            "card": card_dict,
                        },
                        retain=False,
                    )
                    max_wait_s = max(12.0, float(pkg.card.stream_duration_s or 0.0) + 20.0)
                    try:
                        await asyncio.wait_for(abort_ev.wait(), timeout=max_wait_s)
                    except asyncio.TimeoutError:
                        pass
                    self.ready_packages.pop(slot_key, None)
                    if pkg.item_id in self.active_packages:
                        await self.purge_consumed_package(pkg.item_id)

                self._director_play_abort_events.pop(dmd_id, None)

                # Optional inter-item breathing gap unless skipped or preempted by `play_now`
                if dmd_id in self._director_skip_gap:
                    self._director_skip_gap.discard(dmd_id)
                    continue
                cur_prof = self.profile_store.get_profile(dmd_id) or prof
                gap_s = max(0.0, float(cur_prof.get("inter_item_gap_s") or 0.0))
                if gap_s > 0.0 and not self.priority_queues.get(dmd_id):
                    wake_ev.clear()
                    try:
                        await asyncio.wait_for(wake_ev.wait(), timeout=gap_s)
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            logger.info("[ORCHESTRATOR] Stopped ServerManagedDirector for '%s'", dmd_id)
        except Exception as exc:
            logger.error("[ORCHESTRATOR] ServerManagedDirector error for '%s': %s", dmd_id, exc)


consume_once_orchestrator = ConsumeOnceOrchestrator()
