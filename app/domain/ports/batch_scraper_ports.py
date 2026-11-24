"""
Hexagonal Domain Ports for the Batch Scraper & Ephemeral Stream Queue (`app/domain/ports/batch_scraper_ports.py`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.domain.batch_scraper_specs import ChapterSelectionSpec, GifEncodingSpec
from app.domain.entities import MediaCandidate


@dataclass(frozen=True)
class ComposedGifResult:
    """Output produced by `IBatchGifComposerPort` in an isolated temporary directory."""

    gif_path: Path
    width: int
    height: int
    fps: int
    frame_count: int
    duration_s: float
    size_bytes: int
    marquee_png_path: Optional[Path] = None
    marquee_size_bytes: int = 0


@dataclass(frozen=True)
class StagedBatchFiles:
    """Paths held transiently in the ephemeral spool until acknowledged by the browser."""

    job_id: str
    seq: int
    rel_dir: str
    filename: str
    gif_path: Path
    size_bytes: int
    companion_relpath: str = ""
    companion_path: Optional[Path] = None
    companion_size_bytes: int = 0


class IBatchGifComposerPort(ABC):
    """Outbound port composing a pure 24-bit RGB888 `.gif` from selected Showcase chapters."""

    @abstractmethod
    async def compose_candidate_gif(
        self,
        candidate: MediaCandidate,
        chapters: ChapterSelectionSpec,
        encoding: GifEncodingSpec,
        work_dir: Path,
        job_id: str = "",
        export_marquee_png: bool = False,
    ) -> Optional[ComposedGifResult]:
        """Compose a unified RGB888 `.gif` (and optional companion marquee `.png`) or return `None`."""


class IEphemeralArtifactQueuePort(ABC):
    """Outbound port managing bounded ephemeral staging files unlinked immediately upon client ACK."""

    @abstractmethod
    def create_worker_scratch_dir(self, job_id: str, token: str) -> Path:
        """Create an isolated per-item scratch directory outside RAM `tmpfs`."""

    @abstractmethod
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
        """Move completed `.gif` (and optional companion `.png`) into the bounded spool slot."""

    @abstractmethod
    def get_staged(self, job_id: str, seq: int) -> Optional[StagedBatchFiles]:
        """Retrieve a staged artifact by `(job_id, seq)` for streaming to the client."""

    @abstractmethod
    def ack_and_unlink(self, job_id: str, seq: int) -> bool:
        """Unlink staged files immediately from server disk once written by the client."""

    @abstractmethod
    def purge_job(self, job_id: str) -> None:
        """Delete all remaining scratch & staged files for `job_id`."""
