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
from typing import Dict, List, Optional, Set

from app.config import settings
from app.infrastructure.persistence.json_seen_repository import seen_repository
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

logger = logging.getLogger("dmd_showcase.pool")


@dataclass
class BatchSlot:
    batch_id: str
    items: List[MediaShowcaseItem]
    candidates: Dict[str, RawMediaCandidate]
    item_dirs: Dict[str, Path]
    created_at: float = field(default_factory=time.monotonic)
    ready_at: Optional[float] = None
    last_served_at: Optional[float] = None
    served_count: int = 0
    include_video: bool = True

    @property
    def ready_count(self) -> int:
        if not self.include_video:
            return len(self.items)
        return sum(1 for item in self.items if item.video_ready)

    @property
    def is_ready(self) -> bool:
        return len(self.items) > 0 and self.ready_count >= len(self.items)


@dataclass
class SessionState:
    session_id: str
    domain: DomainType
    request: SessionRegisterRequest
    current_batch: BatchSlot
    next_batch: Optional[BatchSlot] = None
    retired_batch: Optional[BatchSlot] = None
    next_batch_creating: bool = False
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
            "[POOL] SessionBatchManager started with %d bounded worker(s) (cache_dir=%s)",
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
        self, item_key: str, files_map: Dict[str, object], width: int, height: int, max_dur_s: float
    ) -> MediaAssetsUrls:
        pan_gifs = [
            f"/api/v1/assets/{item_key}/{fname}"
            for fname in (files_map.get("panorama_gifs") or [])  # type: ignore[union-attr]
        ]
        pan_streams = [
            f"/api/v1/assets/{item_key}/{fname}"
            for fname in (files_map.get("panorama_streams_rgb565") or [])  # type: ignore[union-attr]
        ]
        snaps_rgb = [
            f"/api/v1/assets/{item_key}/{fname}"
            for fname in (files_map.get("snapshots_pan_rgb565") or [])  # type: ignore[union-attr]
        ]
        snap_gifs = [
            f"/api/v1/assets/{item_key}/{fname}"
            for fname in (files_map.get("snapshot_gifs") or [])  # type: ignore[union-attr]
        ]
        snap_streams = [
            f"/api/v1/assets/{item_key}/{fname}"
            for fname in (files_map.get("snapshot_streams_rgb565") or [])  # type: ignore[union-attr]
        ]
        j_gif = str(files_map.get("jacket_gif") or "")
        j_stream = str(files_map.get("jacket_stream_rgb565") or "")
        m_gif = str(files_map.get("marquee_gif") or "")
        m_stream = str(files_map.get("marquee_stream_rgb565") or "")
        return MediaAssetsUrls(
            poster_png=f"/api/v1/assets/{item_key}/{files_map['poster_png']}",
            poster_rgb565=f"/api/v1/assets/{item_key}/{files_map['poster_rgb565']}",
            marquee_png=f"/api/v1/assets/{item_key}/{files_map['marquee_png']}",
            marquee_rgb565=f"/api/v1/assets/{item_key}/{files_map['marquee_rgb565']}",
            snapshot_pan_png=f"/api/v1/assets/{item_key}/{files_map['snapshot_pan_png']}",
            snapshot_pan_rgb565=f"/api/v1/assets/{item_key}/{files_map['snapshot_pan_rgb565']}",
            jacket_gif=f"/api/v1/assets/{item_key}/{j_gif}" if j_gif else "",
            jacket_stream_rgb565=f"/api/v1/assets/{item_key}/{j_stream}" if j_stream else "",
            marquee_gif=f"/api/v1/assets/{item_key}/{m_gif}" if m_gif else "",
            marquee_stream_rgb565=f"/api/v1/assets/{item_key}/{m_stream}" if m_stream else "",
            snapshot_gifs=snap_gifs,
            snapshot_streams_rgb565=snap_streams,
            panorama_gifs=pan_gifs,
            panorama_streams_rgb565=pan_streams,
            snapshots_pan_rgb565=snaps_rgb,
            video_gif="",
            video_stream_rgb565="",
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
        poster_w, poster_h = req.resolved_poster_dims()
        eff_snap_count = req.effective_snapshot_count()

        logger.info(
            "[SESSION] Building batch '%s' for session '%s' (domain=%s, priority=%s, count=%d, %dx%d, poster=%dx%d, jacket_pan=%s, marquee_pan=%s, snaps=%d): %s",
            batch_id,
            session_id,
            domain,
            priority.name,
            len(candidates),
            req.width,
            req.height,
            poster_w,
            poster_h,
            req.jacket_panorama,
            req.marquee_panorama,
            eff_snap_count,
            [c.title for c in candidates],
        )

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
                cand.snapshot_urls or cand.image_urls,
                eff_snap_count if req.include_snapshot else 0,
                req.width,
                req.height,
                req.video_fps,
                req.color_boost,
                poster_w,
                poster_h,
                cand.resolved_jacket_url(),
                req.jacket_panorama,
                req.marquee_panorama,
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
                source_used=cand.source_used,
                poster_width=poster_w,
                poster_height=poster_h,
                text_scroll_speed=req.text_scroll_speed,
                jacket_panorama=bool(urls.jacket_stream_rgb565),
                marquee_panorama=bool(urls.marquee_stream_rgb565),
                snapshot_panorama_count=len(urls.snapshot_streams_rgb565),
                image_count=len(urls.panorama_streams_rgb565),
                has_video=False,
                video_ready=not req.include_video,
                video_duration_s=0.0,
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
            logger.info("[SESSION] Static batch '%s' is immediately READY (%d items)", batch_id, len(items))
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
            logger.info(
                "[QUEUE] Enqueued %d video job(s) for batch '%s' (session=%s, queue_depth=%d, active_workers=%d)",
                len(items),
                batch_id,
                session_id,
                self.queue_depth,
                self.active_workers,
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

    async def _schedule_next_batch_if_needed(self, state: SessionState) -> None:
        if state.next_batch is not None or state.next_batch_creating:
            return
        state.next_batch_creating = True
        try:
            next_slot = await self._create_batch_slot(
                state.session_id,
                state.domain,
                state.request,
                exclude_ids=state.seen_ids,
                priority=JobPriority.LOW_PREFETCH,
            )
            for cand in next_slot.candidates.values():
                state.seen_ids.add(cand.id)
            state.next_batch = next_slot
            logger.info(
                "[SESSION] Pre-warming NextBatch '%s' one step ahead for session '%s' (seen_ids=%d)",
                next_slot.batch_id,
                state.session_id,
                len(state.seen_ids),
            )
        except Exception as exc:
            logger.error("[SESSION] Failed to schedule NextBatch for %s: %s", state.session_id, exc)
        finally:
            state.next_batch_creating = False

    async def _worker_loop(self, worker_id: int) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break

            session = self.sessions.get(job.session_id)
            if session is None and job.priority != int(JobPriority.HIGH_ON_DEMAND):
                self._queue.task_done()
                continue

            self._active_workers += 1
            t0 = time.monotonic()
            logger.info(
                "[WORKER #%d] Starting video job '%s' ('%s') | video_url=%s | youtube_url=%s",
                worker_id,
                job.item.id,
                job.candidate.title,
                job.candidate.video_url or "None",
                job.candidate.youtube_url or "None",
            )
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
                    job.candidate.youtube_url,
                    job.req.width,
                    job.req.height,
                    job.req.video_fps,
                    master_dur,
                    job.req.video_mode,
                    job.req.color_boost,
                    settings.worker_nice_increment,
                )
                has_vid = bool(res.get("has_video", False))
                job.item.has_video = has_vid
                job.item.video_ready = True
                if has_vid:
                    job.item.video_duration_s = min(
                        float(res.get("duration_s", job.req.video_max_duration_s)),
                        job.req.video_max_duration_s,
                    )
                    job.item.video_fps = int(res.get("fps", job.req.video_fps))
                    job.item.quality_score = int(res.get("quality_score", 85))
                    gif_f = str(res.get("gif_file", ""))
                    stream_f = str(res.get("stream_file", ""))
                    job.item.assets.video_gif = (
                        f"/api/v1/assets/{job.item.id}/{gif_f}?max_duration_s={job.req.video_max_duration_s}"
                    )
                    job.item.assets.video_stream_rgb565 = (
                        f"/api/v1/assets/{job.item.id}/{stream_f}?max_duration_s={job.req.video_max_duration_s}"
                    )
                else:
                    job.item.video_duration_s = 0.0
                    job.item.assets.video_gif = ""
                    job.item.assets.video_stream_rgb565 = ""

                elapsed = time.monotonic() - t0
                self._completed_jobs += 1
                self._total_job_duration_s += elapsed

                if session:
                    for b_label, b in (("CurrentBatch", session.current_batch), ("NextBatch", session.next_batch)):
                        if b and b.batch_id == job.batch_id:
                            logger.info(
                                "[WORKER #%d] Finished '%s' (has_video=%s) in %.2fs -> %s '%s' progress: %d/%d ready",
                                worker_id,
                                job.item.title,
                                has_vid,
                                elapsed,
                                b_label,
                                b.batch_id,
                                b.ready_count,
                                len(b.items),
                            )
                            if b.is_ready and b.ready_at is None:
                                b.ready_at = time.monotonic()
                                logger.info(
                                    "[SESSION] %s '%s' for session '%s' is NOW 100%% READY (%d/%d items)!",
                                    b_label,
                                    b.batch_id,
                                    session.session_id,
                                    b.ready_count,
                                    len(b.items),
                                )
                                if b is session.current_batch and session.next_batch is None:
                                    loop.create_task(self._schedule_next_batch_if_needed(session))
                else:
                    logger.info(
                        "[WORKER #%d] Finished on-demand job '%s' (has_video=%s) in %.2fs",
                        worker_id,
                        job.item.title,
                        has_vid,
                        elapsed,
                    )
            except Exception as exc:
                job.item.has_video = False
                job.item.video_ready = True
                logger.error("[WORKER #%d] Failed video job %s: %s", worker_id, job.item.id, exc)
            finally:
                self._active_workers = max(0, self._active_workers - 1)
                self._queue.task_done()

    def _purge_batch_files(self, session_id: str, batch: BatchSlot) -> None:
        for item_key in batch.item_dirs:
            self.asset_index.pop(item_key, None)
        batch_dir = settings.cache_dir / session_id / batch.batch_id
        shutil.rmtree(batch_dir, ignore_errors=True)

    async def register_session(
        self,
        domain: DomainType,
        req: SessionRegisterRequest,
        forced_session_id: Optional[str] = None,
    ) -> SessionRegisterResponse:
        self.start()
        self.cleanup_expired_sessions()

        session_id = forced_session_id or self._make_session_id(domain, req)
        existing = self.sessions.get(session_id)
        if (
            existing is not None
            and existing.domain == domain
            and existing.request.width == req.width
            and existing.request.height == req.height
            and existing.request.lang == req.lang
            and existing.request.primary_source == req.primary_source
            and existing.request.include_video == req.include_video
            and existing.request.jacket_panorama == req.jacket_panorama
            and existing.request.marquee_panorama == req.marquee_panorama
            and existing.request.effective_snapshot_count() == req.effective_snapshot_count()
            and len(existing.current_batch.items) > 0
        ):
            existing.last_accessed = time.monotonic()
            cur_b = existing.current_batch
            logger.info(
                "[SESSION] Reusing active session '%s' (domain=%s, status=%s, ready=%d/%d)",
                session_id,
                domain,
                "ready" if cur_b.is_ready else "warming",
                cur_b.ready_count,
                len(cur_b.items),
            )
            return SessionRegisterResponse(
                session_id=session_id,
                domain=domain,
                status="ready" if cur_b.is_ready else "warming",
                batch_size=len(cur_b.items),
                ready_count=cur_b.ready_count,
                queue_depth=self.queue_depth,
                active_workers=self.active_workers,
            )

        old = self.sessions.pop(session_id, None)
        if old:
            self._purge_batch_files(session_id, old.current_batch)
            if old.next_batch:
                self._purge_batch_files(session_id, old.next_batch)
            if old.retired_batch:
                self._purge_batch_files(session_id, old.retired_batch)

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

        # If static-only (no video jobs), pre-warm NextBatch immediately
        if batch.is_ready:
            await self._schedule_next_batch_if_needed(state)

        logger.info(
            "[SESSION] Registered session '%s' (domain=%s, status=%s, items=%d, primary_source=%s, image_count=%d)",
            session_id,
            domain,
            "ready" if batch.is_ready else "warming",
            len(batch.items),
            req.primary_source,
            req.image_count,
        )

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

        # Auto-recover unknown session_id matching `ses_{domain}_...` (e.g. after Docker container restart)
        if state is None and session_id.startswith(f"ses_{domain}_"):
            logger.info(
                "[SESSION] Auto-recovering unknown session_id '%s' for domain '%s' (container restart recovery)",
                session_id,
                domain,
            )
            default_req = SessionRegisterRequest(client_id=session_id)
            await self.register_session(domain, default_req, forced_session_id=session_id)
            state = self.sessions.get(session_id)

        if not state or state.domain != domain:
            return None

        now = time.monotonic()
        state.last_accessed = now
        cur = state.current_batch

        # If a previous batch build yielded 0 candidates (e.g. temporary network hiccup), retry after 5s cooldown
        if len(cur.items) == 0 and (now - cur.created_at) >= 5.0:
            rebuilt = await self._create_batch_slot(
                session_id,
                domain,
                state.request,
                exclude_ids=state.seen_ids,
            )
            for cand in rebuilt.candidates.values():
                state.seen_ids.add(cand.id)
            state.current_batch = rebuilt
            cur = state.current_batch

        # Rotate immediately to NextBatch on any subsequent turn poll once NextBatch is ready
        if (
            cur.is_ready
            and cur.served_count >= 1
            and cur.last_served_at is not None
            and (now - cur.last_served_at) >= 2.0
            and state.next_batch is not None
            and state.next_batch.is_ready
        ):
            if state.retired_batch:
                self._purge_batch_files(session_id, state.retired_batch)
            state.retired_batch = state.current_batch
            state.current_batch = state.next_batch
            state.next_batch = None
            cur = state.current_batch
            logger.info(
                "[SESSION] Rotated session '%s' to fresh NextBatch '%s' -> items=%s",
                session_id,
                cur.batch_id,
                [it.title for it in cur.items],
            )

        if cur.is_ready:
            for cand in cur.candidates.values():
                seen_repository.mark_consumed(domain, cand.id)
            cur.served_count += 1
            cur.last_served_at = now
            # Ensure NextBatch is scheduled one step ahead
            if state.next_batch is None and not state.next_batch_creating:
                await self._schedule_next_batch_if_needed(state)

        # Inject any ready high-priority on-demand items at the front
        ready_on_demand = [it for it in state.priority_on_demand_items if it.video_ready]
        if ready_on_demand:
            state.priority_on_demand_items = [
                it for it in state.priority_on_demand_items if not it.video_ready
            ]
            items_out = ready_on_demand + cur.items[: max(0, len(cur.items) - len(ready_on_demand))]
        else:
            items_out = cur.items

        logger.info(
            "[SESSION] Batch poll for '%s' (batch=%s): status=%s (%d/%d ready) | items=%s",
            session_id,
            cur.batch_id,
            "ready" if cur.is_ready else "warming",
            cur.ready_count,
            len(cur.items),
            [it.title for it in items_out],
        )

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
            jacket_panorama=on_demand_req.jacket_panorama,
            marquee_panorama=on_demand_req.marquee_panorama,
            snapshot_panorama_count=on_demand_req.snapshot_panorama_count,
            image_count=on_demand_req.image_count,
            primary_source=on_demand_req.primary_source,
            scraper_user=on_demand_req.scraper_user,
            scraper_pass=on_demand_req.scraper_pass,
            scraper_api_key=on_demand_req.scraper_api_key,
            video_max_duration_s=on_demand_req.video_max_duration_s,
            video_fps=on_demand_req.video_fps,
        )
        poster_w, poster_h = synth_req.resolved_poster_dims()
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
            cand.snapshot_urls or cand.image_urls,
            synth_req.effective_snapshot_count(),
            synth_req.width,
            synth_req.height,
            synth_req.video_fps,
            True,
            poster_w,
            poster_h,
            cand.resolved_jacket_url(),
            synth_req.jacket_panorama,
            synth_req.marquee_panorama,
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
            source_used=cand.source_used,
            poster_width=poster_w,
            poster_height=poster_h,
            jacket_panorama=bool(urls.jacket_stream_rgb565),
            marquee_panorama=bool(urls.marquee_stream_rgb565),
            snapshot_panorama_count=len(urls.snapshot_streams_rgb565),
            image_count=len(urls.panorama_streams_rgb565),
            has_video=False,
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
                if st.retired_batch:
                    self._purge_batch_files(sid, st.retired_batch)
                shutil.rmtree(settings.cache_dir / sid, ignore_errors=True)
        return len(expired)


batch_manager = SessionBatchManager()
