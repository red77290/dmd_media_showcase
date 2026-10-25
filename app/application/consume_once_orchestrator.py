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
from dataclasses import replace as dc_replace
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from app.application.source_chain_service import DomainSourceChainService, source_chain_service
from app.config import settings
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.domain.ports.seen_repository import ISeenHistoryRepository
from app.domain.taxonomy import resolve_canonical_genre, resolve_canonical_system
from app.infrastructure.mqtt.showcase_mqtt_gateway import ShowcaseMqttGateway, showcase_mqtt_gateway
from app.infrastructure.persistence.json_seen_repository import seen_repository
from app.infrastructure.streaming.ffmpeg_udp_streamer import udp_clock_streamer
from app.infrastructure.transcoder.dmd_converter_adapter import dmd_transcoder_adapter

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


class ConsumeOnceOrchestrator:
    """
    Stateless Consume-Once orchestrator managing per-DMD, per-domain prepared slots.
    """

    def __init__(
        self,
        chain_service: Optional[DomainSourceChainService] = None,
        seen_repo: Optional[ISeenHistoryRepository] = None,
        transcoder: Optional[IMediaTranscoderPort] = None,
        streamer: Optional[IFrameStreamerPort] = None,
        mqtt_gateway: Optional[ShowcaseMqttGateway] = None,
    ) -> None:
        self.chain_service = chain_service or source_chain_service
        self.seen_repo = seen_repo or seen_repository
        self.transcoder = transcoder or dmd_transcoder_adapter
        self.streamer = streamer or udp_clock_streamer
        self.mqtt = mqtt_gateway or showcase_mqtt_gateway

        # Per `(dmd_id, domain)` configuration, ready slot (N), standby slot (N+1), active playing slot, and prep task
        self.client_configs: Dict[Tuple[str, DomainType], Tuple[PanelGeometry, FilterCriteria]] = {}
        self.client_udp_ips: Dict[str, str] = {}
        self.client_transports: Dict[Tuple[str, DomainType], str] = {}
        self.client_last_seen: Dict[Tuple[str, DomainType], float] = {}
        self.last_played_titles: Dict[Tuple[str, DomainType], str] = {}
        self.last_claimed_until: Dict[Tuple[str, DomainType], float] = {}
        self.ready_packages: Dict[Tuple[str, DomainType], PreparedShowcasePackage] = {}
        self.standby_packages: Dict[Tuple[str, DomainType], PreparedShowcasePackage] = {}
        self.active_packages: Dict[str, PreparedShowcasePackage] = {}
        self._prep_tasks: Dict[Tuple[str, DomainType], asyncio.Task[None]] = {}
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
        return len(self.ready_packages)

    @property
    def standby_slots_count(self) -> int:
        return len(self.standby_packages)

    def distinct_board_keys(self) -> Set[str]:
        return {
            canonical_dmd_board_key(dmd_id, self.client_udp_ips.get(dmd_id, ""))
            for (dmd_id, _dom) in self.client_configs
        }

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
        """Return a summary of currently registered DMD clients and their prepared queue slots."""
        out: List[Dict[str, Any]] = []
        now = time.time()
        for (dmd_id, domain), (geom, crit) in sorted(self.client_configs.items()):
            key = (dmd_id, domain)
            ready_pkg = self.ready_packages.get(key)
            standby_pkg = self.standby_packages.get(key)
            prep_task = self._prep_tasks.get(key)
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
            out.append(
                {
                    "dmd_id": dmd_id,
                    "domain": domain,
                    "transport": self.client_transports.get(key, "MQTT+UDP"),
                    "client_ip": self.client_udp_ips.get(dmd_id, ""),
                    "width": geom.width,
                    "height": geom.height,
                    "lang": crit.lang,
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
                    "last_seen_ago_s": int(max(0.0, now - last_seen)) if last_seen else None,
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
            jacket_panorama=bool(raw_cfg.get("jacket_panorama", True)),
            marquee_panorama=bool(raw_cfg.get("marquee_panorama", False)),
            snapshot_panorama_count=int(raw_cfg.get("snapshot_panorama_count", 0)),
            include_video=bool(raw_cfg.get("show_video", raw_cfg.get("include_video", True))),
            video_max_duration_s=eff_video_dur,
            video_fps=eff_video_fps,
            video_mode=video_mode_val,
            color_boost=color_boost_val,
        )

    async def handle_mqtt_client_config(self, dmd_id: str, payload: Dict[str, Any]) -> None:
        """
        Handle retained `dmd_showcase/clients/{dmd_id}/config` message published by a DMD
        at boot or when WebUI settings are saved.
        """
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
        key = (dmd_id, domain)
        if key in self.ready_packages and key in self.standby_packages:
            return
        loop = asyncio.get_running_loop()
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
        self.seen_repo.mark_consumed(domain, pkg.candidate.id)

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
        pkg = self.active_packages.pop(item_id, None)
        if pkg is not None:
            shutil.rmtree(pkg.item_dir, ignore_errors=True)
            logger.info(
                "[ORCHESTRATOR] Purged consumed media files for '%s' ('%s')",
                item_id,
                pkg.candidate.title,
            )
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
            await self._cleanup_abandoned_active_packages(dmd_id)


consume_once_orchestrator = ConsumeOnceOrchestrator()
