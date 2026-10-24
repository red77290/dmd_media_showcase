"""
Zero-dependency mDNS (`dmdmediashowcase.local`) responder for LAN auto-discovery.

Allows ESP32 (`ArcadeMatrix`) and Raspberry Pi (`ArcadeMatrix_RPi`) devices on the
same local network to reach the Showcase server out-of-the-box via the preconfigured
URL `http://dmdmediashowcase.local:8890` without manual IP lookup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
from typing import Optional, Tuple

logger = logging.getLogger("dmd_showcase.mdns")

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
DEFAULT_MDNS_HOSTNAME = "dmdmediashowcase"
SERVICE_TYPE_FQDN = "_dmd-showcase._tcp.local."

DNS_TYPE_A = 1
DNS_TYPE_PTR = 12
DNS_TYPE_TXT = 16
DNS_TYPE_SRV = 33
DNS_TYPE_ANY = 255
DNS_CLASS_IN = 1


def detect_lan_ipv4() -> str:
    """Detect the primary outbound LAN IPv4 address of this machine (or `HOST_LAN_IP` in Docker)."""
    env_ip = (
        os.environ.get("HOST_LAN_IP")
        or os.environ.get("SHOWCASE_HOST_IP")
        or ""
    ).strip()
    if env_ip and not env_ip.startswith("127."):
        return env_ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def encode_dns_name(fqdn: str) -> bytes:
    """Encode a dot-separated domain name into DNS wire format."""
    parts = [p for p in fqdn.strip(".").split(".") if p]
    out = bytearray()
    for part in parts:
        encoded = part.encode("utf-8")
        out.append(len(encoded))
        out.extend(encoded)
    out.append(0)
    return bytes(out)


def decode_dns_name(packet: bytes, offset: int) -> Tuple[str, int]:
    """Decode a DNS name supporting RFC 1035 pointer compression."""
    labels = []
    pos = offset
    jumped = False
    next_offset = offset
    visited = 0

    while pos < len(packet) and visited < 64:
        visited += 1
        length = packet[pos]
        if length == 0:
            pos += 1
            if not jumped:
                next_offset = pos
            break
        if (length & 0xC0) == 0xC0:
            if pos + 1 >= len(packet):
                return "", len(packet)
            ptr = ((length & 0x3F) << 8) | packet[pos + 1]
            if not jumped:
                next_offset = pos + 2
                jumped = True
            pos = ptr
            continue
        pos += 1
        if pos + length > len(packet):
            return "", len(packet)
        labels.append(packet[pos : pos + length].decode("utf-8", errors="ignore"))
        pos += length
        if not jumped:
            next_offset = pos

    name = ".".join(labels) + "." if labels else "."
    return name.lower(), next_offset


def build_mdns_a_response(txid: int, fqdn: str, ipv4: str, ttl: int = 120) -> bytes:
    """Build an authoritative mDNS A-record response packet."""
    header = struct.pack("!HHHHHH", txid, 0x8400, 0, 1, 0, 0)
    qname = encode_dns_name(fqdn)
    rdata = socket.inet_aton(ipv4)
    rr = qname + struct.pack("!HHIH", DNS_TYPE_A, DNS_CLASS_IN | 0x8000, ttl, len(rdata)) + rdata
    return header + rr


def build_mdns_ptr_response(
    txid: int,
    service_fqdn: str,
    instance_fqdn: str,
    host_fqdn: str,
    ipv4: str,
    port: int,
    ttl: int = 120,
) -> bytes:
    """Build an authoritative mDNS PTR + SRV + A response for `_dmd-showcase._tcp.local.`."""
    header = struct.pack("!HHHHHH", txid, 0x8400, 0, 1, 0, 2)
    ptr_rdata = encode_dns_name(instance_fqdn)
    ptr_rr = (
        encode_dns_name(service_fqdn)
        + struct.pack("!HHIH", DNS_TYPE_PTR, DNS_CLASS_IN, ttl, len(ptr_rdata))
        + ptr_rdata
    )

    host_wire = encode_dns_name(host_fqdn)
    srv_rdata = struct.pack("!HHH", 0, 0, port) + host_wire
    srv_rr = (
        ptr_rdata
        + struct.pack("!HHIH", DNS_TYPE_SRV, DNS_CLASS_IN | 0x8000, ttl, len(srv_rdata))
        + srv_rdata
    )

    a_rdata = socket.inet_aton(ipv4)
    a_rr = (
        host_wire
        + struct.pack("!HHIH", DNS_TYPE_A, DNS_CLASS_IN | 0x8000, ttl, len(a_rdata))
        + a_rdata
    )
    return header + ptr_rr + srv_rr + a_rr


def parse_and_reply_mdns(
    packet: bytes,
    hostname: str = DEFAULT_MDNS_HOSTNAME,
    ipv4: str = "127.0.0.1",
    port: int = 8890,
) -> Optional[bytes]:
    """Inspect an incoming mDNS UDP datagram and return a reply packet if matched."""
    if len(packet) < 12:
        return None
    txid, flags, qdcount, _ancount, _nscount, _arcount = struct.unpack("!HHHHHH", packet[:12])
    # Only process queries (QR bit == 0)
    if (flags & 0x8000) != 0 or qdcount == 0:
        return None

    host_fqdn = f"{hostname.lower().strip('.')}.local."
    instance_fqdn = f"{hostname.lower().strip('.')}.{SERVICE_TYPE_FQDN}"

    offset = 12
    for _ in range(qdcount):
        qname, offset = decode_dns_name(packet, offset)
        if offset + 4 > len(packet):
            break
        qtype, _qclass = struct.unpack("!HH", packet[offset : offset + 4])
        offset += 4

        if qname == host_fqdn and qtype in (DNS_TYPE_A, DNS_TYPE_ANY):
            return build_mdns_a_response(txid, host_fqdn, ipv4)
        if qname == SERVICE_TYPE_FQDN and qtype in (DNS_TYPE_PTR, DNS_TYPE_ANY):
            return build_mdns_ptr_response(
                txid, SERVICE_TYPE_FQDN, instance_fqdn, host_fqdn, ipv4, port
            )
    return None


class _MdnsUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, hostname: str, ipv4: str, port: int) -> None:
        self.hostname = hostname
        self.ipv4 = ipv4
        self.port = port
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        reply = parse_and_reply_mdns(data, self.hostname, self.ipv4, self.port)
        if reply and self.transport:
            try:
                # Reply to both multicast group and unicast requester for maximum compatibility
                self.transport.sendto(reply, (MDNS_GROUP, MDNS_PORT))
                if addr[1] != MDNS_PORT:
                    self.transport.sendto(reply, addr)
            except Exception:
                pass


class ShowcaseMdnsResponder:
    """Lifecycle manager for the local network `dmdmediashowcase.local` responder."""

    def __init__(self, hostname: str = DEFAULT_MDNS_HOSTNAME, port: int = 8890) -> None:
        self.hostname = hostname
        self.port = port
        self.ipv4 = "127.0.0.1"
        self._transport: Optional[asyncio.DatagramTransport] = None

    async def start(self, port: Optional[int] = None) -> None:
        if port is not None:
            self.port = port
        self.ipv4 = detect_lan_ipv4()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind(("", MDNS_PORT))
            mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setblocking(False)

            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _MdnsUdpProtocol(self.hostname, self.ipv4, self.port),
                sock=sock,
            )
            self._transport = transport  # type: ignore[assignment]
            # Broadcast initial unsolicited announcement
            announce = build_mdns_a_response(0, f"{self.hostname}.local.", self.ipv4)
            self._transport.sendto(announce, (MDNS_GROUP, MDNS_PORT))
            logger.info(
                "[MDNS] Advertising http://%s.local:%d -> %s:%d",
                self.hostname,
                self.port,
                self.ipv4,
                self.port,
            )
        except Exception as exc:
            logger.warning("[MDNS] Could not bind UDP :5353 mDNS responder (%s)", exc)

    async def stop(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None


showcase_mdns_responder = ShowcaseMdnsResponder()
