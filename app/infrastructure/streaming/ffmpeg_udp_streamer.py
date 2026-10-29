"""
Real-time clock-paced UDP frame streamer (`perf_counter` monotonic thread -> `DMDF` chunked RGB565LE datagrams).

Binary `DMDF` UDP datagram wire format (Little-Endian, 10-byte header + <= 1408-byte payload):
- `0..4`  : `magic` = `b"DMDF"` (4 bytes)
- `4..6`  : `frame_seq` = `uint16` wrapping frame sequence number
- `6..7`  : `chunk_idx` = `uint8` chunk index within current frame (`0 .. total_chunks - 1`)
- `7..8`  : `total_chunks` = `uint8` total chunks making up a full frame (`width * height * 2` bytes)
- `8..10` : `byte_offset` = `uint16` byte offset in the target RGB565 framebuffer
- `10..N` : raw RGB565LE pixel slice (`<= 1408` bytes, `<= 1418` B UDP payload < Wi-Fi MTU, 6 chunks for 128x32)
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.domain.ports.frame_streamer import IFrameStreamerPort

logger = logging.getLogger("dmd_showcase.streaming.udp")

DMDF_MAGIC = b"DMDF"
DMDH_MAGIC = b"DMDH"
DMDA_MAGIC = b"DMDA"
DMDF_HEADER_FORMAT = "<4sHBBH"
MAX_UDP_PAYLOAD_BYTES = 1408
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
    Streams a prepared `.rgb565.stream` (`DMDS`) file from a dedicated monotonic real-time
    thread (`time.perf_counter()`) and transmits `DMDF` UDP packets (`<= 1408B` payload = `6`
    chunks per `128x32` frame, fitting ESP32 `UDP_RECVMBOX_SIZE=6` & `kRxBaWin=6`) to the DMD client.
    """

    def __init__(
        self,
        packet_sink: Optional[Callable[[bytes, str, int], None]] = None,
    ) -> None:
        self._active_tasks: Dict[str, asyncio.Task[None]] = {}
        self._active_procs: Dict[str, asyncio.subprocess.Process] = {}
        self._stop_events: Dict[str, threading.Event] = {}
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
        stop_ev = self._stop_events.pop(dmd_id, None)
        if stop_ev is not None:
            stop_ev.set()
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
        stop_ev = threading.Event()
        self._stop_events[dmd_id] = stop_ev
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
                stop_ev=stop_ev,
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
        stop_ev: threading.Event,
        on_eof: Optional[Callable[[str, str, bool], Awaitable[None]]],
    ) -> None:
        frame_bytes = width * height * 2
        completed_ok = False
        loop = asyncio.get_running_loop()
        done_fut: asyncio.Future[Tuple[bool, int]] = loop.create_future()

        def _tx_worker() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            except OSError:
                pass
            sock.settimeout(0.02)
            use_connected_udp = self._packet_sink is None
            if use_connected_udp:
                try:
                    sock.connect((udp_ip, int(udp_port)))
                except OSError:
                    use_connected_udp = False

            fd = -1
            frames_sent = 0
            ok_flag = False
            try:
                if not stream_path.exists():
                    ok_flag = True
                    return
                file_size = stream_path.stat().st_size
                if file_size < frame_bytes:
                    ok_flag = True
                    return

                fd = os.open(str(stream_path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
                magic = os.read(fd, 4)
                header_offset = 16 if magic == b"DMDS" else 0
                payload_bytes = file_size - header_offset
                if payload_bytes < frame_bytes:
                    ok_flag = True
                    return
                total_frames = payload_bytes // frame_bytes

                logger.info(
                    "[UDP STREAM] Starting monotonic DMDF stream for dmd_id='%s' item='%s' -> %s:%d (%dx%d @ %dfps, %d frames)",
                    dmd_id,
                    item_id,
                    udp_ip,
                    udp_port,
                    width,
                    height,
                    fps,
                    total_frames,
                )

                t_launch = time.perf_counter()
                frame_seq = 0
                frame_idx = 0
                consecutive_icmp_refused = 0
                logged_pinhole = False

                while not stop_ev.is_set() and frame_idx < total_frames:
                    listener_transport = self._listener_transport
                    listener_closing = (
                        listener_transport.is_closing()
                        if listener_transport is not None and hasattr(listener_transport, "is_closing")
                        else False
                    )
                    listener_sock = (
                        listener_transport.get_extra_info("socket")
                        if listener_transport is not None and hasattr(listener_transport, "get_extra_info")
                        else None
                    )
                    pinhole_addr: Optional[Tuple[str, int]] = None
                    if (
                        self._packet_sink is None
                        and listener_transport is not None
                        and not listener_closing
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

                    is_deterministic_capture = (self._packet_sink is not None) or (
                        pinhole_addr is not None and not isinstance(listener_sock, socket.socket)
                    )

                    if frame_idx > 0:
                        target_t = t_launch + (frame_idx / float(fps))
                        now_t = time.perf_counter()
                        wait_s = target_t - now_t
                        if wait_s > 0.0004:
                            if stop_ev.wait(wait_s):
                                break
                        elif not is_deterministic_capture and wait_s < -(1.5 / float(fps)):
                            due_frame = int((now_t - t_launch) * float(fps))
                            if due_frame >= total_frames:
                                break
                            if due_frame > frame_idx:
                                frame_idx = due_frame

                    os.lseek(fd, header_offset + frame_idx * frame_bytes, os.SEEK_SET)
                    chunks_read = bytearray()
                    while len(chunks_read) < frame_bytes:
                        part = os.read(fd, frame_bytes - len(chunks_read))
                        if not part:
                            break
                        chunks_read.extend(part)
                    if len(chunks_read) < frame_bytes:
                        break

                    packets = pack_dmdf_frame_chunks(bytes(chunks_read), frame_seq)
                    icmp_aborted = False
                    for pkt_idx, pkt in enumerate(packets):
                        if stop_ev.is_set():
                            break
                        if self._packet_sink is not None:
                            self._packet_sink(pkt, udp_ip, udp_port)
                            continue

                        # Micro-pace every 2 chunks (0.45ms) so 128x32 (6 chunks <= ESP32 6-slot
                        # UDP_RECVMBOX_SIZE & kRxBaWin) and 256x64 (24 chunks) never burst-drop on Wi-Fi.
                        if not is_deterministic_capture and pkt_idx > 0 and (pkt_idx % 2) == 0:
                            time.sleep(0.00045)

                        if pinhole_addr is not None and listener_transport is not None:
                            if isinstance(listener_sock, socket.socket):
                                try:
                                    listener_sock.sendto(pkt, pinhole_addr)
                                    consecutive_icmp_refused = 0
                                except OSError:
                                    pass
                            else:
                                try:
                                    loop.call_soon_threadsafe(listener_transport.sendto, pkt, pinhole_addr)
                                    consecutive_icmp_refused = 0
                                except RuntimeError:
                                    break
                            continue

                        try:
                            if use_connected_udp:
                                sock.send(pkt)
                            else:
                                sock.sendto(pkt, (udp_ip, udp_port))
                            consecutive_icmp_refused = 0
                        except (BlockingIOError, socket.timeout):
                            time.sleep(0.0008)
                            try:
                                if use_connected_udp:
                                    sock.send(pkt)
                                else:
                                    sock.sendto(pkt, (udp_ip, udp_port))
                                consecutive_icmp_refused = 0
                            except OSError:
                                pass
                        except ConnectionRefusedError:
                            if (time.perf_counter() - t_launch) >= 0.5:
                                consecutive_icmp_refused += 1
                                if consecutive_icmp_refused >= 6:
                                    logger.info(
                                        "[UDP STREAM] Remote UDP port %s:%d closed (ICMP unreachable) for dmd_id='%s' -> stopping stream",
                                        udp_ip,
                                        udp_port,
                                        dmd_id,
                                    )
                                    icmp_aborted = True
                                    break
                        except OSError:
                            pass

                    if icmp_aborted:
                        break
                    frame_seq = (frame_seq + 1) & 0xFFFF
                    frames_sent += 1
                    frame_idx += 1

                ok_flag = not stop_ev.is_set()
            except Exception as exc:
                logger.warning("[UDP STREAM] Error streaming to %s:%d (%s)", udp_ip, udp_port, exc)
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                try:
                    sock.close()
                except OSError:
                    pass
                if not loop.is_closed():
                    try:
                        loop.call_soon_threadsafe(
                            lambda: done_fut.set_result((ok_flag, frames_sent))
                            if not done_fut.done()
                            else None
                        )
                    except RuntimeError:
                        pass

        worker = threading.Thread(
            target=_tx_worker,
            name=f"dmdf-udp-{dmd_id[:16]}",
            daemon=True,
        )
        worker.start()

        try:
            completed_ok, sent_count = await done_fut
            logger.info(
                "[UDP STREAM] Completed DMDF stream for dmd_id='%s' item='%s' (%d frames sent)",
                dmd_id,
                item_id,
                sent_count,
            )
        except asyncio.CancelledError:
            stop_ev.set()
            logger.info("[UDP STREAM] Cancelled active stream for dmd_id='%s'", dmd_id)
            raise
        finally:
            stop_ev.set()
            worker.join(timeout=0.25)
            if self._stop_events.get(dmd_id) is stop_ev:
                self._stop_events.pop(dmd_id, None)
            self._active_meta.pop(dmd_id, None)
            self._active_procs.pop(dmd_id, None)
            self._active_tasks.pop(dmd_id, None)
            if on_eof is not None:
                try:
                    await on_eof(dmd_id, item_id, completed_ok)
                except Exception as exc:
                    logger.debug("[UDP STREAM] on_eof callback error: %s", exc)


udp_clock_streamer = FfmpegUdpClockStreamer()
