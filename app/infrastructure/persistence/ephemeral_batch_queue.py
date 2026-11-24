"""
Bounded Disk-Backed Ephemeral Artifact Queue (`app/infrastructure/persistence/ephemeral_batch_queue.py`).

Implements `IEphemeralArtifactQueuePort`:
- Stores transient worker scratch and unacknowledged `.gif` files under `<data_dir>/.batch_spool/`
  (strictly outside Docker's `512m` RAM `tmpfs` `/tmp/dmd_media_showcase_cache` -> `0` RAM bloat!).
- Immediately unlinks files upon client `ACK` (`ack_and_unlink`) or job cancellation/purge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import threading
from typing import Dict, Optional, Set, Tuple

from app.domain.ports.batch_scraper_ports import IEphemeralArtifactQueuePort, StagedBatchFiles
from app.portable_env import get_data_dir


def _safe_job_dir_name(job_id: str) -> str:
    return "".join(c for c in job_id if c.isalnum() or c in ("_", "-")) or "job"


class EphemeralBatchArtifactQueue(IEphemeralArtifactQueuePort):
    """Thread-safe disk-backed staging spool preserving unacknowledged ready slots across restarts."""

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
                    root.mkdir(parents=True, exist_ok=True)
                    # Clean only incomplete worker scratch directories; keep ready/ slots intact for rehydration!
                    for child in root.iterdir():
                        if child.is_dir():
                            shutil.rmtree(child / "scratch", ignore_errors=True)
                    self._initialized = True
        root.mkdir(parents=True, exist_ok=True)
        return root

    def create_worker_scratch_dir(self, job_id: str, token: str) -> Path:
        safe_job = _safe_job_dir_name(job_id)
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
        safe_job = _safe_job_dir_name(job_id)
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

        meta_tmp = slot_dir / "slot.json.tmp"
        meta_path = slot_dir / "slot.json"
        meta_payload = {
            "job_id": job_id,
            "seq": int(seq),
            "rel_dir": rel_dir,
            "filename": filename,
            "size_bytes": gif_bytes,
            "companion_relpath": companion_relpath,
            "companion_size_bytes": comp_bytes,
        }
        meta_tmp.write_text(json.dumps(meta_payload, ensure_ascii=False), encoding="utf-8")
        os.replace(meta_tmp, meta_path)

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
            safe_job = _safe_job_dir_name(job_id)
            fallback_slot = self._root() / safe_job / "ready" / f"seq_{int(seq):06d}"
            if fallback_slot.exists():
                shutil.rmtree(fallback_slot, ignore_errors=True)
                return True
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
        job_dir = self._root() / _safe_job_dir_name(job_id)
        shutil.rmtree(job_dir, ignore_errors=True)

    def rehydrate_job_artifacts(self, job_id: str) -> Dict[int, StagedBatchFiles]:
        ready_root = self._root() / _safe_job_dir_name(job_id) / "ready"
        found: Dict[int, StagedBatchFiles] = {}
        if not ready_root.is_dir():
            return found
        for slot_dir in sorted(ready_root.iterdir()):
            if not slot_dir.is_dir() or not slot_dir.name.startswith("seq_"):
                continue
            gif_path = slot_dir / "item.gif"
            meta_path = slot_dir / "slot.json"
            if not gif_path.is_file():
                shutil.rmtree(slot_dir, ignore_errors=True)
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
                seq = int(meta.get("seq") or slot_dir.name.removeprefix("seq_"))
            except Exception:
                continue
            comp_path = slot_dir / "companion.png"
            has_comp = comp_path.is_file() and bool(meta.get("companion_relpath"))
            entry = StagedBatchFiles(
                job_id=job_id,
                seq=seq,
                rel_dir=str(meta.get("rel_dir") or ""),
                filename=str(meta.get("filename") or f"item_{seq}.gif"),
                gif_path=gif_path,
                size_bytes=gif_path.stat().st_size,
                companion_relpath=str(meta.get("companion_relpath") or "") if has_comp else "",
                companion_path=comp_path if has_comp else None,
                companion_size_bytes=comp_path.stat().st_size if has_comp else 0,
            )
            found[seq] = entry
        with self._lock:
            for seq, entry in found.items():
                self._staged[(job_id, seq)] = entry
        return found

    def cleanup_orphan_jobs(self, active_job_ids: Set[str]) -> None:
        safe_active = {_safe_job_dir_name(jid) for jid in active_job_ids}
        root = self._root()
        for child in root.iterdir():
            if child.is_dir() and child.name not in safe_active:
                shutil.rmtree(child, ignore_errors=True)


ephemeral_batch_queue = EphemeralBatchArtifactQueue()
