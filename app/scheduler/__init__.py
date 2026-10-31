"""
Bounded worker pool and session double-batch scheduler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.scheduler.worker_pool import SessionBatchManager

__all__ = ["SessionBatchManager"]


def __getattr__(name: str) -> Any:
    if name == "SessionBatchManager":
        from app.scheduler.worker_pool import SessionBatchManager

        return SessionBatchManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
