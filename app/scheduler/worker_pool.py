"""
Scheduler Worker Pool Facade (`app/scheduler/worker_pool.py`).

Re-exports `SessionBatchManager`, `batch_manager`, and batch/job data models from
their cohesive sub-modules (`batch_models`, `executor_lifecycle`, `session_batch_manager`).
"""

from __future__ import annotations

from app.scheduler.batch_models import BatchSlot, QueuedVideoJob, SessionState
from app.scheduler.executor_lifecycle import BoundedWorkerExecutorMixin
from app.scheduler.session_batch_manager import SessionBatchManager, batch_manager

__all__ = [
    "BatchSlot",
    "BoundedWorkerExecutorMixin",
    "QueuedVideoJob",
    "SessionBatchManager",
    "SessionState",
    "batch_manager",
]
