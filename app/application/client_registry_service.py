"""
Application Service: `ClientRegistryService` (`app/application/client_registry_service.py`).

Manages DMD client presence, physical board deduplication (`DmdBoardIdentity`), HTTP/MQTT
telemetry inspection, read-only Mode 1 live profile synthesis, and `hello` handshakes.
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from app.application.concurrency import AsyncTaskSupervisor
from app.application.criteria_parser import parse_domain_criteria
from app.application.showcase_state import ShowcaseRuntimeState
from app.config import settings
from app.domain.board_identity import (
    PER_ENGINE_DMD_ID_RE,
    canonical_dmd_board_key,
    select_best_udp_ip,
)
from app.domain.entities import DomainType, FilterCriteria, PanelGeometry
from app.domain.ports import IDmdProfileRepository, IFrameStreamerPort
from app.domain.style_specs import (
    VALID_DOMAINS,
    normalize_dmd_profile_spec,
    normalize_domain_studio_style,
)

_PER_ENGINE_DMD_ID_RE = PER_ENGINE_DMD_ID_RE


class ClientRegistryService:
    """Tracks connected DMD boards, Mode 1 live inspector snapshots, and `hello` announcements."""

    def __init__(
        self,
        state: ShowcaseRuntimeState,
        profile_store: IDmdProfileRepository,
        streamer: IFrameStreamerPort,
    ) -> None:
        self.state = state
        self.profile_store = profile_store
        self.streamer = streamer

    def distinct_board_keys(self) -> Set[str]:
        keys = {
            canonical_dmd_board_key(dmd_id, self.state.client_udp_ips.get(dmd_id, ""))
            for (dmd_id, _dom) in self.state.client_configs
        }
        for prof in self.profile_store.list_profiles():
            if prof.get("config_source") == "server" and (
                self.state.client_online.get(prof["dmd_id"], False)
                or prof.get("always_stream_udp", False)
            ):
                keys.add(
                    canonical_dmd_board_key(
                        prof["dmd_id"],
                        prof.get("udp_ip") or self.state.client_udp_ips.get(prof["dmd_id"], ""),
                    )
                )
        return keys

    def active_broadcasts_count(self) -> int:
        now = time.time()
        playing_boards: Set[str] = set()
        for pkg in self.state.active_packages.values():
            if pkg.consumed:
                playing_boards.add(
                    canonical_dmd_board_key(
                        pkg.dmd_id, self.state.client_udp_ips.get(pkg.dmd_id, "")
                    )
                )
        for (dmd_id, _dom), until_ts in list(self.state.last_claimed_until.items()):
            if now < until_ts:
                playing_boards.add(
                    canonical_dmd_board_key(dmd_id, self.state.client_udp_ips.get(dmd_id, ""))
                )
        udp_cnt = getattr(self.streamer, "active_streams_count", 0)
        return max(udp_cnt, len(playing_boards))

    def record_http_client(
        self,
        dmd_id: str,
        domain: DomainType,
        client_ip: Optional[str] = None,
    ) -> None:
        key = (dmd_id, domain)
        self.state.client_transports.setdefault(key, "HTTP")
        self.state.client_last_seen[key] = time.time()
        ip = str(client_ip or "").strip()
        if ip and ip not in ("0.0.0.0", "unknown", "testclient"):
            self.state.client_udp_ips.setdefault(dmd_id, ip)

    def resolve_target_slot_dmd_id(self, dmd_id: str, domain: DomainType) -> str:
        """
        For Mode 1 multi-engine boards (e.g. `esp32_games_128x32` + `esp32_movies_128x32`),
        map `(dmd_id, domain)` to the matching per-domain runner ID when registered.
        """
        if (
            (dmd_id, domain) in self.state.client_configs
            or dmd_id in self.state.server_managed_ids
        ):
            return dmd_id
        m = _PER_ENGINE_DMD_ID_RE.match(dmd_id)
        if m:
            hwid = m.group("hwid")
            mid = f"_{hwid.lower()}" if hwid else ""
            sibling = f"{m.group('prefix').lower()}{mid}_{domain}_{m.group('geom').lower()}"
            if (sibling, domain) in self.state.client_configs:
                return sibling
        board_key = canonical_dmd_board_key(dmd_id, self.state.client_udp_ips.get(dmd_id, ""))
        for (other_id, other_dom) in self.state.client_configs:
            if other_dom == domain:
                other_board = canonical_dmd_board_key(
                    other_id, self.state.client_udp_ips.get(other_id, "")
                )
                if other_board == board_key:
                    return other_id
        return dmd_id

    def describe_connected_clients(
        self,
        queue_length_fn: Callable[[str], int],
    ) -> List[Dict[str, Any]]:
        """Return a summary of registered DMD clients, persisted server profiles, and queue slots."""
        out: List[Dict[str, Any]] = []
        now = time.time()
        profiles_by_id = {p["dmd_id"]: p for p in self.profile_store.list_profiles()}
        seen_dmd_ids: Set[str] = set()

        for (dmd_id, domain), (geom, crit) in sorted(self.state.client_configs.items()):
            seen_dmd_ids.add(dmd_id)
            key = (dmd_id, domain)
            prof = profiles_by_id.get(dmd_id)
            ready_pkg = self.state.ready_packages.get(key)
            if ready_pkg is None and dmd_id in self.state.director_prefetched:
                pf = self.state.director_prefetched[dmd_id]
                if pf.domain == domain:
                    ready_pkg = pf
            standby_pkg = self.state.standby_packages.get(key)
            prep_task = self.state.prep_tasks.get(key) or self.state.director_prefetch_tasks.get(
                dmd_id
            )
            consumed_pkg = next(
                (
                    p
                    for p in self.state.active_packages.values()
                    if p.dmd_id == dmd_id and p.domain == domain and p.consumed
                ),
                None,
            )
            stream_meta = (
                self.streamer.get_active_stream(dmd_id)
                if hasattr(self.streamer, "get_active_stream")
                else None
            )
            is_domain_streaming = bool(
                stream_meta
                and (
                    consumed_pkg is None
                    or stream_meta.get("item_id") == consumed_pkg.item_id
                )
            )
            is_playing_now = bool(
                is_domain_streaming
                or consumed_pkg is not None
                or now < self.state.last_claimed_until.get(key, 0.0)
            )
            last_title = self.state.last_played_titles.get(key)
            last_source = self.state.last_played_sources.get(key, "")
            playing_title = (
                consumed_pkg.card.title
                if consumed_pkg is not None
                else (last_title if is_playing_now else None)
            )
            playing_source = (
                (consumed_pkg.card.source_used or consumed_pkg.candidate.source_id or "")
                if consumed_pkg is not None
                else (last_source if is_playing_now else "")
            )
            ready_source = (
                (ready_pkg.card.source_used or ready_pkg.candidate.source_id or "")
                if ready_pkg is not None
                else ""
            )
            standby_source = (
                (standby_pkg.card.source_used or standby_pkg.candidate.source_id or "")
                if standby_pkg is not None
                else ""
            )
            preparing_source = self.state.preparing_sources.get(key, "")
            if not preparing_source:
                for q_entry in self.state.priority_queues.get(dmd_id) or ():
                    if (
                        q_entry.domain == domain
                        and q_entry.status == "preparing"
                        and q_entry.candidate.id != "pending"
                        and q_entry.candidate.source_id
                    ):
                        preparing_source = q_entry.candidate.source_id
                        break
            last_seen = self.state.client_last_seen.get(key)
            q_len = queue_length_fn(dmd_id)
            cfg_source = str(
                prof.get("config_source")
                if prof
                else ("server" if dmd_id in self.state.server_managed_ids else "client")
            )
            supports_local_card = (
                bool(prof.get("supports_local_card", True))
                if prof
                else (not crit.render_card_in_stream)
            )
            out.append(
                {
                    "dmd_id": dmd_id,
                    "display_name": str(prof.get("display_name") or dmd_id) if prof else dmd_id,
                    "domain": domain,
                    "config_source": cfg_source,
                    "supports_local_card": supports_local_card,
                    "always_stream_udp": bool(prof.get("always_stream_udp", False))
                    if prof
                    else False,
                    "paused": bool(prof.get("paused", False)) if prof else False,
                    "online": self.state.client_online.get(dmd_id, True),
                    "transport": self.state.client_transports.get(
                        key,
                        "UDP-PASSIVE"
                        if (cfg_source == "server" and not supports_local_card)
                        else "MQTT+TCP",
                    ),
                    "client_ip": self.state.client_udp_ips.get(dmd_id)
                    or (str(prof.get("udp_ip") or "") if prof else ""),
                    "udp_port": self.state.client_udp_ports.get(
                        dmd_id, int(prof.get("udp_port") or 4210) if prof else 4210
                    ),
                    "width": geom.width,
                    "height": geom.height,
                    "lang": str(prof.get("lang") or crit.lang) if prof else crit.lang,
                    "video_mode": crit.video_mode,
                    "color_boost": crit.color_boost,
                    "video_max_duration_s": crit.video_max_duration_s,
                    "video_fps": crit.video_fps,
                    "systems": list(crit.systems),
                    "genres": list(crit.genres),
                    "decades": list(crit.decades),
                    "active_title": playing_title or last_title,
                    "playing_title": playing_title,
                    "playing_source": playing_source,
                    "last_played_title": last_title,
                    "last_played_source": last_source,
                    "is_playing": is_playing_now,
                    "udp_streaming": is_domain_streaming,
                    "ready_title": ready_pkg.card.title if ready_pkg else None,
                    "ready_source": ready_source,
                    "standby_title": standby_pkg.card.title if standby_pkg else None,
                    "standby_source": standby_source,
                    "preparing": AsyncTaskSupervisor.is_task_live(prep_task),
                    "preparing_source": preparing_source,
                    "queue_length": q_len,
                    "last_seen_ago_s": int(max(0.0, now - last_seen)) if last_seen else None,
                }
            )

        for dmd_id, prof in sorted(profiles_by_id.items()):
            if dmd_id in seen_dmd_ids:
                continue
            enabled_slots = [s for s in (prof.get("playlist") or []) if s.get("enabled", True)]
            first_slot = (
                enabled_slots[0] if enabled_slots else ((prof.get("playlist") or [{}])[0])
            )
            dom = str(first_slot.get("domain") or "games")
            pf_pkg = self.state.director_prefetched.get(dmd_id)
            out.append(
                {
                    "dmd_id": dmd_id,
                    "display_name": str(prof.get("display_name") or dmd_id),
                    "domain": dom,
                    "config_source": str(prof.get("config_source") or "server"),
                    "supports_local_card": bool(prof.get("supports_local_card", True)),
                    "always_stream_udp": bool(prof.get("always_stream_udp", False)),
                    "paused": bool(prof.get("paused", False)),
                    "online": self.state.client_online.get(dmd_id, False),
                    "transport": "UDP-PASSIVE"
                    if not prof.get("supports_local_card", True)
                    else "MQTT+TCP",
                    "client_ip": self.state.client_udp_ips.get(dmd_id)
                    or str(prof.get("udp_ip") or ""),
                    "udp_port": int(prof.get("udp_port") or 4210),
                    "width": int(prof.get("width") or 128),
                    "height": int(prof.get("height") or 32),
                    "lang": str(prof.get("lang") or "fr"),
                    "video_mode": str(first_slot.get("video_mode") or "auto_action"),
                    "color_boost": first_slot.get("color_boost") or "auto",
                    "video_max_duration_s": float(first_slot.get("video_max_duration_s") or 8.0),
                    "video_fps": int(first_slot.get("video_fps") or 15),
                    "systems": list(first_slot.get("systems") or []),
                    "genres": list(first_slot.get("genres") or []),
                    "decades": list(first_slot.get("decades") or []),
                    "active_title": None,
                    "playing_title": None,
                    "playing_source": "",
                    "last_played_title": None,
                    "last_played_source": "",
                    "is_playing": False,
                    "udp_streaming": False,
                    "ready_title": pf_pkg.card.title if pf_pkg else None,
                    "ready_source": (pf_pkg.card.source_used or pf_pkg.candidate.source_id or "")
                    if pf_pkg
                    else "",
                    "standby_title": None,
                    "standby_source": "",
                    "preparing": AsyncTaskSupervisor.is_task_live(
                        self.state.director_prefetch_tasks.get(dmd_id)
                    ),
                    "preparing_source": self.state.preparing_sources.get((dmd_id, dom), ""),  # type: ignore[arg-type]
                    "queue_length": queue_length_fn(dmd_id),
                    "last_seen_ago_s": None,
                }
            )
        return out

    def is_live_client_managed_dmd(
        self,
        dmd_id: str,
        extra_http_sessions: Optional[List[Tuple[Any, str]]] = None,
    ) -> bool:
        """Return True when `dmd_id` is active in Mode 1 and not overridden by a `server` profile."""
        clean_id = str(dmd_id or "").strip()
        if not clean_id or clean_id in self.state.server_managed_ids:
            return False
        prof = self.profile_store.get_profile(clean_id)
        if prof is not None and prof.get("config_source") == "server":
            return False
        if any(cid == clean_id for (cid, _dom) in self.state.client_configs):
            return True
        for sess, _ip in extra_http_sessions or []:
            cid = str(
                getattr(sess.request, "client_id", None) or getattr(sess, "session_id", "")
            ).strip()
            if cid and cid != "webui_tester" and cid == clean_id:
                return True
        return False

    def synthesize_live_client_profile(
        self,
        dmd_id: str,
        extra_http_sessions: Optional[List[Tuple[Any, str]]] = None,
        parse_criteria_fn: Callable[[DomainType, Dict[str, Any], str], FilterCriteria] = parse_domain_criteria,
    ) -> Optional[Dict[str, Any]]:
        """Build a read-only live inspector profile consolidating sibling Mode 1 domain runners."""
        clean_id = str(dmd_id or "").strip()
        if not clean_id:
            return None

        profiles_by_id = {p["dmd_id"]: p for p in self.profile_store.list_profiles()}
        all_entries: List[
            Tuple[
                str,
                DomainType,
                PanelGeometry,
                FilterCriteria,
                str,
                str,
                Optional[Dict[str, Any]],
            ]
        ] = []

        for (cid, dom), (geom, crit) in sorted(self.state.client_configs.items()):
            if cid in self.state.server_managed_ids:
                continue
            p_existing = profiles_by_id.get(cid)
            if p_existing is not None and p_existing.get("config_source") == "server":
                continue
            ip = self.state.client_udp_ips.get(cid, "")
            tr = self.state.client_transports.get((cid, dom), "MQTT+TCP")
            raw_p = self.state.client_raw_configs.get(cid)
            all_entries.append((cid, dom, geom, crit, tr, ip, raw_p))

        existing_pairs = {(cid, dom) for (cid, dom, *_rest) in all_entries}
        for sess, sess_ip in extra_http_sessions or []:
            cid = str(
                getattr(sess.request, "client_id", None) or getattr(sess, "session_id", "")
            ).strip()
            dom_raw = str(getattr(sess, "domain", "games"))
            if not cid or cid == "webui_tester" or dom_raw not in VALID_DOMAINS:
                continue
            dom_http: DomainType = dom_raw  # type: ignore[assignment]
            if (cid, dom_http) in existing_pairs:
                continue
            p_existing = profiles_by_id.get(cid)
            if p_existing is not None and p_existing.get("config_source") == "server":
                continue
            clean_ip = sess_ip if sess_ip and sess_ip not in ("unknown", "testclient") else ""
            geom = PanelGeometry(
                width=int(sess.request.width), height=int(sess.request.height)
            )
            raw_req = sess.request.model_dump() if hasattr(sess.request, "model_dump") else {}
            crit = parse_criteria_fn(
                dom_http, raw_req, str(getattr(sess.request, "lang", "fr"))
            )
            all_entries.append((cid, dom_http, geom, crit, "HTTP", clean_ip, raw_req))

        direct_matches = [e for e in all_entries if e[0] == clean_id]
        if not direct_matches:
            return None

        _prim_cid, _prim_dom, prim_geom, prim_crit, prim_tr, prim_ip, _prim_raw = direct_matches[0]
        clicked_ip = prim_ip or self.state.client_udp_ips.get(clean_id, "")
        board_key = canonical_dmd_board_key(clean_id, clicked_ip)

        domain_map: Dict[
            DomainType,
            Tuple[str, PanelGeometry, FilterCriteria, str, str, Optional[Dict[str, Any]]],
        ] = {}
        for cid, dom, geom, crit, tr, ip, raw_p in all_entries:
            if cid != clean_id and canonical_dmd_board_key(cid, ip) == board_key:
                domain_map[dom] = (cid, geom, crit, tr, ip, raw_p)
        for cid, dom, geom, crit, tr, ip, raw_p in direct_matches:
            domain_map[dom] = (cid, geom, crit, tr, ip, raw_p)

        udp_ip = clicked_ip or next((v[4] for v in domain_map.values() if v[4]), "")
        default_port = 5568 if clean_id.lower().startswith("rpi_") else 4210
        udp_port = int(self.state.client_udp_ports.get(clean_id, default_port))

        playlist_raw: List[Dict[str, Any]] = []
        domain_styles_raw: Dict[str, Dict[str, Any]] = {}
        active_engines: Dict[str, Dict[str, Any]] = {}
        received_configs: Dict[str, Any] = {}

        for dom in VALID_DOMAINS:
            if dom in domain_map:
                sib_id, sib_geom, sib_crit, sib_tr, sib_ip, sib_raw = domain_map[dom]
                sib_port = int(
                    self.state.client_udp_ports.get(
                        sib_id, 5568 if sib_id.lower().startswith("rpi_") else udp_port
                    )
                )
                active_engines[dom] = {
                    "dmd_id": sib_id,
                    "transport": sib_tr,
                    "width": sib_geom.width,
                    "height": sib_geom.height,
                    "lang": sib_crit.lang,
                    "udp_port": sib_port,
                }
                if sib_id not in received_configs:
                    if isinstance(sib_raw, dict) and sib_raw:
                        received_configs[sib_id] = copy.deepcopy(sib_raw)
                    else:
                        received_configs[sib_id] = {
                            "dmd_id": sib_id,
                            "domain": dom,
                            "transport": sib_tr,
                            "client_ip": sib_ip or udp_ip,
                            "udp_port": sib_port,
                            "width": sib_geom.width,
                            "height": sib_geom.height,
                            "lang": sib_crit.lang,
                            "systems": list(sib_crit.systems) if dom == "games" else [],
                            "genres": list(sib_crit.genres),
                            "decades": list(sib_crit.decades),
                            "min_rating": sib_crit.min_rating,
                            "video_type": sib_crit.video_type,
                            "show_poster": sib_crit.include_poster,
                            "show_synopsis": sib_crit.include_synopsis,
                            "text_scroll_speed": sib_crit.text_scroll_speed,
                            "jacket_panorama": sib_crit.jacket_panorama,
                            "marquee_panorama": sib_crit.marquee_panorama,
                            "snapshot_panorama_count": sib_crit.snapshot_panorama_count,
                            "panorama_duration_s": sib_crit.panorama_duration_s,
                            "show_video": sib_crit.include_video,
                            "video_max_duration_s": sib_crit.video_max_duration_s,
                            "video_fps": sib_crit.video_fps,
                            "video_mode": sib_crit.video_mode,
                            "color_boost": sib_crit.color_boost,
                        }

                style_dict = normalize_domain_studio_style(
                    dom,
                    {
                        "include_card_intro": bool(sib_crit.include_card_intro),
                        "card_layout": str(sib_crit.card_layout),
                        "card_theme": str(sib_crit.card_theme),
                        "card_font": str(sib_crit.card_font),
                        "card_accent_color": str(sib_crit.card_accent_color),
                        "include_poster": bool(sib_crit.include_poster),
                        "show_badge": bool(sib_crit.show_badge),
                        "show_year": bool(sib_crit.show_year),
                        "show_rating": bool(sib_crit.show_rating),
                        "include_synopsis": bool(sib_crit.include_synopsis),
                        "text_scroll_speed": str(sib_crit.text_scroll_speed),
                        "card_hold_s": float(sib_crit.card_hold_s),
                        "jacket_panorama": bool(sib_crit.jacket_panorama),
                        "marquee_panorama": bool(sib_crit.marquee_panorama),
                        "snapshot_panorama_count": int(sib_crit.snapshot_panorama_count),
                        "panorama_duration_s": float(sib_crit.panorama_duration_s),
                        "include_video": bool(sib_crit.include_video),
                        "video_type": str(sib_crit.video_type or "any"),
                        "video_max_duration_s": float(sib_crit.video_max_duration_s),
                        "video_fps": int(sib_crit.video_fps),
                        "video_mode": str(sib_crit.video_mode),
                        "color_boost": str(sib_crit.color_boost),
                    },
                    default_panorama_duration_s=float(settings.panorama_duration_s),
                    default_video_mode=str(settings.default_video_mode),
                )
                domain_styles_raw[dom] = style_dict
                playlist_raw.append(
                    {
                        "enabled": True,
                        "domain": dom,
                        "count": 1,
                        "lang": sib_crit.lang,
                        "systems": list(sib_crit.systems) if dom == "games" else [],
                        "genres": list(sib_crit.genres),
                        "decades": list(sib_crit.decades),
                        "min_rating": float(sib_crit.min_rating),
                        "video_type": str(sib_crit.video_type or "any"),
                        "include_poster": bool(sib_crit.include_poster),
                        "include_synopsis": bool(sib_crit.include_synopsis),
                        "text_scroll_speed": str(sib_crit.text_scroll_speed),
                        "jacket_panorama": bool(sib_crit.jacket_panorama),
                        "marquee_panorama": bool(sib_crit.marquee_panorama),
                        "snapshot_panorama_count": int(sib_crit.snapshot_panorama_count),
                        "panorama_duration_s": float(sib_crit.panorama_duration_s),
                        "include_video": bool(sib_crit.include_video),
                        "video_max_duration_s": float(sib_crit.video_max_duration_s),
                        "video_fps": int(sib_crit.video_fps),
                        "video_mode": str(sib_crit.video_mode),
                        "color_boost": str(sib_crit.color_boost),
                    }
                )
            else:
                domain_styles_raw[dom] = normalize_domain_studio_style(
                    dom,
                    {},
                    default_panorama_duration_s=float(settings.panorama_duration_s),
                    default_video_mode=str(settings.default_video_mode),
                )
                playlist_raw.append(
                    {
                        "enabled": False,
                        "domain": dom,
                        "count": 1,
                        "lang": prim_crit.lang,
                        "systems": [],
                        "genres": [],
                        "decades": [],
                        "min_rating": 6.0,
                        "video_type": "any",
                        "include_poster": False,
                        "include_synopsis": False,
                        "text_scroll_speed": "normal",
                        "jacket_panorama": False,
                        "marquee_panorama": False,
                        "snapshot_panorama_count": 0,
                        "panorama_duration_s": float(settings.panorama_duration_s),
                        "include_video": False,
                        "video_max_duration_s": 8.0,
                        "video_fps": 15,
                        "video_mode": "auto_action",
                        "color_boost": "auto",
                    }
                )

        profile = normalize_dmd_profile_spec(
            clean_id,
            {
                "dmd_id": clean_id,
                "display_name": clean_id,
                "config_source": "client",
                "supports_local_card": not bool(prim_crit.render_card_in_stream),
                "always_stream_udp": False,
                "width": prim_geom.width,
                "height": prim_geom.height,
                "udp_ip": udp_ip,
                "udp_port": udp_port,
                "lang": prim_crit.lang,
                "inter_item_gap_s": 0.0,
                "domain_styles": domain_styles_raw,
                "playlist": playlist_raw,
            },
            default_lang=str(settings.default_lang),
            default_panorama_duration_s=float(settings.panorama_duration_s),
            default_video_mode=str(settings.default_video_mode),
            default_color_boost=str(settings.default_color_boost),
        )
        for slot in profile.get("playlist") or []:
            dom_k = slot.get("domain")
            if dom_k in domain_map:
                slot["engine_dmd_id"] = domain_map[dom_k][0]  # type: ignore[index]
                slot["transport"] = domain_map[dom_k][3]  # type: ignore[index]
            else:
                slot["engine_dmd_id"] = None
                slot["transport"] = None

        client_live_payload = {
            "inspected_dmd_id": clean_id,
            "board_key": board_key,
            "config_source": "client",
            "read_only": True,
            "transport": prim_tr,
            "client_ip": udp_ip,
            "udp_port": udp_port,
            "active_engines": active_engines,
            "received_configs": received_configs,
        }
        return {
            "persisted": False,
            "read_only_client_managed": True,
            "profile": profile,
            "client_live_payload": client_live_payload,
        }

    async def handle_client_hello(
        self,
        dmd_id: str,
        payload: Dict[str, Any],
        detected_ip: str = "",
        on_server_profile_ready: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Handle passive/hybrid DMD `hello` announcement over MQTT or HTTP."""
        declared_ip = str(payload.get("udp_ip") or payload.get("client_ip") or "").strip()
        peer_ip = str(payload.get("_peer_ip") or detected_ip or "").strip()
        best_ip = select_best_udp_ip(
            declared_ip, self.state.client_udp_ips.get(dmd_id), peer_ip
        )
        if best_ip and best_ip not in ("127.0.0.1", "testclient"):
            self.state.client_udp_ips[dmd_id] = best_ip
        elif declared_ip:
            self.state.client_udp_ips[dmd_id] = declared_ip

        if payload.get("udp_port"):
            self.state.client_udp_ports[dmd_id] = max(1, min(65535, int(payload["udp_port"])))
        self.state.client_online[dmd_id] = True

        prof = self.profile_store.ensure_from_hello(dmd_id, payload, detected_ip=best_ip)
        now_ts = time.time()
        for slot in prof.get("playlist") or []:
            if slot.get("enabled", True) and slot.get("domain") in VALID_DOMAINS:
                self.state.client_last_seen[(dmd_id, slot["domain"])] = now_ts  # type: ignore[index]

        if prof.get("config_source") == "server" and on_server_profile_ready is not None:
            await on_server_profile_ready(dmd_id)
        return prof
