"""
Self-contained asyncio MQTT 3.1.1 Broker & Client with full support for:
- `retain=True` retained messages delivered immediately upon `SUBSCRIBE`
- `Last Will and Testament (LWT)` published automatically on ungraceful disconnect
- Topic wildcards (`+` single-level and `#` multi-level)
- Zero external native dependencies (100% compatible with Windows/macOS PyInstaller & Docker).
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("dmd_showcase.mqtt.broker")

MessageCallback = Callable[[str, bytes, bool], Awaitable[None]]


def mqtt_topic_matches(pattern: str, topic: str) -> bool:
    """Return True if `topic` matches MQTT filter `pattern` (supporting `+` and `#`)."""
    if pattern == "#" or pattern == topic:
        return True
    p_parts = pattern.split("/")
    t_parts = topic.split("/")
    for idx, p_seg in enumerate(p_parts):
        if p_seg == "#":
            return True
        if idx >= len(t_parts):
            return False
        if p_seg != "+" and p_seg != t_parts[idx]:
            return False
    return len(p_parts) == len(t_parts)


def _encode_remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        encoded = length % 128
        length //= 128
        if length > 0:
            encoded |= 0x80
        out.append(encoded)
        if length == 0:
            break
    return bytes(out)


async def _read_remaining_length(reader: asyncio.StreamReader) -> int:
    multiplier = 1
    value = 0
    while True:
        b = await reader.readexactly(1)
        encoded = b[0]
        value += (encoded & 127) * multiplier
        if (encoded & 128) == 0:
            break
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            raise ValueError("Malformed MQTT Remaining Length")
    return value


def _encode_utf8(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def _decode_utf8(buf: bytes, offset: int) -> Tuple[str, int]:
    (length,) = struct.unpack_from("!H", buf, offset)
    offset += 2
    return buf[offset : offset + length].decode("utf-8", errors="replace"), offset + length


def build_publish_packet(
    topic: str, payload: bytes, qos: int = 0, retain: bool = False, pkt_id: int = 1
) -> bytes:
    flags = ((qos & 0x03) << 1) | (0x01 if retain else 0x00)
    header_first_byte = 0x30 | flags
    var_hdr = _encode_utf8(topic)
    if qos > 0:
        var_hdr += struct.pack("!H", pkt_id & 0xFFFF)
    body = var_hdr + payload
    return bytes([header_first_byte]) + _encode_remaining_length(len(body)) + body


@dataclass
class _ClientSession:
    client_id: str
    writer: asyncio.StreamWriter
    subscriptions: List[str] = field(default_factory=list)
    will_topic: Optional[str] = None
    will_payload: bytes = b""
    will_retain: bool = False
    clean_disconnect: bool = False


class EmbeddedMqttBroker:
    """
    Lightweight asyncio MQTT 3.1.1 Broker listening on `:1883` (or ephemeral test port).
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 1883) -> None:
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None
        self.retained: Dict[str, bytes] = {}
        self.clients: Dict[str, _ClientSession] = {}
        self._internal_callbacks: List[Tuple[str, MessageCallback]] = []

    async def start(self) -> int:
        if self._server is not None:
            return self.port
        try:
            self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
            sockets = self._server.sockets
            if sockets:
                self.port = int(sockets[0].getsockname()[1])
            logger.info("[MQTT BROKER] Embedded MQTT 3.1.1 broker listening on %s:%d", self.host, self.port)
        except OSError as exc:
            logger.warning("[MQTT BROKER] Port %d already bound (%s)", self.port, exc)
        return self.port

    async def stop(self) -> None:
        for sess in list(self.clients.values()):
            try:
                sess.writer.close()
            except Exception:
                pass
        self.clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def subscribe_internal(self, topic_filter: str, callback: MessageCallback) -> None:
        self._internal_callbacks.append((topic_filter, callback))

    async def publish(self, topic: str, payload: bytes, retain: bool = False) -> None:
        if retain:
            if payload:
                self.retained[topic] = payload
            else:
                self.retained.pop(topic, None)

        pkt = build_publish_packet(topic, payload, qos=0, retain=retain)
        for sess in list(self.clients.values()):
            if any(mqtt_topic_matches(sub, topic) for sub in sess.subscriptions):
                try:
                    sess.writer.write(pkt)
                    await sess.writer.drain()
                except Exception:
                    pass

        for pattern, cb in list(self._internal_callbacks):
            if mqtt_topic_matches(pattern, topic):
                try:
                    await cb(topic, payload, retain)
                except Exception as exc:
                    logger.debug("[MQTT BROKER] Internal subscriber error on %s: %s", topic, exc)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session: Optional[_ClientSession] = None
        try:
            while True:
                first = await reader.readexactly(1)
                pkt_type = (first[0] & 0xF0) >> 4
                flags = first[0] & 0x0F
                rem_len = await _read_remaining_length(reader)
                body = await reader.readexactly(rem_len) if rem_len > 0 else b""

                if pkt_type == 1:  # CONNECT
                    _, pos = _decode_utf8(body, 0)  # Protocol Name ("MQTT" or "MQIsdp")
                    pos += 1  # Protocol Level
                    conn_flags = body[pos]
                    pos += 3  # Connect Flags + KeepAlive (2B)
                    client_id, pos = _decode_utf8(body, pos)
                    client_id = client_id or f"anon_{id(writer)}"
                    will_flag = bool(conn_flags & 0x04)
                    will_retain = bool(conn_flags & 0x20)
                    will_topic: Optional[str] = None
                    will_payload = b""
                    if will_flag:
                        will_topic, pos = _decode_utf8(body, pos)
                        (w_len,) = struct.unpack_from("!H", body, pos)
                        pos += 2
                        will_payload = body[pos : pos + w_len]
                    session = _ClientSession(
                        client_id=client_id,
                        writer=writer,
                        will_topic=will_topic,
                        will_payload=will_payload,
                        will_retain=will_retain,
                    )
                    self.clients[client_id] = session
                    writer.write(b"\x20\x02\x00\x00")  # CONNACK Accepted
                    await writer.drain()

                elif pkt_type == 3 and session:  # PUBLISH
                    qos = (flags >> 1) & 0x03
                    retain = bool(flags & 0x01)
                    topic, pos = _decode_utf8(body, 0)
                    pkt_id = 0
                    if qos > 0:
                        (pkt_id,) = struct.unpack_from("!H", body, pos)
                        pos += 2
                    payload = body[pos:]
                    if qos == 1:
                        writer.write(struct.pack("!BBH", 0x40, 2, pkt_id))  # PUBACK
                        await writer.drain()
                    await self.publish(topic, payload, retain=retain)

                elif pkt_type == 8 and session:  # SUBSCRIBE
                    (pkt_id,) = struct.unpack_from("!H", body, 0)
                    pos = 2
                    return_codes = bytearray()
                    new_subs: List[str] = []
                    while pos < len(body):
                        sub_filter, pos = _decode_utf8(body, pos)
                        req_qos = body[pos] & 0x03
                        pos += 1
                        if sub_filter not in session.subscriptions:
                            session.subscriptions.append(sub_filter)
                        new_subs.append(sub_filter)
                        return_codes.append(min(1, req_qos))
                    suback_body = struct.pack("!H", pkt_id) + bytes(return_codes)
                    writer.write(b"\x90" + _encode_remaining_length(len(suback_body)) + suback_body)
                    await writer.drain()
                    # Immediately deliver matching retained messages
                    for r_topic, r_payload in list(self.retained.items()):
                        if any(mqtt_topic_matches(sf, r_topic) for sf in new_subs):
                            writer.write(build_publish_packet(r_topic, r_payload, qos=0, retain=True))
                    await writer.drain()

                elif pkt_type == 12:  # PINGREQ
                    writer.write(b"\xD0\x00")  # PINGRESP
                    await writer.drain()

                elif pkt_type == 14 and session:  # DISCONNECT
                    session.clean_disconnect = True
                    break
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            if session is not None:
                self.clients.pop(session.client_id, None)
                if not session.clean_disconnect and session.will_topic:
                    await self.publish(
                        session.will_topic,
                        session.will_payload,
                        retain=session.will_retain,
                    )
            try:
                writer.close()
            except Exception:
                pass


class SimpleAsyncMqttClient:
    """
    Minimal asyncio MQTT 3.1.1 TCP client used for connecting to an external broker
    or testing the embedded broker over real sockets.
    """

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        will_topic: Optional[str] = None,
        will_payload: bytes = b"",
        will_retain: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.will_topic = will_topic
        self.will_payload = will_payload
        self.will_retain = will_retain
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._rx_task: Optional[asyncio.Task[None]] = None
        self._callbacks: List[Tuple[str, MessageCallback]] = []
        self._pkt_id = 1

    async def connect(
        self,
        custom_streams: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None,
    ) -> None:
        if custom_streams is not None:
            self._reader, self._writer = custom_streams
        else:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        conn_flags = 0x02  # Clean Session
        payload = _encode_utf8(self.client_id)
        if self.will_topic:
            conn_flags |= 0x04 | (0x20 if self.will_retain else 0x00)
            payload += _encode_utf8(self.will_topic)
            payload += struct.pack("!H", len(self.will_payload)) + self.will_payload
        var_hdr = _encode_utf8("MQTT") + bytes([0x04, conn_flags]) + struct.pack("!H", 60)
        body = var_hdr + payload
        self._writer.write(b"\x10" + _encode_remaining_length(len(body)) + body)
        await self._writer.drain()
        ack = await self._reader.readexactly(4)
        if ack[0] != 0x20 or ack[3] != 0x00:
            raise ConnectionError(f"MQTT CONNACK rejected: {ack!r}")
        self._rx_task = asyncio.get_running_loop().create_task(self._read_loop())

    async def subscribe(self, topic_filter: str, callback: MessageCallback) -> None:
        self._callbacks.append((topic_filter, callback))
        if not self._writer:
            return
        self._pkt_id = (self._pkt_id % 65535) + 1
        body = struct.pack("!H", self._pkt_id) + _encode_utf8(topic_filter) + b"\x01"
        self._writer.write(b"\x82" + _encode_remaining_length(len(body)) + body)
        await self._writer.drain()

    async def publish(self, topic: str, payload: bytes, retain: bool = False) -> None:
        if not self._writer:
            return
        self._writer.write(build_publish_packet(topic, payload, qos=0, retain=retain))
        await self._writer.drain()

    async def disconnect(self, graceful: bool = True) -> None:
        if self._writer and graceful:
            try:
                self._writer.write(b"\xE0\x00")
                await self._writer.drain()
            except Exception:
                pass
        if self._rx_task:
            self._rx_task.cancel()
        if self._writer:
            self._writer.close()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                first = await self._reader.readexactly(1)
                pkt_type = (first[0] & 0xF0) >> 4
                flags = first[0] & 0x0F
                rem_len = await _read_remaining_length(self._reader)
                body = await self._reader.readexactly(rem_len) if rem_len > 0 else b""
                if pkt_type == 3:  # PUBLISH
                    qos = (flags >> 1) & 0x03
                    retain = bool(flags & 0x01)
                    topic, pos = _decode_utf8(body, 0)
                    if qos > 0:
                        pos += 2
                    payload = body[pos:]
                    for pat, cb in list(self._callbacks):
                        if mqtt_topic_matches(pat, topic):
                            await cb(topic, payload, retain)
        except (asyncio.IncompleteReadError, asyncio.CancelledError, OSError):
            pass
