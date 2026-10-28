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

PER_ENGINE_DMD_ID_RE = re.compile(
    r"^(?P<prefix>rpi|esp32|http)(?:_(?P<hwid>[0-9a-z]{4,12}))?_(?P<domain>games|movies|series|anime)_(?P<geom>\d+x\d+)(?:_(?P<sync>sync))?(?:_\d+)?$",
    re.IGNORECASE,
)
_PER_ENGINE_DMD_ID_RE = PER_ENGINE_DMD_ID_RE


def strip_mqtt_gen_suffix(client_id: str) -> str:
    """Normalize an MQTT client ID (e.g. `esp32_a4cf12_games_128x32_3` or `rpi_games_128x32_3`) back to its logical `dmd_id`."""
    raw = str(client_id or "").strip()
    match = PER_ENGINE_DMD_ID_RE.match(raw)
    if match:
        hwid = match.group("hwid")
        mid = f"_{hwid.lower()}" if hwid else ""
        sync = "_sync" if match.group("sync") else ""
        return f"{match.group('prefix').lower()}{mid}_{match.group('domain').lower()}_{match.group('geom').lower()}{sync}"
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
    - Hardware-scoped IDs (`esp32_a4cf12_games_128x32`, `rpi_9b03e1_movies_128x32`) carry a
      deterministic per-board `<hwid>` so two boards of identical platform & resolution never
      collide even behind Docker NAT (`172.18.0.1`), while all 4 domains + `_sync` on the same
      board share `board:{prefix}_{hwid}`.
    - Legacy IDs without `<hwid>` fall back to physical LAN `client_ip` or `board:{prefix}_{WxH}`.
    """
    raw = str(dmd_id or "").strip()
    match = PER_ENGINE_DMD_ID_RE.match(raw)
    if match and match.group("hwid"):
        return f"board:{match.group('prefix').lower()}_{match.group('hwid').lower()}"
    if is_usable_physical_lan_ip(client_ip):
        return f"ip:{client_ip.strip()}"
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
