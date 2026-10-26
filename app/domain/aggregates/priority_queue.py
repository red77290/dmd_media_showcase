"""
Domain Aggregate: On-Demand Priority Playback Queue (`app/domain/aggregates/priority_queue.py`).

Encapsulates `enqueue_next` (FIFO tail) vs `play_now` (priority head preemption) ordering,
queue entry lifecycle states (`queued`, `preparing`, `ready`, `playing`), and cancellation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Dict, List, Literal, Optional

from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
)


@dataclass
class QueuedOnDemandEntry:
    """Domain entity representing an On-Demand item queued for a specific DMD client."""

    queue_id: str
    dmd_id: str
    domain: DomainType
    candidate: MediaCandidate
    geometry: PanelGeometry
    criteria: FilterCriteria
    mode: Literal["enqueue_next", "play_now"] = "enqueue_next"
    mark_seen: bool = True
    from_studio_sim: bool = False
    status: Literal["queued", "preparing", "ready", "playing"] = "queued"
    created_at: float = field(default_factory=time.time)
    prepared_pkg: Optional[PreparedShowcasePackage] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "queue_id": self.queue_id,
            "dmd_id": self.dmd_id,
            "domain": self.domain,
            "candidate_id": self.candidate.id,
            "item_id": self.prepared_pkg.item_id if self.prepared_pkg else "",
            "title": self.candidate.title,
            "year": self.candidate.year,
            "badge": self.candidate.badge,
            "lang": self.criteria.lang,
            "mode": self.mode,
            "mark_seen": self.mark_seen,
            "from_studio_sim": self.from_studio_sim,
            "status": self.status,
            "created_at": self.created_at,
        }


class PriorityPlaybackQueue:
    """
    Per-client priority queue aggregate managing `QueuedOnDemandEntry` lists across DMDs.
    Preserves `self.by_client` dictionary structure for $O(1)$ inspection.
    """

    def __init__(self) -> None:
        self.by_client: Dict[str, List[QueuedOnDemandEntry]] = {}

    def list_entries(self, dmd_id: str) -> List[QueuedOnDemandEntry]:
        return list(self.by_client.get(dmd_id) or [])

    def list_serialized(self, dmd_id: str) -> List[Dict[str, object]]:
        return [entry.to_dict() for entry in (self.by_client.get(dmd_id) or [])]

    def enqueue(self, entry: QueuedOnDemandEntry) -> None:
        q_list = self.by_client.setdefault(entry.dmd_id, [])
        if entry.mode == "play_now":
            q_list.insert(0, entry)
        else:
            q_list.append(entry)

    def peek_head(self, dmd_id: str) -> Optional[QueuedOnDemandEntry]:
        q_list = self.by_client.get(dmd_id)
        return q_list[0] if q_list else None

    def pop_by_item_id(self, dmd_id: str, item_id: str) -> Optional[QueuedOnDemandEntry]:
        q_list = self.by_client.get(dmd_id)
        if not q_list:
            return None
        for idx, entry in enumerate(q_list):
            if entry.prepared_pkg is not None and entry.prepared_pkg.item_id == item_id:
                return q_list.pop(idx)
        return None

    def remove(self, dmd_id: str, queue_id: str) -> Optional[QueuedOnDemandEntry]:
        q_list = self.by_client.get(dmd_id)
        if not q_list:
            return None
        for idx, entry in enumerate(q_list):
            if entry.queue_id == queue_id:
                return q_list.pop(idx)
        return None
