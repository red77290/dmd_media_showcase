"""
Domain Value Object & Pure Identity Rules for Physical DMD Matrix Boards (`app/domain/board_identity.py`).

Encapsulates:
- MQTT reconnect generation suffix stripping (`rpi_games_128x32_3` -> `rpi_games_128x32`)
- Physical LAN IP vs Docker NAT / loopback classification
- Canonical physical board deduplication (`canonical_dmd_board_key`) across multi-engine
  `ArcadeMatrix` (ESP32) and `ArcadeMatrix_RPi` boards
- UDP LAN destination IP selection (`select_best_udp_ip`)
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

_PER_ENGINE_DMD_ID_RE = re.compile(
    r"^(?P<prefix>rpi|esp32|http)_(?P<domain>games|movies|series|anime)_(?P<geom>\d+x\d+)(?:_\d+)?$",
    re.IGNORECASE,
)


def strip_mqtt_gen_suffix(client_id: str) -> str:
    """Normalize an MQTT client ID (e.g. `rpi_games_128x32_3`) back to its logical `dmd_id`."""
    raw = str(client_id or "").strip()
    match = _PER_ENGINE_DMD_ID_RE.match(raw)
    if match:
        return f"{match.group('prefix').lower()}_{match.group('domain').lower()}_{match.group('geom').lower()}"
    return raw


def is_usable_physical_lan_ip(ip: str) -> bool:
    """
    Return True when `ip` identifies a physical DMD on the LAN, excluding loopback,
    wildcard, test placeholders, and Docker bridge `.1` NAT gateways (e.g. `172.18.0.1`).
    """
    val = str(ip or "").strip().lower()
    if not val or val in (
        "0.0.0.0",
        "127.0.0.1",
        "::1",
        "localhost",
        "unknown",
        "auto",
        "testclient",
    ):
        return False
    if val.startswith("127."):
        return False
    parts = val.split(".")
    if len(parts) == 4 and parts[0] == "172" and parts[3] == "1":
        try:
            second = int(parts[1])
            if 16 <= second <= 31:
                return False
        except ValueError:
            pass
    return True


def canonical_dmd_board_key(dmd_id: str, client_ip: str = "") -> str:
    """
    Compute a stable deduplication key representing one physical DMD matrix board:
    - Both `ArcadeMatrix` (ESP32) and `ArcadeMatrix_RPi` instantiate up to 4 per-domain
      runners (`rpi_games_128x32`, `rpi_movies_128x32`, ...) on the same physical board.
    - Grouping by real LAN `client_ip` (when non-loopback / non-Docker-NAT) or by
      `{platform}_{WxH}` ensures 1 physical board with 4 Showcase engines is counted as
      1 DMD client with 4 domain slots rather than 4 separate boards.
    """
    if is_usable_physical_lan_ip(client_ip):
        return f"ip:{client_ip.strip()}"
    raw = str(dmd_id or "").strip()
    match = _PER_ENGINE_DMD_ID_RE.match(raw)
    if match:
        return f"board:{match.group('prefix').lower()}_{match.group('geom').lower()}"
    return f"id:{raw or 'default_dmd'}"


def select_best_udp_ip(
    declared_ip: Optional[str],
    cached_ip: Optional[str],
    peer_ip: Optional[str],
) -> str:
    """
    Resolve the real LAN IP of the DMD client for UDP `DMDF` streaming:
    Always prefer the explicit `client_ip` / `udp_ip` declared by the DMD inside its payload
    so Docker bridge NAT gateways (`172.17.0.1`, `172.18.0.1`) are bypassed.
    """
    for candidate in (declared_ip, cached_ip, peer_ip):
        ip = str(candidate or "").strip()
        if ip and ip not in ("0.0.0.0", "auto", "unknown"):
            return ip
    return "127.0.0.1"


@dataclass(frozen=True)
class DmdBoardIdentity:
    """Immutable Value Object representing a normalized DMD client & physical board identity."""

    raw_id: str
    logical_id: str
    board_key: str
    udp_ip: str
    platform_prefix: str = ""

    @classmethod
    def from_raw(
        cls,
        raw_id: str,
        declared_ip: Optional[str] = None,
        cached_ip: Optional[str] = None,
        peer_ip: Optional[str] = None,
    ) -> "DmdBoardIdentity":
        logical = strip_mqtt_gen_suffix(raw_id) or "default_dmd"
        best_ip = select_best_udp_ip(declared_ip, cached_ip, peer_ip)
        match = _PER_ENGINE_DMD_ID_RE.match(logical)
        prefix = match.group("prefix").lower() if match else ""
        return cls(
            raw_id=str(raw_id or ""),
            logical_id=logical,
            board_key=canonical_dmd_board_key(logical, best_ip),
            udp_ip=best_ip,
            platform_prefix=prefix,
        )
