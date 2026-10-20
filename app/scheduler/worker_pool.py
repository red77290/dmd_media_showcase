"""
Bounded FIFO Worker Pool and Session Double-Batch Manager (`CurrentBatch` + `NextBatch`).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
import uuid
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.config import settings
from app.models import (
    BatchStatusResponse,
    DomainType,
    JobPriority,
    MediaAssetsUrls,
    MediaShowcaseItem,
    OnDemandRequest,
    SessionRegisterRequest,
    SessionRegisterResponse,
)
from app.providers import (
    AnimeProvider,
    BaseMediaProvider,
    GamesProvider,
    MoviesProvider,
    RawMediaCandidate,
    SeriesProvider,
)
from app.scheduler.asset_builder import (
    build_static_assets_sync,
    build_video_assets_worker_task,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchSlot:
    batch_id: str
    items: List[MediaShowcaseItem]
    candidates: Dict[str, RawMediaCandidate]
    item_dirs: Dict[str, Path]
    created_at: float = field(default_factory=time.monotonic)
    ready_at: Optional[float] = None
    served_count: int = 0
    include_video: bool = True

    @property
    def ready_count(self) -> int:
        if not self.include_video:
            return len(self.items)
        return sum(1 for item in self.items if item.video_ready)

    @property
    def is_ready(self) -> bool:
        return self.ready_count >= len(self.items)


@dataclass
class SessionState:
    session_id: str
    domain: DomainType
    request: SessionRegisterRequest
    current_batch: BatchSlot
    next_batch: Optional[BatchSlot] = None
    priority_on_demand_items: List[MediaShowcaseItem] = field(default_factory=list)
    seen_ids: Set[str] = field(default_factory=set)
    last_accessed: float = field(default_factory=time.monotonic)


@dataclass(order=True)
class QueuedVideoJob:
    priority: int
    seq: int
    session_id: str = field(compare=False)
    batch_id: str = field(compare=False)
    item: MediaShowcaseItem = field(compare=False)
    candidate: RawMediaCandidate = field(compare=False)
    item_dir: Path = field(compare=False)
    req: SessionRegisterRequest = field(compare=False)


class SessionBatchManager:
    """
    Coordinates DMD sessions, double-batch pre-warming (`CurrentBatch` + `NextBatch`),
    and the bounded FIFO worker pool (`MAX_CONCURRENT_JOBS`).
    """

    def __init__(self) -> None:
        self.providers: Dict[DomainType, BaseMediaProvider] = {
            "games": GamesProvider(),
            "movies": MoviesProvider(),
            "series": SeriesProvider(),
            "anime": AnimeProvider(),
        }
        self.sessions: Dict[str, SessionState] = {}
        self.asset_index: Dict[str, Path] = {}
        self._queue: asyncio.PriorityQueue[QueuedVideoJob] = asyncio.PriorityQueue()
        self._seq_counter = 0
        self._active_workers = 0
        self._completed_jobs = 0
        self._total_job_duration_s = 0.0
        self._worker_tasks: List[asyncio.Task] = []
        self._executor: Optional[Executor] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._queue = asyncio.PriorityQueue()
        self._active_workers = 0
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        max_w = settings.effective_max_workers
        if settings.use_process_pool:
            try:
                self._executor = ProcessPoolExecutor(max_workers=max_w)
            except Exception:
                self._executor = ThreadPoolExecutor(max_workers=max_w)
        else:
            self._executor = ThreadPoolExecutor(max_workers=max_w)

        loop = asyncio.get_running_loop()
        self._worker_tasks = [
            loop.create_task(self._worker_loop(idx)) for idx in range(max_w)
        ]
        logger.info(
            "SessionBatchManager started with %d bounded worker(s) (cache_dir=%s)",
            max_w,
            settings.cache_dir,
        )

    async def stop(self) -> None:
        self._running = False
        for t in self._worker_tasks:
            t.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        self._active_workers = 0

    @property
    def active_workers(self) -> int:
        return self._active_workers

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def completed_jobs_total(self) -> int:
        return self._completed_jobs

    @property
    def avg_job_duration_s(self) -> float:
        if self._completed_jobs == 0:
            return 0.0
        return round(self._total_job_duration_s / self._completed_jobs, 3)

    def _make_session_id(self, domain: DomainType, req: SessionRegisterRequest) -> str:
        if req.client_id:
            digest = hashlib.sha1(f"{domain}:{req.client_id}".encode("utf-8")).hexdigest()[:12]
            return f"ses_{domain}_{digest}"
        return f"ses_{domain}_{uuid.uuid4().hex[:10]}"

    def _build_asset_urls(
        self, item_key: str, files_map: Dict[str, str], width: int, height: int, max_dur_s: float
    ) -> MediaAssetsUrls:
        stem = f"video_{width}x{height}"
        return MediaAssetsUrls(
            poster_png=f"/api/v1/assets/{item_key}/{files_map['poster_png']}",
            poster_rgb565=f"/api/v1/assets/{item_key}/{files_map['poster_rgb565']}",
            marquee_png=f"/api/v1/assets/{item_key}/{files_map['marquee_png']}",
            marquee_rgb565=f"/api/v1/assets/{item_key}/{files_map['marquee_rgb565']}",
            snapshot_pan_png=f"/api/v1/assets/{item_key}/{files_map['snapshot_pan_png']}",
            snapshot_pan_rgb565=f"/api/v1/assets/{item_key}/{files_map['snapshot_pan_rgb565']}",
            video_gif=f"/api/v1/assets/{item_key}/{stem}.gif?max_duration_s={max_dur_s}",
            video_stream_rgb565=f"/api/v1/assets/{item_key}/{stem}.rgb565.stream?max_duration_s={max_dur_s}",
        )

    async def _create_batch_slot(
        self,
        session_id: str,
        domain: DomainType,
        req: SessionRegisterRequest,
        exclude_ids: Set[str],
        priority: JobPriority = JobPriority.NORMAL_BATCH,
    ) -> BatchSlot:
        provider = self.providers[domain]
        candidates = await provider.fetch_random_candidates(req, exclude_ids=exclude_ids)
        batch_id = f"b_{uuid.uuid4().hex[:8]}"

        items: List[MediaShowcaseItem] = []
        cand_map: Dict[str, RawMediaCandidate] = {}
        dir_map: Dict[str, Path] = {}

        loop = asyncio.get_running_loop()
        for idx, cand in enumerate(candidates):
            item_key = f"{batch_id}_{idx}_{cand.id}"
            item_dir = settings.cache_dir / session_id / batch_id / item_key
            files_map = await loop.run_in_executor(
                None,
                build_static_assets_sync,
                str(item_dir),
                cand.title,
                cand.badge,
                cand.theme_palette,
                cand.poster_url,
                cand.marquee_url,
                cand.backdrop_url,
                req.width,
                req.height,
                req.color_boost,
            )
            self.asset_index[item_key] = item_dir
            dir_map[item_key] = item_dir
            cand_map[item_key] = cand

            urls = self._build_asset_urls(
                item_key, files_map, req.width, req.height, req.video_max_duration_s
            )
            showcase_item = MediaShowcaseItem(
                id=item_key,
                domain=domain,
                title=cand.title,
                year=cand.year,
                badge=cand.badge,
                badge_color=cand.badge_color,
                subtitle=cand.subtitle,
                rating=cand.rating,
                synopsis=cand.synopsis if req.include_synopsis else "",
                lang=req.lang,
                video_ready=not req.include_video,
                video_duration_s=req.video_max_duration_s if not req.include_video else 0.0,
                video_fps=req.video_fps,
                assets=urls,
            )
            items.append(showcase_item)

        slot = BatchSlot(
            batch_id=batch_id,
            items=items,
            candidates=cand_map,
            item_dirs=dir_map,
            include_video=req.include_video,
        )
        if not req.include_video:
            slot.ready_at = time.monotonic()
        else:
            for item in items:
                self._enqueue_video_job(
                    priority=priority,
                    session_id=session_id,
                    batch_id=batch_id,
                    item=item,
                    candidate=cand_map[item.id],
                    item_dir=dir_map[item.id],
                    req=req,
                )

        return slot

    def _enqueue_video_job(
        self,
        priority: JobPriority,
        session_id: str,
        batch_id: str,
        item: MediaShowcaseItem,
        candidate: RawMediaCandidate,
        item_dir: Path,
        req: SessionRegisterRequest,
    ) -> None:
        self._seq_counter += 1
        job = QueuedVideoJob(
            priority=int(priority),
            seq=self._seq_counter,
            session_id=session_id,
            batch_id=batch_id,
            item=item,
            candidate=candidate,
            item_dir=item_dir,
            req=req,
        )
        self._queue.put_nowait(job)

    async def _worker_loop(self, worker_id: int) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break

            # Skip if session was already purged or replaced
            session = self.sessions.get(job.session_id)
            if session is None and job.priority != int(JobPriority.HIGH_ON_DEMAND):
                self._queue.task_done()
                continue

            self._active_workers += 1
            t0 = time.monotonic()
            try:
                master_dur = max(job.req.video_max_duration_s, settings.server_master_clip_max_s)
                res = await loop.run_in_executor(
                    self._executor,
                    build_video_assets_worker_task,
                    str(job.item_dir),
                    job.candidate.title,
                    job.candidate.badge,
                    job.candidate.theme_palette,
                    job.candidate.video_url,
                    job.req.width,
                    job.req.height,
                    job.req.video_fps,
                    master_dur,
                    job.req.video_mode,
                    job.req.color_boost,
                    settings.worker_nice_increment,
                )
                job.item.video_ready = True
                job.item.video_duration_s = min(
                    float(res.get("duration_s", job.req.video_max_duration_s)),
                    job.req.video_max_duration_s,
                )
                job.item.video_fps = int(res.get("fps", job.req.video_fps))
                job.item.quality_score = int(res.get("quality_score", 85))

                if session:
                    for b in (session.current_batch, session.next_batch):
                        if b and b.batch_id == job.batch_id and b.is_ready and b.ready_at is None:
                            b.ready_at = time.monotonic()

                elapsed = time.monotonic() - t0
                self._completed_jobs += 1
                self._total_job_duration_s += elapsed
                logger.info(
                    "[Worker #%d] Video ready for %s (%s) in %.2fs",
                    worker_id,
                    job.item.id,
                    job.item.title,
                    elapsed,
                )
            except Exception as exc:
                logger.error("[Worker #%d] Failed video job %s: %s", worker_id, job.item.id, exc)
            finally:
                self._active_workers = max(0, self._active_workers - 1)
                self._queue.task_done()

    def _purge_batch_files(self, session_id: str, batch: BatchSlot) -> None:
        for item_key in batch.item_dirs:
            self.asset_index.pop(item_key, None)
        batch_dir = settings.cache_dir / session_id / batch.batch_id
        shutil.rmtree(batch_dir, ignore_errors=True)

    async def register_session(
        self, domain: DomainType, req: SessionRegisterRequest
    ) -> SessionRegisterResponse:
        self.start()
        self.cleanup_expired_sessions()

        session_id = self._make_session_id(domain, req)
        old = self.sessions.pop(session_id, None)
        if old:
            self._purge_batch_files(session_id, old.current_batch)
            if old.next_batch:
                self._purge_batch_files(session_id, old.next_batch)

        seen: Set[str] = set()
        batch = await self._create_batch_slot(session_id, domain, req, exclude_ids=seen)
        for cand in batch.candidates.values():
            seen.add(cand.id)

        state = SessionState(
            session_id=session_id,
            domain=domain,
            request=req,
            current_batch=batch,
            seen_ids=seen,
        )
        self.sessions[session_id] = state

        return SessionRegisterResponse(
            session_id=session_id,
            domain=domain,
            status="ready" if batch.is_ready else "warming",
            batch_size=len(batch.items),
            ready_count=batch.ready_count,
            queue_depth=self.queue_depth,
            active_workers=self.active_workers,
        )

    async def get_batch_status(
        self, domain: DomainType, session_id: str
    ) -> Optional[BatchStatusResponse]:
        self.start()
        state = self.sessions.get(session_id)
        if not state or state.domain != domain:
            return None

        now = time.monotonic()
        state.last_accessed = now
        cur = state.current_batch

        # Check if we should rotate to NextBatch (when CurrentBatch TTL elapsed and NextBatch is ready)
        if (
            cur.is_ready
            and cur.served_count >= 1
            and cur.ready_at is not None
            and (now - cur.ready_at) >= settings.batch_ttl_seconds
            and state.next_batch is not None
            and state.next_batch.is_ready
        ):
            old_batch = state.current_batch
            state.current_batch = state.next_batch
            state.next_batch = None
            cur = state.current_batch
            self._purge_batch_files(session_id, old_batch)

        if cur.is_ready:
            cur.served_count += 1
            # Schedule NextBatch one step ahead if not already scheduled
            if state.next_batch is None:
                state.next_batch = await self._create_batch_slot(
                    session_id, domain, state.request, exclude_ids=state.seen_ids
                )
                for cand in state.next_batch.candidates.values():
                    state.seen_ids.add(cand.id)

        # Inject any ready high-priority on-demand items at the front
        ready_on_demand = [it for it in state.priority_on_demand_items if it.video_ready]
        if ready_on_demand:
            state.priority_on_demand_items = [
                it for it in state.priority_on_demand_items if not it.video_ready
            ]
            items_out = ready_on_demand + cur.items[: max(0, len(cur.items) - len(ready_on_demand))]
        else:
            items_out = cur.items

        return BatchStatusResponse(
            session_id=session_id,
            domain=domain,
            status="ready" if cur.is_ready else "warming",
            ready_count=cur.ready_count,
            total_count=len(cur.items),
            queue_position=0 if cur.is_ready else max(1, self.queue_depth),
            active_workers=self.active_workers,
            items=items_out,
        )

    async def prepare_on_demand(self, on_demand_req: OnDemandRequest) -> MediaShowcaseItem:
        """
        Future-ready entry point for voice assistants (Google Home / Home Assistant)
        or on-demand UI requests. Queues the item with HIGH_ON_DEMAND priority.
        """
        self.start()
        provider = self.providers[on_demand_req.domain]
        cand = await provider.search_on_demand(on_demand_req.query, lang=on_demand_req.lang)
        if cand is None:
            raise ValueError(f"No media found for query: {on_demand_req.query}")

        synth_req = SessionRegisterRequest(
            count=1,
            lang=on_demand_req.lang,
            width=on_demand_req.width,
            height=on_demand_req.height,
            video_max_duration_s=on_demand_req.video_max_duration_s,
            video_fps=on_demand_req.video_fps,
        )
        batch_id = f"od_{uuid.uuid4().hex[:8]}"
        item_key = f"{batch_id}_{cand.id}"
        target_ses = on_demand_req.target_session_id or "on_demand"
        item_dir = settings.cache_dir / target_ses / batch_id / item_key

        loop = asyncio.get_running_loop()
        files_map = await loop.run_in_executor(
            None,
            build_static_assets_sync,
            str(item_dir),
            cand.title,
            cand.badge,
            cand.theme_palette,
            cand.poster_url,
            cand.marquee_url,
            cand.backdrop_url,
            synth_req.width,
            synth_req.height,
            True,
        )
        self.asset_index[item_key] = item_dir
        urls = self._build_asset_urls(
            item_key, files_map, synth_req.width, synth_req.height, synth_req.video_max_duration_s
        )
        item = MediaShowcaseItem(
            id=item_key,
            domain=on_demand_req.domain,
            title=cand.title,
            year=cand.year,
            badge=cand.badge,
            badge_color=cand.badge_color,
            subtitle=cand.subtitle,
            rating=cand.rating,
            synopsis=cand.synopsis,
            lang=on_demand_req.lang,
            video_ready=False,
            video_duration_s=on_demand_req.video_max_duration_s,
            video_fps=on_demand_req.video_fps,
            on_demand=True,
            assets=urls,
        )
        self._enqueue_video_job(
            priority=JobPriority.HIGH_ON_DEMAND,
            session_id=target_ses,
            batch_id=batch_id,
            item=item,
            candidate=cand,
            item_dir=item_dir,
            req=synth_req,
        )
        if on_demand_req.target_session_id and on_demand_req.target_session_id in self.sessions:
            self.sessions[on_demand_req.target_session_id].priority_on_demand_items.append(item)
        return item

    def resolve_asset_path(self, item_key: str, filename: str) -> Optional[Path]:
        item_dir = self.asset_index.get(item_key)
        if not item_dir:
            return None
        safe_name = Path(filename).name
        target = item_dir / safe_name
        if target.exists() and target.is_file():
            return target
        return None

    def cleanup_expired_sessions(self) -> int:
        now = time.monotonic()
        expired = [
            sid
            for sid, st in self.sessions.items()
            if (now - st.last_accessed) > settings.session_ttl_seconds
        ]
        for sid in expired:
            st = self.sessions.pop(sid, None)
            if st:
                self._purge_batch_files(sid, st.current_batch)
                if st.next_batch:
                    self._purge_batch_files(sid, st.next_batch)
                shutil.rmtree(settings.cache_dir / sid, ignore_errors=True)
        return len(expired)


batch_manager = SessionBatchManager()
