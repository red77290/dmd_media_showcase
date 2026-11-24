"""
Atomic JSON Persistence Adapter for Batch Scraper Jobs (`app/infrastructure/persistence/json_batch_job_repository.py`).

Implements `IBatchJobRepositoryPort`:
- Stores active and recently completed Batch Scraper jobs in `<data_dir>/batch_jobs.json`
  via crash-safe atomic `tmp -> os.replace` writes.
- Ensures batch tasks remain visible, controllable, and downloadable across browser
  tab/window closures, page refreshes, LAN device switches, and container restarts.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
from typing import Dict, List, Optional, Sequence

from app.domain.ports.batch_scraper_ports import IBatchJobRepositoryPort
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.persistence.batch_jobs")


class JsonBatchJobRepository(IBatchJobRepositoryPort):
    """Thread-safe atomic JSON repository for persistent Batch Scraper job states."""

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._explicit_path = file_path
        self._lock = threading.RLock()

    def _path(self) -> Path:
        if self._explicit_path is not None:
            self._explicit_path.parent.mkdir(parents=True, exist_ok=True)
            return self._explicit_path
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "batch_jobs.json"

    def save_jobs(self, records: Sequence[Dict[str, object]]) -> None:
        target = self._path()
        tmp = target.with_suffix(".json.tmp")
        payload = {
            "schema_version": 1,
            "jobs": list(records),
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        with self._lock:
            try:
                tmp.write_text(serialized, encoding="utf-8")
                os.replace(tmp, target)
            except Exception as exc:
                logger.warning("[BATCH STORE] Failed persisting %s: %s", target, exc)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def load_jobs(self) -> List[Dict[str, object]]:
        target = self._path()
        with self._lock:
            if not target.is_file():
                return []
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
                jobs = raw.get("jobs") if isinstance(raw, dict) else None
                if not isinstance(jobs, list):
                    return []
                return [dict(item) for item in jobs if isinstance(item, dict)]
            except Exception as exc:
                logger.warning("[BATCH STORE] Ignoring unreadable %s: %s", target, exc)
                return []


json_batch_job_repository = JsonBatchJobRepository()
