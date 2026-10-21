"""
Domain Port: `IMediaTranscoderPort` for converting HD master images and videos into DMD streams.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict

from app.domain.entities import FilterCriteria, MediaCandidate, PanelGeometry


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
