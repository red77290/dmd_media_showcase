"""
Domain Port: `IFrameStreamerPort` for real-time clock-paced UDP streaming (`ffmpeg -re` -> `DMDF`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Awaitable, Callable, Optional


class IFrameStreamerPort(ABC):
    """
    Streams a prepared panorama + video sequence to a DMD client over UDP at exact real-time
    frame cadence (`ffmpeg -re`), emitting `DMDF` chunked RGB565LE datagrams.
    """

    @abstractmethod
    async def start_udp_clock_stream(
        self,
        dmd_id: str,
        item_id: str,
        stream_path: Path,
        width: int,
        height: int,
        fps: int,
        udp_ip: str,
        udp_port: int,
        on_eof: Optional[Callable[[str, str, bool], Awaitable[None]]] = None,
    ) -> bool:
        """Start streaming `stream_path` to `udp_ip:udp_port` in the background."""

    @abstractmethod
    async def stop_stream(self, dmd_id: str) -> None:
        """Abort any active UDP stream for `dmd_id` immediately."""
