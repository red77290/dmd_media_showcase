"""
Zero-dependency mDNS (`dmdmediashowcase.local`) responder for LAN auto-discovery.

Allows ESP32 (`ArcadeMatrix`), Raspberry Pi (`ArcadeMatrix_RPi`), and desktop/mobile
browsers on the same local network to reach the Showcase server out-of-the-box via
`http://dmdmediashowcase.local:8890` without manual IP lookup:
- Pure-Python RFC 6762 UDP responder (`A`, `PTR`/`SRV`, `AAAA` -> `NSEC` negative cache,
  `QU` unicast replies on `:5353`, explicit LAN NIC multicast binding, periodic refresh).
- Native macOS Bonjour integration (`/usr/bin/dns-sd -P`) when running directly on Darwin
  (`DMD Media Showcase.app` or local Python) so Safari/Chrome on the same Mac and LAN
  peers resolve `dmdmediashowcase.local` natively through Apple `mDNSResponder`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
import socket
import struct
import sys
from typing import Optional, Tuple

logger = logging.getLogger("dmd_showcase.mdns")

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
DEFAULT_MDNS_HOSTNAME = "dmdmediashowcase"
SERVICE_TYPE_FQDN = "_dmd-showcase._tcp.local."

DNS_TYPE_A = 1
DNS_TYPE_PTR = 12
DNS_TYPE_TXT = 16
DNS_TYPE_AAAA = 28
DNS_TYPE_SRV = 33
DNS_TYPE_NSEC = 47
DNS_TYPE_ANY = 255
DNS_CLASS_IN = 1
DNS_QU_FLAG = 0x8000


def _is_docker_bridge_ipv4(ip: str) -> bool:
    """Return True when `ip` looks like an internal Docker bridge IP (`172.16.0.0/12`) inside a container."""
    if not Path("/.dockerenv").exists():
        return False
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    first, second = int(parts[0]), int(parts[1])
    return first == 172 and 16 <= second <= 31


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


def build_mdns_nsec_ipv4_only_response(txid: int, fqdn: str, ipv4: str, ttl: int = 120) -> bytes:
    """
    Build an RFC 6762 Section 6.1 negative `NSEC` response for `AAAA` queries on an IPv4-only host,
    plus the IPv4 `A` record in Additional Records so dual-stack browsers never stall for 5s.
    """
    header = struct.pack("!HHHHHH", txid, 0x8400, 0, 1, 0, 1)
    qname = encode_dns_name(fqdn)
    # RFC 4034 type bit map: Window 0, length 1, bit 1 (`DNS_TYPE_A = 1` -> 0x80 >> 1 = 0x40)
    nsec_rdata = qname + bytes([0x00, 0x01, 0x40])
    nsec_rr = (
        qname
        + struct.pack("!HHIH", DNS_TYPE_NSEC, DNS_CLASS_IN | 0x8000, ttl, len(nsec_rdata))
        + nsec_rdata
    )
    a_rdata = socket.inet_aton(ipv4)
    a_rr = (
        qname
        + struct.pack("!HHIH", DNS_TYPE_A, DNS_CLASS_IN | 0x8000, ttl, len(a_rdata))
        + a_rdata
    )
    return header + nsec_rr + a_rr


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


def parse_mdns_query(
    packet: bytes,
    hostname: str = DEFAULT_MDNS_HOSTNAME,
    ipv4: str = "127.0.0.1",
    port: int = 8890,
) -> Tuple[Optional[bytes], bool]:
    """
    Inspect an incoming mDNS UDP datagram and return `(reply_packet, prefer_unicast)`.
    Scans all `qdcount` questions so multi-question `[AAAA, A]` packets always receive `A`.
    """
    if len(packet) < 12:
        return None, False
    txid, flags, qdcount, _ancount, _nscount, _arcount = struct.unpack("!HHHHHH", packet[:12])
    if (flags & 0x8000) != 0 or qdcount == 0:
        return None, False

    clean_host = hostname.lower().removesuffix(".local").strip(".")
    host_fqdn = f"{clean_host}.local."
    instance_fqdn = f"{clean_host}.{SERVICE_TYPE_FQDN}"

    want_a = False
    want_aaaa = False
    want_ptr = False
    prefer_unicast = False

    offset = 12
    for _ in range(min(qdcount, 32)):
        qname, offset = decode_dns_name(packet, offset)
        if offset + 4 > len(packet):
            break
        qtype, qclass = struct.unpack("!HH", packet[offset : offset + 4])
        offset += 4
        qu = (qclass & DNS_QU_FLAG) != 0

        if qname == host_fqdn:
            if qtype in (DNS_TYPE_A, DNS_TYPE_ANY):
                want_a = True
                prefer_unicast = prefer_unicast or qu
            elif qtype == DNS_TYPE_AAAA:
                want_aaaa = True
                prefer_unicast = prefer_unicast or qu
        elif qname == SERVICE_TYPE_FQDN and qtype in (DNS_TYPE_PTR, DNS_TYPE_ANY):
            want_ptr = True
            prefer_unicast = prefer_unicast or qu

    if want_a:
        return build_mdns_a_response(txid, host_fqdn, ipv4), prefer_unicast
    if want_ptr:
        return (
            build_mdns_ptr_response(
                txid, SERVICE_TYPE_FQDN, instance_fqdn, host_fqdn, ipv4, port
            ),
            prefer_unicast,
        )
    if want_aaaa:
        return build_mdns_nsec_ipv4_only_response(txid, host_fqdn, ipv4), prefer_unicast
    return None, False


def parse_and_reply_mdns(
    packet: bytes,
    hostname: str = DEFAULT_MDNS_HOSTNAME,
    ipv4: str = "127.0.0.1",
    port: int = 8890,
) -> Optional[bytes]:
    """Inspect an incoming mDNS UDP datagram and return a reply packet if matched."""
    reply, _prefer_unicast = parse_mdns_query(packet, hostname, ipv4, port)
    return reply


class _MdnsUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, hostname: str, ipv4: str, port: int) -> None:
        self.hostname = hostname
        self.ipv4 = ipv4
        self.port = port
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        reply, prefer_unicast = parse_mdns_query(data, self.hostname, self.ipv4, self.port)
        if reply and self.transport:
            with contextlib.suppress(Exception):
                self.transport.sendto(reply, (MDNS_GROUP, MDNS_PORT))
            if prefer_unicast or addr[1] != MDNS_PORT:
                with contextlib.suppress(Exception):
                    self.transport.sendto(reply, addr)


class ShowcaseMdnsResponder:
    """Lifecycle manager for the local network `dmdmediashowcase.local` responder."""

    def __init__(self, hostname: str = DEFAULT_MDNS_HOSTNAME, port: int = 8890) -> None:
        self.hostname = hostname
        self.port = port
        self.ipv4 = "127.0.0.1"
        self.is_nat_isolated = False
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._announce_task: Optional[asyncio.Task[None]] = None
        self._dnssd_proc: Optional[asyncio.subprocess.Process] = None

    def _clean_hostname(self) -> str:
        return self.hostname.lower().removesuffix(".local").strip(".") or DEFAULT_MDNS_HOSTNAME

    def _send_announcement(self) -> None:
        if self._transport is None:
            return
        clean = self._clean_hostname()
        host_fqdn = f"{clean}.local."
        instance_fqdn = f"{clean}.{SERVICE_TYPE_FQDN}"
        with contextlib.suppress(Exception):
            self._transport.sendto(
                build_mdns_a_response(0, host_fqdn, self.ipv4),
                (MDNS_GROUP, MDNS_PORT),
            )
            self._transport.sendto(
                build_mdns_ptr_response(
                    0, SERVICE_TYPE_FQDN, instance_fqdn, host_fqdn, self.ipv4, self.port
                ),
                (MDNS_GROUP, MDNS_PORT),
            )

    async def _announce_loop(self) -> None:
        for delay_s in (1.0, 3.0):
            await asyncio.sleep(delay_s)
            self._send_announcement()
        while True:
            await asyncio.sleep(60.0)
            self._send_announcement()

    async def _start_macos_bonjour_proxy(self) -> bool:
        """Register `<hostname>.local` with Apple `mDNSResponder` when running natively on macOS."""
        if (
            sys.platform != "darwin"
            or self.is_nat_isolated
            or self.ipv4.startswith("127.")
            or os.environ.get("DMD_DISABLE_DNSSD") == "1"
            or not Path("/usr/bin/dns-sd").exists()
        ):
            return False
        clean = self._clean_hostname()
        try:
            self._dnssd_proc = await asyncio.create_subprocess_exec(
                "/usr/bin/dns-sd",
                "-P",
                "DMD Media Showcase",
                "_dmd-showcase._tcp",
                "local",
                str(self.port),
                f"{clean}.local",
                self.ipv4,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return True
        except Exception as exc:
            logger.debug("[MDNS] Optional macOS dns-sd proxy unavailable: %s", exc)
            return False

    async def start(self, port: Optional[int] = None) -> None:
        await self.stop()
        if port is not None:
            self.port = port
        self.ipv4 = detect_lan_ipv4()
        self.is_nat_isolated = _is_docker_bridge_ipv4(self.ipv4)
        clean = self._clean_hostname()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", MDNS_PORT))

            # Prefer binding multicast explicitly to `self.ipv4` so VPN (`utun`) or bridge
            # interfaces never hijack LAN mDNS on multi-NIC hosts; fall back to `0.0.0.0`
            # when `HOST_LAN_IP` belongs to an outer Docker host interface.
            try:
                iface_bytes = socket.inet_aton(self.ipv4)
                mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), iface_bytes)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, iface_bytes)
            except OSError:
                self.is_nat_isolated = True
                mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton("0.0.0.0"))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

            sock.setblocking(False)

            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _MdnsUdpProtocol(clean, self.ipv4, self.port),
                sock=sock,
            )
            self._transport = transport  # type: ignore[assignment]
            self._send_announcement()
            self._announce_task = asyncio.create_task(self._announce_loop())

            bonjour_ok = await self._start_macos_bonjour_proxy()
            if self.is_nat_isolated:
                logger.info(
                    "[MDNS] Container UDP :5353 bound (target=%s:%d), but Docker bridge/VM NAT "
                    "isolates Layer-2 mDNS multicast (224.0.0.251). Use 'network_mode: host' on "
                    "Linux or './scripts/register_macos_mdns.sh' / 'DMD Media Showcase.app' on "
                    "macOS for browser 'http://%s.local:%d' (ArcadeMatrix ESP32 auto-discovers via HTTP/MQTT).",
                    self.ipv4,
                    self.port,
                    clean,
                    self.port,
                )
            else:
                logger.info(
                    "[MDNS] Advertising http://%s.local:%d -> %s:%d%s",
                    clean,
                    self.port,
                    self.ipv4,
                    self.port,
                    " (macOS Bonjour dns-sd active)" if bonjour_ok else "",
                )
        except Exception as exc:
            logger.warning("[MDNS] Could not bind UDP :5353 mDNS responder (%s)", exc)

    async def stop(self) -> None:
        if self._announce_task is not None:
            self._announce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._announce_task
            self._announce_task = None

        if self._dnssd_proc is not None:
            with contextlib.suppress(ProcessLookupError, Exception):
                self._dnssd_proc.terminate()
                await asyncio.wait_for(self._dnssd_proc.wait(), timeout=1.5)
            self._dnssd_proc = None

        if self._transport is not None:
            with contextlib.suppress(Exception):
                self._transport.close()
            self._transport = None


showcase_mdns_responder = ShowcaseMdnsResponder()
