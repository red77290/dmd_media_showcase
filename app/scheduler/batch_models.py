"""
Session Batch & Priority Video Job Models (`app/scheduler/batch_models.py`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.models import (
    DomainType,
    MediaShowcaseItem,
    SessionRegisterRequest,
)
from app.providers import RawMediaCandidate


@dataclass
class BatchSlot:
    """Single pre-warmed batch slot (`CurrentBatch` or `NextBatch`) for a rotation session."""

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
    """Double-batch rotation session state (`CurrentBatch` + `NextBatch` + `RetiredBatch`)."""

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
    """Priority-ordered FIFO video transcode job processed by bounded workers."""

    priority: int
    seq: int
    session_id: str = field(compare=False)
    batch_id: str = field(compare=False)
    item: MediaShowcaseItem = field(compare=False)
    candidate: RawMediaCandidate = field(compare=False)
    item_dir: Path = field(compare=False)
    req: SessionRegisterRequest = field(compare=False)
