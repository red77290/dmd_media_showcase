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
import os
import socket
import struct
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.portable_env import get_ffmpeg_binary

logger = logging.getLogger("dmd_showcase.streaming.udp")

DMDF_MAGIC = b"DMDF"
DMDH_MAGIC = b"DMDH"
DMDA_MAGIC = b"DMDA"
DMDF_HEADER_FORMAT = "<4sHBBH"
MAX_UDP_PAYLOAD_BYTES = 1024
NAT_PINHOLE_TTL_S = 180.0


class _UdpPinholeProtocol(asyncio.DatagramProtocol):
    """Receives client-initiated `DMDH` UDP punch datagrams on `:5569` to establish NAT return mappings."""

    def __init__(self, streamer: "FfmpegUdpClockStreamer") -> None:
        self._streamer = streamer

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._streamer.record_udp_hello_datagram(data, addr)


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
        self._nat_pinholes: Dict[str, Tuple[str, int, float]] = {}
        self._listener_transport: Optional[asyncio.DatagramTransport] = None
        self._listener_port: int = 0

    async def start_listener(self, host: str = "0.0.0.0", port: int = 5569) -> bool:
        """Bind the server UDP listener (`:5569`, or `0` for OS-assigned ephemeral) so NAT/VM-bridged DMD clients can punch a return pinhole."""
        if port < 0:
            return False
        if self._listener_transport is not None and not self._listener_transport.is_closing():
            return True
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _UdpPinholeProtocol(self),
                local_addr=(host, int(port)),
                reuse_port=False,
            )
            self._listener_transport = transport
            sock_info = transport.get_extra_info("sockname")
            self._listener_port = int(sock_info[1]) if sock_info else int(port)
            logger.info(
                "[UDP STREAM] Bound NAT pinhole & DMDF server socket on %s:%d/udp",
                host,
                self._listener_port,
            )
            return True
        except OSError as exc:
            logger.warning(
                "[UDP STREAM] Could not bind UDP pinhole listener on %s:%d (%s) — falling back to ephemeral sockets",
                host,
                port,
                exc,
            )
            self._listener_transport = None
            self._listener_port = 0
            return False

    async def stop_listener(self) -> None:
        """Close the `:5569` server UDP listener if active."""
        transport = self._listener_transport
        self._listener_transport = None
        self._listener_port = 0
        if transport is not None and not transport.is_closing():
            transport.close()

    def record_udp_hello_datagram(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Record a client `DMDH|<dmd_id>[|<lan_ip>]` UDP punch datagram and reply with `DMDA`."""
        if len(data) < 4 or data[:4] != DMDH_MAGIC:
            return
        raw_tail = data[4:].lstrip(b"| :\x00").decode("utf-8", errors="ignore").strip()
        parts = [p.strip() for p in raw_tail.split("|") if p.strip()]
        dmd_id = parts[0] if len(parts) >= 1 else ""
        declared_ip = parts[1] if len(parts) >= 2 else ""
        now_mono = time.monotonic()
        entry = (str(addr[0]), int(addr[1]), now_mono)
        if dmd_id:
            self._nat_pinholes[dmd_id] = entry
        if declared_ip and declared_ip not in ("0.0.0.0", "127.0.0.1"):
            self._nat_pinholes[f"@ip:{declared_ip}"] = entry
        if addr[0] and addr[0] not in ("0.0.0.0", "127.0.0.1"):
            self._nat_pinholes[f"@ip:{addr[0]}"] = entry

        if self._listener_transport is not None and not self._listener_transport.is_closing():
            try:
                self._listener_transport.sendto(DMDA_MAGIC + b"\x01\x00\x00\x00", addr)
            except OSError:
                pass

    def resolve_nat_pinhole(self, dmd_id: str, udp_ip: str) -> Optional[Tuple[str, int]]:
        """Return a fresh `(ip, port)` NAT pinhole tuple if the client punched `:5569` recently."""
        now_mono = time.monotonic()
        for key in (dmd_id, f"@ip:{udp_ip}"):
            if not key:
                continue
            entry = self._nat_pinholes.get(key)
            if entry is not None:
                pin_ip, pin_port, ts = entry
                if (now_mono - ts) <= NAT_PINHOLE_TTL_S:
                    return (pin_ip, pin_port)
                self._nat_pinholes.pop(key, None)
        return None

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
        completed_ok = False
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        use_connected_udp = self._packet_sink is None
        if use_connected_udp:
            try:
                sock.connect((udp_ip, int(udp_port)))
            except OSError:
                use_connected_udp = False
        stream_fh = None

        try:
            file_size = stream_path.stat().st_size if stream_path.exists() else 0
            if file_size < frame_bytes:
                completed_ok = True
                return

            # Zero-copy DMDS header skip: use unbuffered FileIO (`buffering=0`) + kernel `os.lseek`
            # so `create_subprocess_exec(stdin=stream_fh)` starts reading at exact byte 16 without
            # Python BufferedReader prefetching or copying multi-megabyte raw565 buffers in RAM/tmpfs.
            stream_fh = stream_path.open("rb", buffering=0)
            magic = stream_fh.read(4)
            header_offset = 16 if magic == b"DMDS" else 0
            if file_size - header_offset < frame_bytes:
                completed_ok = True
                return
            os.lseek(stream_fh.fileno(), header_offset, os.SEEK_SET)

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
                "pipe:0",
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
            t_launch = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=stream_fh,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._active_procs[dmd_id] = proc
            assert proc.stdout is not None

            frame_seq = 0
            consecutive_icmp_refused = 0
            icmp_aborted = False
            logged_pinhole = False
            while not icmp_aborted:
                try:
                    frame_buf = await proc.stdout.readexactly(frame_bytes)
                except asyncio.IncompleteReadError:
                    break
                pinhole_addr: Optional[Tuple[str, int]] = None
                if (
                    self._packet_sink is None
                    and self._listener_transport is not None
                    and not self._listener_transport.is_closing()
                ):
                    pinhole_addr = self.resolve_nat_pinhole(dmd_id, udp_ip)
                    if pinhole_addr is not None and not logged_pinhole:
                        logged_pinhole = True
                        logger.info(
                            "[UDP STREAM] Routing DMDF chunks for dmd_id='%s' via NAT pinhole %s:%d (source :%d)",
                            dmd_id,
                            pinhole_addr[0],
                            pinhole_addr[1],
                            self._listener_port,
                        )
                packets = pack_dmdf_frame_chunks(frame_buf, frame_seq)
                for pkt_idx, pkt in enumerate(packets):
                    if self._packet_sink is not None:
                        self._packet_sink(pkt, udp_ip, udp_port)
                        continue
                    # Micro-pace in 4-KB groups (every 4 chunks) so both 128x32 (8 chunks on
                    # single-radio classic ESP32) and 256x64 (32 chunks) avoid Wi-Fi AP / LwIP
                    # burst drops while taking < 0.7 ms per 128x32 frame.
                    if pkt_idx > 0 and (pkt_idx % 4) == 0:
                        await asyncio.sleep(0.0006)
                    if pinhole_addr is not None and self._listener_transport is not None:
                        try:
                            self._listener_transport.sendto(pkt, pinhole_addr)
                            consecutive_icmp_refused = 0
                        except OSError:
                            pass
                        continue
                    try:
                        if use_connected_udp:
                            sock.send(pkt)
                        else:
                            sock.sendto(pkt, (udp_ip, udp_port))
                        consecutive_icmp_refused = 0
                    except BlockingIOError:
                        await asyncio.sleep(0.001)
                        try:
                            if use_connected_udp:
                                sock.send(pkt)
                            else:
                                sock.sendto(pkt, (udp_ip, udp_port))
                            consecutive_icmp_refused = 0
                        except (BlockingIOError, ConnectionRefusedError):
                            pass
                    except ConnectionRefusedError:
                        # Connected UDP socket received ICMP Port Unreachable from the DMD:
                        # ignore brief startup race (< 0.5s), otherwise abort zombie stream immediately.
                        if (time.monotonic() - t_launch) >= 0.5:
                            consecutive_icmp_refused += 1
                            if consecutive_icmp_refused >= 8:
                                logger.info(
                                    "[UDP STREAM] Remote UDP port %s:%d closed (ICMP unreachable) for dmd_id='%s' -> stopping stream",
                                    udp_ip,
                                    udp_port,
                                    dmd_id,
                                )
                                icmp_aborted = True
                                break
                frame_seq = (frame_seq + 1) & 0xFFFF

            if icmp_aborted and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
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
            proc_cleanup = self._active_procs.pop(dmd_id, None)
            if proc_cleanup is not None and proc_cleanup.returncode is None:
                try:
                    proc_cleanup.kill()
                except Exception:
                    pass
            self._active_tasks.pop(dmd_id, None)
            if stream_fh is not None:
                try:
                    stream_fh.close()
                except Exception:
                    pass
            sock.close()
            if on_eof is not None:
                try:
                    await on_eof(dmd_id, item_id, completed_ok)
                except Exception as exc:
                    logger.debug("[UDP STREAM] on_eof callback error: %s", exc)


udp_clock_streamer = FfmpegUdpClockStreamer()
