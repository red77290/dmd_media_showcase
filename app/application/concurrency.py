"""
Reusable Application Concurrency & Task Supervision Primitives (`app/application/concurrency.py`).

Provides:
- `DynamicAsyncLimiter`: Loop-bound concurrency gate supporting instant live resizing (`set_limit`)
  without aborting in-flight jobs or stranding queued waiters.
- `AsyncTaskSupervisor`: Safe cross-loop task inspection and cancellation helpers.
- `WorkerMetricsTracker`: Telemetry counters for active workers, queue depth, and job durations.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional


class DynamicAsyncLimiter:
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


class AsyncTaskSupervisor:
    """Safe cross-event-loop inspection and cancellation of `asyncio.Task` handles."""

    @staticmethod
    def is_task_live(
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
    def safe_cancel_task(task: Optional[asyncio.Task[Any]]) -> None:
        if task is None or task.done():
            return
        try:
            t_loop = task.get_loop()
            if t_loop is not None and not t_loop.is_closed():
                task.cancel()
        except RuntimeError:
            pass


@dataclass
class WorkerMetricsTracker:
    """Tracks live worker concurrency, waiting queue depth, and completed transcode durations."""

    active_workers: int = 0
    queued_jobs: int = 0
    fairness_waiting_jobs: int = 0
    completed_jobs_total: int = 0
    total_job_duration_s: float = 0.0

    @property
    def queue_depth(self) -> int:
        return self.queued_jobs + self.fairness_waiting_jobs

    @property
    def avg_job_duration_s(self) -> float:
        if self.completed_jobs_total <= 0:
            return 0.0
        return round(self.total_job_duration_s / self.completed_jobs_total, 2)

    def record_completed_job(self, duration_s: float, count: int = 1) -> None:
        if count <= 0:
            return
        self.completed_jobs_total += count
        self.total_job_duration_s += max(0.0, float(duration_s))
