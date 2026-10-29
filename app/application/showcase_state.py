"""
Shared Showcase Runtime State & Queue Entries (`app/application/showcase_state.py`).

Encapsulates the live per-DMD, per-domain slot state, concurrency limiter, and Studio
preview artifacts shared across cohesive Application Services (`ClientRegistryService`,
`SlotPreparationService`, `StudioPreviewService`, `OnDemandQueueService`, and
`PassivePlaylistDirectorService`).
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import time
from typing import Any, Awaitable, Callable, Deque, Dict, List, Literal, Optional, Set, Tuple
import uuid

from app.application.concurrency import AsyncTaskSupervisor, DynamicAsyncLimiter, WorkerMetricsTracker
from app.application.criteria_parser import assemble_prepared_package
from app.config import settings
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
)
from app.domain.ports import IMediaTranscoderPort


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
    cache_hit: bool = False
    raw_query: str = ""
    cache_key: str = ""
    render_signature: str = ""
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
            "cache_hit": self.cache_hit,
            "card_layout": self.criteria.card_layout,
            "card_theme": self.criteria.card_theme,
            "source_used": (
                self.package.card.source_used
                if self.package and self.package.card.source_used
                else (
                    ""
                    if self.candidate.id == "pending"
                    else (self.candidate.source_id or "")
                )
            ),
            "status": self.status,
            "created_at": self.created_at,
        }


class ShowcaseRuntimeState:
    """Centralized in-memory state container for active DMD slots, queues, and worker telemetry."""

    def __init__(self) -> None:
        self.client_configs: Dict[Tuple[str, DomainType], Tuple[PanelGeometry, FilterCriteria]] = {}
        self.client_raw_configs: Dict[str, Dict[str, Any]] = {}
        self.client_udp_ips: Dict[str, str] = {}
        self.client_udp_ports: Dict[str, int] = {}
        self.client_online: Dict[str, bool] = {}
        self.client_transports: Dict[Tuple[str, DomainType], str] = {}
        self.client_last_seen: Dict[Tuple[str, DomainType], float] = {}
        self.last_played_titles: Dict[Tuple[str, DomainType], str] = {}
        self.last_played_sources: Dict[Tuple[str, DomainType], str] = {}
        self.preparing_sources: Dict[Tuple[str, DomainType], str] = {}
        self.last_claimed_until: Dict[Tuple[str, DomainType], float] = {}

        self.ready_packages: Dict[Tuple[str, DomainType], PreparedShowcasePackage] = {}
        self.standby_packages: Dict[Tuple[str, DomainType], PreparedShowcasePackage] = {}
        self.active_packages: Dict[str, PreparedShowcasePackage] = {}
        self.package_mark_seen: Dict[str, bool] = {}
        self.priority_item_ids: Set[str] = set()
        self.priority_queues: Dict[str, Deque[QueuedOnDemandEntry]] = {}
        self.studio_previews: Dict[str, StudioPreviewEntry] = {}
        self.prep_tasks: Dict[Tuple[str, DomainType], asyncio.Task[None]] = {}

        # Server-managed passive DMD director state (Modes 2A & 2B)
        self.server_managed_ids: Set[str] = set()
        self.director_tasks: Dict[str, asyncio.Task[None]] = {}
        self.director_wake_events: Dict[str, asyncio.Event] = {}
        self.director_play_abort_events: Dict[str, asyncio.Event] = {}
        self.director_skip_gap: Set[str] = set()
        self.director_prefetched: Dict[str, PreparedShowcasePackage] = {}
        self.director_prefetch_tasks: Dict[str, asyncio.Task[None]] = {}
        self.director_playlist_cursors: Dict[str, Tuple[int, int]] = {}

        self.metrics = WorkerMetricsTracker()
        self._transcode_sem: Optional[DynamicAsyncLimiter] = None
        self._transcode_sem_loop: Optional[asyncio.AbstractEventLoop] = None

    def get_transcode_semaphore(self) -> DynamicAsyncLimiter:
        loop = asyncio.get_running_loop()
        target = max(1, settings.effective_max_workers)
        if self._transcode_sem is None or self._transcode_sem_loop is not loop:
            self._transcode_sem = DynamicAsyncLimiter(target)
            self._transcode_sem_loop = loop
        elif self._transcode_sem.limit != target:
            self._transcode_sem.set_limit(target)
        return self._transcode_sem

    def reconfigure_concurrency(self, max_workers: Optional[int] = None) -> int:
        """Resize the live Consume-Once transcode concurrency gate in-place without dropping queued tasks."""
        target = max(
            1,
            int(max_workers if max_workers is not None else settings.effective_max_workers),
        )
        if (
            self._transcode_sem is not None
            and self._transcode_sem_loop is not None
            and not self._transcode_sem_loop.is_closed()
        ):
            self._transcode_sem.set_limit(target)
        return target

    def wake_director(self, dmd_id: str) -> None:
        ev = self.director_wake_events.get(dmd_id)
        if ev is not None:
            ev.set()

    async def execute_with_transcode_gate(
        self,
        work_fn: Callable[[], Awaitable[Any]],
        should_count_completion: Optional[Callable[[Any], bool]] = None,
    ) -> Any:
        """Acquire the live concurrency limiter, update worker/queue telemetry, and run `work_fn`."""
        sem = self.get_transcode_semaphore()
        self.metrics.queued_jobs += 1
        queued_decremented = False
        result: Any = None
        completed_ok = False
        try:
            await sem.acquire()
            self.metrics.queued_jobs = max(0, self.metrics.queued_jobs - 1)
            queued_decremented = True
            self.metrics.active_workers += 1
            t_start = time.monotonic()
            try:
                result = await work_fn()
                completed_ok = (
                    should_count_completion(result)
                    if should_count_completion is not None
                    else True
                )
                return result
            finally:
                elapsed_s = max(0.0, time.monotonic() - t_start)
                self.metrics.active_workers = max(0, self.metrics.active_workers - 1)
                if completed_ok:
                    self.metrics.record_completed_job(elapsed_s, count=1)
                sem.release()
        finally:
            if not queued_decremented:
                self.metrics.queued_jobs = max(0, self.metrics.queued_jobs - 1)

    async def build_package_from_candidate(
        self,
        *,
        transcoder: IMediaTranscoderPort,
        dmd_id: str,
        domain: DomainType,
        cand: MediaCandidate,
        geometry: PanelGeometry,
        criteria: FilterCriteria,
    ) -> Optional[PreparedShowcasePackage]:
        """Transcode a `MediaCandidate` through the worker gate into a `PreparedShowcasePackage`."""
        item_id = f"pkg_{domain}_{uuid.uuid4().hex[:8]}_{cand.id}"
        item_dir = settings.cache_dir / "consume_once" / dmd_id / domain / item_id
        slot_key = (dmd_id, domain)
        self.preparing_sources[slot_key] = cand.source_id or ""
        try:
            files_map = await self.execute_with_transcode_gate(
                lambda: transcoder.transcode_candidate(cand, criteria, geometry, item_dir),
                should_count_completion=lambda _res: True,
            )
        finally:
            self.preparing_sources.pop(slot_key, None)
        if not item_dir.exists():
            return None

        return assemble_prepared_package(
            item_id=item_id,
            domain=domain,
            dmd_id=dmd_id,
            cand=cand,
            geometry=geometry,
            criteria=criteria,
            item_dir=item_dir,
            files_map=files_map,
        )

    def purge_dmd_state_and_cache(self, dmd_id: str) -> int:
        """Cancel background tasks and purge all slots, queues, director state, and disk cache for `dmd_id`."""
        clean_id = str(dmd_id or "").strip()
        if not clean_id:
            return 0

        self.server_managed_ids.discard(clean_id)
        self.director_skip_gap.discard(clean_id)
        abort_ev = self.director_play_abort_events.pop(clean_id, None)
        if abort_ev is not None:
            abort_ev.set()
        wake_ev = self.director_wake_events.pop(clean_id, None)
        if wake_ev is not None:
            wake_ev.set()
        AsyncTaskSupervisor.safe_cancel_task(self.director_tasks.pop(clean_id, None))
        AsyncTaskSupervisor.safe_cancel_task(self.director_prefetch_tasks.pop(clean_id, None))
        self.director_playlist_cursors.pop(clean_id, None)

        for key in [k for k in list(self.prep_tasks.keys()) if k[0] == clean_id]:
            AsyncTaskSupervisor.safe_cancel_task(self.prep_tasks.pop(key, None))

        purged_count = 0

        def _drop_pkg(pkg: Optional[PreparedShowcasePackage]) -> None:
            nonlocal purged_count
            if pkg is None:
                return
            self.package_mark_seen.pop(pkg.item_id, None)
            self.priority_item_ids.discard(pkg.item_id)
            self.active_packages.pop(pkg.item_id, None)
            shutil.rmtree(pkg.item_dir, ignore_errors=True)
            purged_count += 1

        direct_q = self.priority_queues.pop(clean_id, None)
        if direct_q:
            for entry in list(direct_q):
                AsyncTaskSupervisor.safe_cancel_task(entry.prep_task)
                _drop_pkg(entry.package)

        for other_q in list(self.priority_queues.values()):
            for entry in list(other_q):
                if entry.target_slot_dmd_id == clean_id:
                    try:
                        other_q.remove(entry)
                    except ValueError:
                        pass
                    AsyncTaskSupervisor.safe_cancel_task(entry.prep_task)
                    _drop_pkg(entry.package)

        for key in [k for k in list(self.ready_packages.keys()) if k[0] == clean_id]:
            _drop_pkg(self.ready_packages.pop(key, None))
        for key in [k for k in list(self.standby_packages.keys()) if k[0] == clean_id]:
            _drop_pkg(self.standby_packages.pop(key, None))
        _drop_pkg(self.director_prefetched.pop(clean_id, None))

        for item_id, pkg in list(self.active_packages.items()):
            if pkg.dmd_id == clean_id:
                _drop_pkg(self.active_packages.pop(item_id, None))

        shutil.rmtree(settings.cache_dir / "consume_once" / clean_id, ignore_errors=True)

        self.client_raw_configs.pop(clean_id, None)
        self.client_udp_ips.pop(clean_id, None)
        self.client_udp_ports.pop(clean_id, None)
        self.client_online.pop(clean_id, None)

        for key in [k for k in list(self.client_configs.keys()) if k[0] == clean_id]:
            self.client_configs.pop(key, None)
            purged_count += 1
        for state_map in (
            self.client_transports,
            self.client_last_seen,
            self.last_played_titles,
            self.last_played_sources,
            self.preparing_sources,
            self.last_claimed_until,
        ):
            for key in [k for k in list(state_map.keys()) if k[0] == clean_id]:
                state_map.pop(key, None)

        return purged_count
