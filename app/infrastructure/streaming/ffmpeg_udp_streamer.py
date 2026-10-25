"""
Real-time clock-paced UDP frame streamer (`ffmpeg -re` -> `DMDF` chunked RGB565LE datagrams).

Binary `DMDF` UDP datagram wire format (Little-Endian, 10-byte header + <= 1024-byte payload):
- `0..4`  : `magic` = `b"DMDF"` (4 bytes)
- `4..6`  : `frame_seq` = `uint16` wrapping frame sequence number
- `6..7`  : `chunk_idx` = `uint8` chunk index within current frame (`0 .. total_chunks - 1`)
- `7..8`  : `total_chunks` = `uint8` total chunks making up a full frame (`width * height * 2` bytes)
- `8..10` : `byte_offset` = `uint16` byte offset in the target RGB565 framebuffer
- `10..N` : raw RGB565LE pixel slice (`<= 1024` bytes, MTU-safe over Wi-Fi)
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.portable_env import get_ffmpeg_binary

logger = logging.getLogger("dmd_showcase.streaming.udp")

DMDF_MAGIC = b"DMDF"
DMDF_HEADER_FORMAT = "<4sHBBH"
MAX_UDP_PAYLOAD_BYTES = 1024


def pack_dmdf_frame_chunks(
    frame_rgb565: bytes,
    frame_seq: int,
    max_chunk_bytes: int = MAX_UDP_PAYLOAD_BYTES,
) -> List[bytes]:
    """
    Slice a single RGB565LE frame (`width * height * 2` bytes) into MTU-safe `DMDF` UDP packets.
    """
    total_len = len(frame_rgb565)
    if total_len == 0:
        return []
    total_chunks = (total_len + max_chunk_bytes - 1) // max_chunk_bytes
    seq_u16 = frame_seq & 0xFFFF
    packets: List[bytes] = []
    for chunk_idx in range(total_chunks):
        byte_offset = chunk_idx * max_chunk_bytes
        payload = frame_rgb565[byte_offset : byte_offset + max_chunk_bytes]
        header = struct.pack(
            DMDF_HEADER_FORMAT,
            DMDF_MAGIC,
            seq_u16,
            chunk_idx & 0xFF,
            total_chunks & 0xFF,
            byte_offset & 0xFFFF,
        )
        packets.append(header + payload)
    return packets


class FfmpegUdpClockStreamer(IFrameStreamerPort):
    """
    Streams a prepared `.rgb565.stream` (`DMDS`) file using `ffmpeg -re` as the real-time
    hardware clock source and transmits `DMDF` UDP packets to the DMD client.
    """

    def __init__(
        self,
        packet_sink: Optional[Callable[[bytes, str, int], None]] = None,
    ) -> None:
        self._active_tasks: Dict[str, asyncio.Task[None]] = {}
        self._active_procs: Dict[str, asyncio.subprocess.Process] = {}
        self._active_meta: Dict[str, Dict[str, Any]] = {}
        self._packet_sink = packet_sink

    @staticmethod
    def _is_task_live(task: Optional[asyncio.Task[None]]) -> bool:
        if task is None or task.done():
            return False
        try:
            t_loop = task.get_loop()
            return t_loop is not None and not t_loop.is_closed()
        except Exception:
            return False

    @property
    def active_streams_count(self) -> int:
        return sum(1 for t in self._active_tasks.values() if self._is_task_live(t))

    def get_active_stream(self, dmd_id: str) -> Optional[Dict[str, Any]]:
        task = self._active_tasks.get(dmd_id)
        if not self._is_task_live(task):
            return None
        return self._active_meta.get(dmd_id)

    def describe_active_streams(self) -> Dict[str, Dict[str, Any]]:
        return {
            dmd_id: dict(meta)
            for dmd_id, meta in self._active_meta.items()
            if self._is_task_live(self._active_tasks.get(dmd_id))
        }

    async def stop_stream(self, dmd_id: str) -> None:
        self._active_meta.pop(dmd_id, None)
        proc = self._active_procs.pop(dmd_id, None)
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        task = self._active_tasks.pop(dmd_id, None)
        if task is not None and not task.done():
            try:
                t_loop = task.get_loop()
            except Exception:
                t_loop = None
            if t_loop is not None and not t_loop.is_closed():
                try:
                    task.cancel()
                except RuntimeError:
                    return
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None
                if current_loop is not None and t_loop is current_loop:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

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
        await self.stop_stream(dmd_id)
        if not stream_path.exists() or stream_path.stat().st_size <= 16:
            if on_eof:
                await on_eof(dmd_id, item_id, True)
            return False

        effective_fps = max(6, min(30, int(fps)))
        self._active_meta[dmd_id] = {
            "item_id": item_id,
            "width": width,
            "height": height,
            "fps": effective_fps,
            "udp_ip": udp_ip,
            "udp_port": int(udp_port),
            "started_at": time.time(),
        }
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self._run_ffmpeg_udp_loop(
                dmd_id=dmd_id,
                item_id=item_id,
                stream_path=stream_path,
                width=width,
                height=height,
                fps=effective_fps,
                udp_ip=udp_ip,
                udp_port=int(udp_port),
                on_eof=on_eof,
            )
        )
        self._active_tasks[dmd_id] = task
        return True

    async def _run_ffmpeg_udp_loop(
        self,
        dmd_id: str,
        item_id: str,
        stream_path: Path,
        width: int,
        height: int,
        fps: int,
        udp_ip: str,
        udp_port: int,
        on_eof: Optional[Callable[[str, str, bool], Awaitable[None]]],
    ) -> None:
        frame_bytes = width * height * 2
        raw_path = stream_path.with_suffix(".raw565")
        completed_ok = False
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)

        try:
            # Strip the 16-byte DMDS header so `ffmpeg -re` reads pure RGB565LE frames
            data = stream_path.read_bytes()
            raw_frames = data[16:] if data[:4] == b"DMDS" else data
            if len(raw_frames) < frame_bytes:
                completed_ok = True
                return
            raw_path.write_bytes(raw_frames)

            ffmpeg_bin = get_ffmpeg_binary()
            cmd = [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-re",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb565le",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                str(raw_path),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb565le",
                "pipe:1",
            ]
            logger.info(
                "[UDP STREAM] Launching ffmpeg -re for dmd_id='%s' item='%s' -> %s:%d (%dx%d @ %dfps)",
                dmd_id,
                item_id,
                udp_ip,
                udp_port,
                width,
                height,
                fps,
            )
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._active_procs[dmd_id] = proc
            assert proc.stdout is not None

            frame_seq = 0
            while True:
                try:
                    frame_buf = await proc.stdout.readexactly(frame_bytes)
                except asyncio.IncompleteReadError:
                    break
                packets = pack_dmdf_frame_chunks(frame_buf, frame_seq)
                for pkt_idx, pkt in enumerate(packets):
                    if self._packet_sink is not None:
                        self._packet_sink(pkt, udp_ip, udp_port)
                        continue
                    # Micro-pace multi-chunk HD frames (e.g. 256x64 = 32 chunks = 33 KB/frame)
                    # in 8-KB groups so Wi-Fi AP TX queues and ESP32 lwIP UDP mailboxes never
                    # tail-drop chunks 16..31, while 128x32 (8 chunks) sends with zero delay.
                    if pkt_idx > 0 and (pkt_idx % 8) == 0:
                        await asyncio.sleep(0.0008)
                    try:
                        sock.sendto(pkt, (udp_ip, udp_port))
                    except BlockingIOError:
                        await asyncio.sleep(0.001)
                        sock.sendto(pkt, (udp_ip, udp_port))
                frame_seq = (frame_seq + 1) & 0xFFFF

            await proc.wait()
            completed_ok = True
            logger.info(
                "[UDP STREAM] Completed ffmpeg -re stream for dmd_id='%s' item='%s' (%d frames sent)",
                dmd_id,
                item_id,
                frame_seq,
            )
        except asyncio.CancelledError:
            logger.info("[UDP STREAM] Cancelled active stream for dmd_id='%s'", dmd_id)
            raise
        except Exception as exc:
            logger.warning("[UDP STREAM] Error streaming to %s:%d (%s)", udp_ip, udp_port, exc)
        finally:
            self._active_meta.pop(dmd_id, None)
            self._active_procs.pop(dmd_id, None)
            self._active_tasks.pop(dmd_id, None)
            sock.close()
            raw_path.unlink(missing_ok=True)
            if on_eof is not None:
                try:
                    await on_eof(dmd_id, item_id, completed_ok)
                except Exception as exc:
                    logger.debug("[UDP STREAM] on_eof callback error: %s", exc)


udp_clock_streamer = FfmpegUdpClockStreamer()
