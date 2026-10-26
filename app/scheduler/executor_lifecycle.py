"""
Bounded Worker Executor Lifecycle Mixin (`app/scheduler/executor_lifecycle.py`).

Encapsulates event-loop-resilient `ProcessPoolExecutor` / `ThreadPoolExecutor` management,
live worker scaling (`reconfigure_workers`), graceful teardown across closed event loops,
and worker pool telemetry metrics.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Dict, Generic, List, Optional, Set, Tuple, TypeVar

from app.config import settings

logger = logging.getLogger("dmd_showcase.pool")

TJob = TypeVar("TJob")


class BoundedWorkerExecutorMixin(Generic[TJob]):
    """Reusable bounded async + process/thread worker pool lifecycle controller."""

    def _init_executor_state(self) -> None:
        self._queue: asyncio.PriorityQueue[TJob] = asyncio.PriorityQueue()
        self._seq_counter: int = 0
        self._active_workers: int = 0
        self._completed_jobs: int = 0
        self._total_job_duration_s: float = 0.0
        self._worker_tasks: List[asyncio.Task] = []
        self._worker_task_map: Dict[int, asyncio.Task] = {}
        self._busy_worker_ids: Set[int] = set()
        self._target_workers: int = 0
        self._executor: Optional[Executor] = None
        self._executor_spec: Tuple[bool, int] = (True, 0)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running: bool = False

    async def _worker_loop(self, worker_id: int) -> None:  # pragma: no cover - overridden by subclass
        raise NotImplementedError

    @staticmethod
    def _create_executor(use_proc: bool, max_w: int) -> Executor:
        if use_proc:
            try:
                return ProcessPoolExecutor(max_workers=max_w)
            except Exception:
                return ThreadPoolExecutor(max_workers=max_w)
        return ThreadPoolExecutor(max_workers=max_w)

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        if (
            self._running
            and self._loop is loop
            and not loop.is_closed()
            and self._worker_tasks
            and any(not t.done() for t in self._worker_tasks)
        ):
            return

        stale_tasks = list(self._worker_tasks)
        self._worker_tasks.clear()
        self._worker_task_map.clear()
        self._busy_worker_ids.clear()
        for t in stale_tasks:
            if not t.done():
                try:
                    t_loop = t.get_loop()
                    if t_loop is not None and not t_loop.is_closed():
                        t.cancel()
                except RuntimeError:
                    pass
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

        self._running = True
        self._loop = loop
        self._queue = asyncio.PriorityQueue()
        self._active_workers = 0
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        max_w = settings.effective_max_workers
        use_proc = bool(settings.use_process_pool)
        self._target_workers = max_w
        self._executor = self._create_executor(use_proc, max_w)
        self._executor_spec = (use_proc, max_w)

        self._worker_task_map = {
            idx: loop.create_task(self._worker_loop(idx)) for idx in range(max_w)
        }
        self._worker_tasks = list(self._worker_task_map.values())
        logger.info(
            "[POOL] SessionBatchManager started with %d bounded worker(s) (cache_dir=%s)",
            max_w,
            settings.cache_dir,
        )

    async def reconfigure_workers(self) -> int:
        """
        Live-reload worker count (`effective_max_workers`), executor mode (`use_process_pool`),
        and `cache_dir` in real time without dropping queued jobs or aborting active jobs.
        """
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        max_w = settings.effective_max_workers
        use_proc = bool(settings.use_process_pool)
        self._target_workers = max_w

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if (
            not self._running
            or self._loop is None
            or self._loop.is_closed()
            or (current_loop is not None and self._loop is not current_loop)
        ):
            return max_w

        if self._executor is None or self._executor_spec != (use_proc, max_w):
            old_exec = self._executor
            self._executor = self._create_executor(use_proc, max_w)
            self._executor_spec = (use_proc, max_w)
            if old_exec is not None:
                old_exec.shutdown(wait=False, cancel_futures=False)

        cancelled_idle: List[asyncio.Task] = []
        for wid, t in list(self._worker_task_map.items()):
            if t.done():
                self._worker_task_map.pop(wid, None)
                continue
            if wid >= max_w and wid not in self._busy_worker_ids:
                self._worker_task_map.pop(wid, None)
                try:
                    t.cancel()
                    cancelled_idle.append(t)
                except RuntimeError:
                    pass

        for wid in range(max_w):
            existing = self._worker_task_map.get(wid)
            if existing is None or existing.done():
                self._worker_task_map[wid] = self._loop.create_task(self._worker_loop(wid))

        if cancelled_idle and current_loop is self._loop:
            await asyncio.gather(*cancelled_idle, return_exceptions=True)

        self._worker_tasks = [
            t for _, t in sorted(self._worker_task_map.items()) if not t.done()
        ]
        logger.info(
            "[POOL] Reconfigured worker pool live -> target_workers=%d (process_pool=%s, cache_dir=%s)",
            max_w,
            use_proc,
            settings.cache_dir,
        )
        return max_w

    async def stop(self) -> None:
        self._running = False
        self._target_workers = 0
        tasks = list(self._worker_tasks)
        for t_extra in self._worker_task_map.values():
            if t_extra not in tasks:
                tasks.append(t_extra)
        self._worker_tasks.clear()
        self._worker_task_map.clear()
        self._busy_worker_ids.clear()
        self._loop = None
        executor = self._executor
        self._executor = None
        self._active_workers = 0

        try:
            current_loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        same_loop_tasks: List[asyncio.Task] = []
        for t in tasks:
            if t.done():
                continue
            try:
                t_loop = t.get_loop()
            except Exception:
                t_loop = None
            if t_loop is None or t_loop.is_closed():
                continue
            try:
                t.cancel()
            except RuntimeError:
                continue
            if current_loop is not None and t_loop is current_loop:
                same_loop_tasks.append(t)

        if same_loop_tasks:
            await asyncio.gather(*same_loop_tasks, return_exceptions=True)

        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

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
