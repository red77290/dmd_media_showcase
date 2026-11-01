"""
Domain Port: `IMediaTranscoderPort` for converting HD master images and videos into DMD streams.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict

from app.domain.entities import FilterCriteria, MediaCandidate, PanelGeometry


class ConversionAbortedError(Exception):
    """Raised when an in-flight media conversion for a DMD client is explicitly aborted."""


class IMediaTranscoderPort(ABC):
    """
    Transcodes clean HD master assets (posters, marquees, snapshots, and 720p/1080p trailers)
    into DMD RGB565 mini-jackets and unified panorama+video streams via `dmd_gif_converter`.
    """

    @abstractmethod
    async def transcode_candidate(
        self,
        candidate: MediaCandidate,
        criteria: FilterCriteria,
        geometry: PanelGeometry,
        out_dir: Path,
    ) -> Dict[str, object]:
        """
        Build the mini-jacket (`.rgb565` + base64), granular scrolling panoramas (`Jacket` ->
        `Marquee` -> `Snapshots`), video clip (`Auto-Action` + `Smart Color Boost`), and the
        unified `.rgb565.stream` + `.mp4` sequence ready for `ffmpeg -re` UDP streaming.
        """

    def abort_conversions_for_dmd(self, dmd_id: str) -> int:
        """
        Terminate all active converter processes and child subprocesses associated with `dmd_id`.
        Returns the number of terminated worker process groups.
        """
        return 0
