"""
Pure Application Rules for `_sync` (`FrontendSync`) Slot Isolation, Donor Style Inheritance,
Fallback Title Lookup, and Signature-Safe Transcoder Invocation (`app/application/sync_slot_rules.py`).

Depends strictly on `app.domain.*` and `DomainSourceChainService` to preserve Hexagonal boundaries.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from app.application.source_chain_service import DomainSourceChainService
from app.domain.board_identity import PER_ENGINE_DMD_ID_RE, canonical_dmd_board_key
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
)
from app.domain.ports.media_transcoder import IMediaTranscoderPort


def is_sync_dmd_id(dmd_id: str) -> bool:
    """Return True when `dmd_id` is a dedicated `FrontendSync` companion slot (`*_sync`)."""
    return str(dmd_id or "").strip().lower().endswith("_sync")


def strip_sync_suffix(dmd_id: str) -> str:
    """Return the base DMD identifier without a trailing `_sync` suffix."""
    clean = str(dmd_id or "").strip()
    return clean[:-5] if is_sync_dmd_id(clean) else clean


def resolve_donor_style_dmd_id(
    dmd_id: str,
    domain: DomainType,
    client_configs: Mapping[Tuple[str, DomainType], Any],
    client_udp_ips: Mapping[str, str],
) -> str:
    """
    Resolve a sibling rotation slot (`rpi_<hwid>_games_<WxH>` / `esp32_<hwid>_games_<WxH>`)
    strictly for read-only style/criteria inheritance by a `*_sync` client, without ever
    writing back to or hijacking that rotation slot.
    """
    clean = str(dmd_id or "").strip()
    if not clean:
        return ""
    base_id = strip_sync_suffix(clean)
    if (base_id, domain) in client_configs:
        return base_id

    match = PER_ENGINE_DMD_ID_RE.match(clean)
    if match:
        hwid = match.group("hwid")
        mid = f"_{hwid.lower()}" if hwid else ""
        sibling = f"{match.group('prefix').lower()}{mid}_{domain}_{match.group('geom').lower()}"
        if (sibling, domain) in client_configs:
            return sibling

    board_key = canonical_dmd_board_key(clean, client_udp_ips.get(clean, "") or client_udp_ips.get(base_id, ""))
    for (other_id, other_dom) in client_configs:
        if other_dom == domain and not is_sync_dmd_id(other_id):
            other_board = canonical_dmd_board_key(other_id, client_udp_ips.get(other_id, ""))
            if other_board == board_key:
                return other_id
    return base_id


def find_companion_sync_dmd_ids(
    board_dmd_id: str,
    candidate_queue_ids: Iterable[str],
    client_udp_ips: Mapping[str, str],
) -> List[str]:
    """
    Find all `_sync` queue IDs belonging to the same physical DMD board as `board_dmd_id`
    (used to abort orphaned conversions when a DMD board drops offline via MQTT LWT).
    """
    clean = str(board_dmd_id or "").strip()
    if not clean:
        return []
    if is_sync_dmd_id(clean):
        return [clean]

    direct_sync = f"{clean}_sync"
    board_key = canonical_dmd_board_key(clean, client_udp_ips.get(clean, ""))
    matched: List[str] = []
    for q_dmd_id in candidate_queue_ids:
        if not is_sync_dmd_id(q_dmd_id):
            continue
        if q_dmd_id == direct_sync:
            matched.append(q_dmd_id)
            continue
        q_board = canonical_dmd_board_key(
            q_dmd_id,
            client_udp_ips.get(q_dmd_id, "") or client_udp_ips.get(strip_sync_suffix(q_dmd_id), ""),
        )
        if q_board == board_key:
            matched.append(q_dmd_id)
    return matched


async def invoke_transcoder_for_dmd(
    transcoder: IMediaTranscoderPort,
    cand: MediaCandidate,
    criteria: FilterCriteria,
    geometry: PanelGeometry,
    out_dir: Path,
    dmd_id: str,
) -> Dict[str, object]:
    """
    Invoke `transcoder.transcode_candidate(...)`, forwarding `dmd_id` when supported by the
    implementation while remaining 100% compatible with 4-argument test doubles.
    """
    try:
        sig = inspect.signature(transcoder.transcode_candidate)
        accepts_dmd_id = "dmd_id" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        accepts_dmd_id = False

    if accepts_dmd_id:
        return await transcoder.transcode_candidate(  # type: ignore[call-arg]
            cand, criteria, geometry, out_dir, dmd_id=dmd_id
        )
    return await transcoder.transcode_candidate(cand, criteria, geometry, out_dir)


async def _lookup_single_query(
    chain_service: DomainSourceChainService,
    domain: DomainType,
    criteria: FilterCriteria,
    query: str,
) -> Optional[MediaCandidate]:
    clean_q = str(query or "").strip()
    if not clean_q:
        return None
    found = await chain_service.search_candidates(criteria=criteria, query=clean_q, limit=1)
    if found:
        return found[0]

    od_sig = inspect.signature(chain_service.search_on_demand)
    if "criteria" in od_sig.parameters:
        return await chain_service.search_on_demand(
            domain=domain,
            query=clean_q,
            lang=criteria.lang,
            criteria=criteria,
        )
    return await chain_service.search_on_demand(
        domain=domain,
        query=clean_q,
        lang=criteria.lang,
    )


async def search_candidate_with_fallback(
    chain_service: DomainSourceChainService,
    domain: DomainType,
    criteria: FilterCriteria,
    primary_query: str,
    fallback_query: str = "",
) -> Optional[MediaCandidate]:
    """
    Search for `primary_query` (e.g. ROM basename `mslug`) first; if no match is found and
    `fallback_query` (e.g. EmulationStation human title) is distinct and non-empty, retry with it.
    """
    cand = await _lookup_single_query(chain_service, domain, criteria, primary_query)
    if cand is not None:
        return cand

    clean_primary = str(primary_query or "").strip().lower()
    clean_fallback = str(fallback_query or "").strip()
    if clean_fallback and clean_fallback.lower() != clean_primary:
        return await _lookup_single_query(chain_service, domain, criteria, clean_fallback)
    return None
