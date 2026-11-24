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

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "BatchArtifactDescriptor":
        return cls(
            seq=int(data.get("seq") or 0),  # type: ignore[arg-type]
            rel_dir=str(data.get("rel_dir") or ""),
            filename=str(data.get("filename") or ""),
            media_type=str(data.get("media_type") or "image/gif"),
            size_bytes=max(0, int(data.get("size_bytes") or 0)),  # type: ignore[arg-type]
            title=str(data.get("title") or ""),
            system=str(data.get("system") or ""),
            genre=str(data.get("genre") or ""),
            duration_s=float(data.get("duration_s") or 0.0),  # type: ignore[arg-type]
            companion_relpath=str(data.get("companion_relpath") or ""),
            companion_size_bytes=max(0, int(data.get("companion_size_bytes") or 0)),  # type: ignore[arg-type]
        )


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
    max_unacked_bytes: int = 64 * 1024 * 1024  # 64 MB default ceiling (overridden per job)
    planned_directories: List[str] = field(default_factory=list)
    status: BatchJobStatus = "queued"
    created_at: float = field(default_factory=time.monotonic)
    created_wall_ts: float = field(default_factory=time.time)
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
    completed_relpaths: List[str] = field(default_factory=list)

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
        self.max_unacked_items = max(self.max_unacked_items, max(4, min(16, clamped * 3)))
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
        self.completed_relpaths.append(desc.relpath)
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
        converted_or_failed = self.succeeded_count + self.failed_count
        rate_per_min = round((converted_or_failed * 60.0) / elapsed, 2) if (elapsed >= 0.5 and converted_or_failed > 0) else 0.0
        remaining = max(0, self.total_target - done)
        eta_s: Optional[int] = None
        if remaining == 0:
            eta_s = 0
        elif rate_per_min > 0.05:
            eta_s = int(round((remaining / rate_per_min) * 60.0))

        summary_fn = getattr(self.meta, "public_summary", None)
        config_summary = summary_fn() if callable(summary_fn) else {}

        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_wall_ts": round(float(self.created_wall_ts), 2),
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
            "spool_backpressured": not self.has_spool_capacity(),
            "bytes_produced": self.bytes_produced,
            "bytes_acked": self.bytes_acked,
            "last_seq": self._next_seq - 1,
            "last_error": self.last_error,
            "planned_directories": list(self.planned_directories),
            "recent_items": list(self._recent_completed),
            "config_summary": config_summary,
        }

    def to_persisted_state(self, meta_dict: Dict[str, object], extra: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "created_wall_ts": round(float(self.created_wall_ts), 2),
            "elapsed_s": round(self.active_elapsed_seconds(), 2),
            "total_target": self.total_target,
            "max_workers": self.max_workers,
            "max_unacked_items": self.max_unacked_items,
            "max_unacked_bytes": self.max_unacked_bytes,
            "planned_directories": list(self.planned_directories),
            "succeeded_count": self.succeeded_count,
            "acked_count": self.acked_count,
            "skipped_existing_count": self.skipped_existing_count,
            "failed_count": self.failed_count,
            "bytes_produced": self.bytes_produced,
            "bytes_acked": self.bytes_acked,
            "last_error": self.last_error,
            "next_seq": self._next_seq,
            "completed_relpaths": list(self.completed_relpaths),
            "unacked": [d.to_dict() for d in self.unacked_after(0)],
            "recent_items": list(self._recent_completed),
            "meta": meta_dict,
            "extra": extra or {},
        }

    @classmethod
    def restore_from_persisted_state(
        cls,
        data: Dict[str, object],
        meta: TJobMeta,
        valid_unacked_seqs: Optional[Sequence[int]] = None,
    ) -> "BatchScrapeJob[TJobMeta]":
        now_mono = time.monotonic()
        raw_status = str(data.get("status") or "paused")
        restored_status: BatchJobStatus = (
            "paused"
            if raw_status in ("queued", "running", "paused")
            else (raw_status if raw_status in ("completed", "cancelled", "failed") else "completed")  # type: ignore[assignment]
        )
        saved_elapsed = max(0.0, float(data.get("elapsed_s") or 0.0))  # type: ignore[arg-type]
        started_at = max(0.001, now_mono - saved_elapsed)
        finished_at = now_mono if restored_status in ("completed", "cancelled", "failed") else None
        paused_since = now_mono if restored_status == "paused" else None

        allowed_seqs = set(int(s) for s in valid_unacked_seqs) if valid_unacked_seqs is not None else None
        unacked_map: Dict[int, BatchArtifactDescriptor] = {}
        unacked_bytes_sum = 0
        for raw_d in (data.get("unacked") or []):  # type: ignore[attr-defined]
            if not isinstance(raw_d, dict):
                continue
            desc = BatchArtifactDescriptor.from_dict(raw_d)
            if desc.seq <= 0:
                continue
            if allowed_seqs is not None and desc.seq not in allowed_seqs:
                continue
            unacked_map[desc.seq] = desc
            unacked_bytes_sum += desc.size_bytes + desc.companion_size_bytes

        recent_dq: Deque[Dict[str, object]] = deque(maxlen=16)
        for r_it in (data.get("recent_items") or []):  # type: ignore[attr-defined]
            if isinstance(r_it, dict):
                recent_dq.append(dict(r_it))

        job = cls(
            job_id=str(data.get("job_id") or ""),
            meta=meta,
            total_target=max(1, int(data.get("total_target") or 1)),  # type: ignore[arg-type]
            max_workers=max(1, min(8, int(data.get("max_workers") or 2))),  # type: ignore[arg-type]
            max_unacked_items=max(4, int(data.get("max_unacked_items") or 256)),  # type: ignore[arg-type]
            max_unacked_bytes=max(1024, int(data.get("max_unacked_bytes") or (512 * 1024 * 1024))),  # type: ignore[arg-type]
            planned_directories=[str(p) for p in (data.get("planned_directories") or [])],  # type: ignore[attr-defined]
            status=restored_status,
            created_at=started_at,
            created_wall_ts=float(data.get("created_wall_ts") or time.time()),  # type: ignore[arg-type]
            started_at=started_at,
            finished_at=finished_at,
            paused_accum_s=0.0,
            _paused_since=paused_since,
            dispatched_count=max(0, int(data.get("succeeded_count") or 0) + int(data.get("failed_count") or 0)),  # type: ignore[arg-type]
            active_workers=0,
            succeeded_count=max(0, int(data.get("succeeded_count") or 0)),  # type: ignore[arg-type]
            acked_count=max(0, int(data.get("acked_count") or 0)),  # type: ignore[arg-type]
            skipped_existing_count=max(0, int(data.get("skipped_existing_count") or 0)),  # type: ignore[arg-type]
            failed_count=max(0, int(data.get("failed_count") or 0)),  # type: ignore[arg-type]
            bytes_produced=max(0, int(data.get("bytes_produced") or 0)),  # type: ignore[arg-type]
            bytes_acked=max(0, int(data.get("bytes_acked") or 0)),  # type: ignore[arg-type]
            last_error=str(data.get("last_error") or ""),
            completed_relpaths=[str(p) for p in (data.get("completed_relpaths") or [])],  # type: ignore[attr-defined]
            _next_seq=max(1, int(data.get("next_seq") or 1)),  # type: ignore[arg-type]
            _unacked=unacked_map,
            _unacked_bytes=unacked_bytes_sum,
            _recent_completed=recent_dq,
        )
        return job
