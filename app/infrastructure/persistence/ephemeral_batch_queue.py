"""
Bounded Disk-Backed Ephemeral Artifact Queue (`app/infrastructure/persistence/ephemeral_batch_queue.py`).

Implements `IEphemeralArtifactQueuePort`:
- Stores transient worker scratch and unacknowledged `.gif` files under `<data_dir>/.batch_spool/`
  (strictly outside Docker's `512m` RAM `tmpfs` `/tmp/dmd_media_showcase_cache` -> `0` RAM bloat!).
- Immediately unlinks files upon client `ACK` (`ack_and_unlink`) or job cancellation/purge.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import threading
from typing import Dict, Optional, Tuple

from app.domain.ports.batch_scraper_ports import IEphemeralArtifactQueuePort, StagedBatchFiles
from app.portable_env import get_data_dir


class EphemeralBatchArtifactQueue(IEphemeralArtifactQueuePort):
    """Thread-safe ephemeral staging spool with zero permanent server storage."""

    def __init__(self, spool_root: Optional[Path] = None) -> None:
        self._spool_root = spool_root
        self._lock = threading.RLock()
        self._staged: Dict[Tuple[str, int], StagedBatchFiles] = {}
        self._initialized = False

    def _root(self) -> Path:
        root = self._spool_root or (get_data_dir() / ".batch_spool")
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    shutil.rmtree(root, ignore_errors=True)
                    root.mkdir(parents=True, exist_ok=True)
                    self._initialized = True
        root.mkdir(parents=True, exist_ok=True)
        return root

    def create_worker_scratch_dir(self, job_id: str, token: str) -> Path:
        safe_job = "".join(c for c in job_id if c.isalnum() or c in ("_", "-")) or "job"
        safe_tok = "".join(c for c in token if c.isalnum() or c in ("_", "-")) or "item"
        scratch = self._root() / safe_job / "scratch" / safe_tok
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    def stage_completed_artifact(
        self,
        job_id: str,
        seq: int,
        rel_dir: str,
        filename: str,
        src_gif_path: Path,
        companion_relpath: str = "",
        src_companion_path: Optional[Path] = None,
    ) -> StagedBatchFiles:
        safe_job = "".join(c for c in job_id if c.isalnum() or c in ("_", "-")) or "job"
        slot_dir = self._root() / safe_job / "ready" / f"seq_{int(seq):06d}"
        slot_dir.mkdir(parents=True, exist_ok=True)

        dst_gif = slot_dir / "item.gif"
        shutil.move(str(src_gif_path), str(dst_gif))
        gif_bytes = dst_gif.stat().st_size if dst_gif.exists() else 0

        dst_comp: Optional[Path] = None
        comp_bytes = 0
        if companion_relpath and src_companion_path and src_companion_path.exists():
            dst_comp = slot_dir / "companion.png"
            shutil.move(str(src_companion_path), str(dst_comp))
            comp_bytes = dst_comp.stat().st_size

        entry = StagedBatchFiles(
            job_id=job_id,
            seq=int(seq),
            rel_dir=rel_dir,
            filename=filename,
            gif_path=dst_gif,
            size_bytes=gif_bytes,
            companion_relpath=companion_relpath,
            companion_path=dst_comp,
            companion_size_bytes=comp_bytes,
        )
        with self._lock:
            self._staged[(job_id, int(seq))] = entry
        return entry

    def get_staged(self, job_id: str, seq: int) -> Optional[StagedBatchFiles]:
        with self._lock:
            entry = self._staged.get((job_id, int(seq)))
        if entry is None or not entry.gif_path.exists():
            return None
        return entry

    def ack_and_unlink(self, job_id: str, seq: int) -> bool:
        with self._lock:
            entry = self._staged.pop((job_id, int(seq)), None)
        if entry is None:
            return False
        slot_dir = entry.gif_path.parent
        entry.gif_path.unlink(missing_ok=True)
        if entry.companion_path is not None:
            entry.companion_path.unlink(missing_ok=True)
        shutil.rmtree(slot_dir, ignore_errors=True)
        return True

    def purge_job(self, job_id: str) -> None:
        with self._lock:
            keys = [k for k in self._staged if k[0] == job_id]
            for k in keys:
                self._staged.pop(k, None)
        safe_job = "".join(c for c in job_id if c.isalnum() or c in ("_", "-")) or "job"
        job_dir = self._root() / safe_job
        shutil.rmtree(job_dir, ignore_errors=True)


ephemeral_batch_queue = EphemeralBatchArtifactQueue()
