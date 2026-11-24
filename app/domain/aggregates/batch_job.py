"""
Generic Batch Job State Aggregate (`app/domain/aggregates/batch_job.py`).

Pure, transport-agnostic state machine tracking progress, ETA, dynamic worker scaling,
sequence-ordered streaming artifacts (`after_seq`), and bounded unacknowledged spool
backpressure (`max_unacked_items` / `max_unacked_bytes`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import time
from typing import Deque, Dict, Generic, List, Literal, Optional, Sequence, TypeVar

TJobMeta = TypeVar("TJobMeta")

BatchJobStatus = Literal["queued", "running", "paused", "completed", "cancelled", "failed"]


@dataclass(frozen=True)
class BatchArtifactDescriptor:
    """Metadata descriptor of a single streamed file awaiting or having received client ACK."""

    seq: int
    rel_dir: str
    filename: str
    media_type: str
    size_bytes: int
    title: str = ""
    system: str = ""
    genre: str = ""
    duration_s: float = 0.0
    companion_relpath: str = ""
    companion_size_bytes: int = 0

    @property
    def relpath(self) -> str:
        clean_dir = (self.rel_dir or "").strip("/")
        return f"{clean_dir}/{self.filename}" if clean_dir else self.filename

    def to_dict(self) -> Dict[str, object]:
        return {
            "seq": self.seq,
            "rel_dir": self.rel_dir,
            "filename": self.filename,
            "relpath": self.relpath,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "title": self.title,
            "system": self.system,
            "genre": self.genre,
            "duration_s": self.duration_s,
            "companion_relpath": self.companion_relpath,
            "companion_size_bytes": self.companion_size_bytes,
        }


@dataclass
class BatchScrapeJob(Generic[TJobMeta]):
    """
    Deterministic aggregate governing a multi-worker streaming batch job with bounded
    in-flight backpressure so server RAM / disk never grows unboundedly.
    """

    job_id: str
    meta: TJobMeta
    total_target: int
    max_workers: int = 2
    max_unacked_items: int = 8
    max_unacked_bytes: int = 64 * 1024 * 1024  # 64 MB hard spool ceiling
    planned_directories: List[str] = field(default_factory=list)
    status: BatchJobStatus = "queued"
    created_at: float = field(default_factory=time.monotonic)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    paused_accum_s: float = 0.0
    _paused_since: Optional[float] = None

    dispatched_count: int = 0
    active_workers: int = 0
    succeeded_count: int = 0
    acked_count: int = 0
    skipped_existing_count: int = 0
    failed_count: int = 0
    bytes_produced: int = 0
    bytes_acked: int = 0
    last_error: str = ""

    _next_seq: int = 1
    _unacked: Dict[int, BatchArtifactDescriptor] = field(default_factory=dict)
    _unacked_bytes: int = 0
    _recent_completed: Deque[Dict[str, object]] = field(default_factory=lambda: deque(maxlen=16))

    def start(self, now: Optional[float] = None) -> None:
        ts = time.monotonic() if now is None else now
        if self.started_at is None:
            self.started_at = ts
        self.status = "running"
        self._paused_since = None

    def pause(self, now: Optional[float] = None) -> bool:
        if self.status != "running":
            return False
        self.status = "paused"
        self._paused_since = time.monotonic() if now is None else now
        return True

    def resume(self, now: Optional[float] = None) -> bool:
        if self.status != "paused":
            return False
        ts = time.monotonic() if now is None else now
        if self._paused_since is not None:
            self.paused_accum_s += max(0.0, ts - self._paused_since)
            self._paused_since = None
        self.status = "running"
        return True

    def cancel(self, now: Optional[float] = None) -> None:
        if self.status in ("completed", "cancelled", "failed"):
            return
        ts = time.monotonic() if now is None else now
        if self._paused_since is not None:
            self.paused_accum_s += max(0.0, ts - self._paused_since)
            self._paused_since = None
        self.status = "cancelled"
        self.finished_at = ts

    def mark_completed(self, now: Optional[float] = None) -> None:
        if self.status in ("cancelled", "failed"):
            return
        ts = time.monotonic() if now is None else now
        self.status = "completed"
        self.finished_at = ts

    def mark_failed(self, error_msg: str, now: Optional[float] = None) -> None:
        ts = time.monotonic() if now is None else now
        self.last_error = str(error_msg or "Batch failed")
        self.status = "failed"
        self.finished_at = ts

    def update_max_workers(self, workers: int) -> int:
        clamped = max(1, min(8, int(workers)))
        self.max_workers = clamped
        self.max_unacked_items = max(4, min(16, clamped * 3))
        return clamped

    def has_spool_capacity(self) -> bool:
        return (
            len(self._unacked) < self.max_unacked_items
            and self._unacked_bytes < self.max_unacked_bytes
        )

    def can_dispatch_worker(self) -> bool:
        return (
            self.status == "running"
            and self.active_workers < self.max_workers
            and self.has_spool_capacity()
            and self.processed_count + self.active_workers < self.total_target
        )

    @property
    def processed_count(self) -> int:
        return self.succeeded_count + self.skipped_existing_count + self.failed_count

    @property
    def unacked_count(self) -> int:
        return len(self._unacked)

    @property
    def unacked_bytes(self) -> int:
        return self._unacked_bytes

    def on_worker_enter(self) -> None:
        self.active_workers += 1
        self.dispatched_count += 1

    def on_worker_exit(self) -> None:
        self.active_workers = max(0, self.active_workers - 1)

    def record_artifact_ready(
        self,
        rel_dir: str,
        filename: str,
        size_bytes: int,
        media_type: str = "image/gif",
        title: str = "",
        system: str = "",
        genre: str = "",
        duration_s: float = 0.0,
        companion_relpath: str = "",
        companion_size_bytes: int = 0,
    ) -> BatchArtifactDescriptor:
        seq = self._next_seq
        self._next_seq += 1
        total_item_bytes = max(0, int(size_bytes)) + max(0, int(companion_size_bytes))
        desc = BatchArtifactDescriptor(
            seq=seq,
            rel_dir=rel_dir,
            filename=filename,
            media_type=media_type,
            size_bytes=max(0, int(size_bytes)),
            title=title,
            system=system,
            genre=genre,
            duration_s=round(float(duration_s), 2),
            companion_relpath=companion_relpath,
            companion_size_bytes=max(0, int(companion_size_bytes)),
        )
        self._unacked[seq] = desc
        self._unacked_bytes += total_item_bytes
        self.succeeded_count += 1
        self.bytes_produced += total_item_bytes
        self._recent_completed.appendleft(desc.to_dict())
        return desc

    def ack_artifact(self, seq: int) -> Optional[BatchArtifactDescriptor]:
        removed = self._unacked.pop(int(seq), None)
        if removed is not None:
            freed = removed.size_bytes + removed.companion_size_bytes
            self._unacked_bytes = max(0, self._unacked_bytes - freed)
            self.acked_count += 1
            self.bytes_acked += freed
        return removed

    def unacked_after(self, after_seq: int = 0) -> Sequence[BatchArtifactDescriptor]:
        cutoff = int(after_seq or 0)
        return [self._unacked[k] for k in sorted(self._unacked.keys()) if k > cutoff]

    def record_skipped_existing(self, count: int = 1) -> None:
        self.skipped_existing_count += max(0, int(count))

    def record_failure(self, reason: str = "") -> None:
        self.failed_count += 1
        if reason:
            self.last_error = str(reason)[:240]

    def active_elapsed_seconds(self, now: Optional[float] = None) -> float:
        if self.started_at is None:
            return 0.0
        end_ts = self.finished_at if self.finished_at is not None else (time.monotonic() if now is None else now)
        paused_extra = 0.0
        if self._paused_since is not None and self.finished_at is None:
            paused_extra = max(0.0, end_ts - self._paused_since)
        return max(0.001, (end_ts - self.started_at) - self.paused_accum_s - paused_extra)

    def snapshot(self, now: Optional[float] = None) -> Dict[str, object]:
        elapsed = self.active_elapsed_seconds(now)
        done = self.processed_count
        total = max(1, self.total_target)
        pct = min(100.0, round((done * 100.0) / total, 1)) if self.total_target > 0 else 100.0
        # Exclude instant skipped-existing items from live conversion throughput / ETA
        converted_or_failed = self.succeeded_count + self.failed_count
        rate_per_min = round((converted_or_failed * 60.0) / elapsed, 2) if (elapsed >= 0.5 and converted_or_failed > 0) else 0.0
        remaining = max(0, self.total_target - done)
        eta_s: Optional[int] = None
        if remaining == 0:
            eta_s = 0
        elif rate_per_min > 0.05:
            eta_s = int(round((remaining / rate_per_min) * 60.0))

        return {
            "job_id": self.job_id,
            "status": self.status,
            "total_target": self.total_target,
            "processed_count": done,
            "succeeded_count": self.succeeded_count,
            "acked_count": self.acked_count,
            "skipped_existing_count": self.skipped_existing_count,
            "failed_count": self.failed_count,
            "active_workers": self.active_workers,
            "max_workers": self.max_workers,
            "progress_pct": pct,
            "items_per_min": rate_per_min,
            "elapsed_s": round(elapsed, 1),
            "eta_s": eta_s,
            "unacked_count": len(self._unacked),
            "unacked_bytes": self._unacked_bytes,
            "max_unacked_bytes": self.max_unacked_bytes,
            "bytes_produced": self.bytes_produced,
            "bytes_acked": self.bytes_acked,
            "last_seq": self._next_seq - 1,
            "last_error": self.last_error,
            "planned_directories": list(self.planned_directories),
            "recent_items": list(self._recent_completed),
        }
