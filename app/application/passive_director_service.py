"""
Application Service: `PassivePlaylistDirectorService` (`app/application/passive_director_service.py`).

Drives Server-Managed DMD profiles (`config_source='server'`):
- Mode 2A (`supports_local_card=True`): Hybrid 60 FPS local Info Card over MQTT + UDP `DMDF` video.
- Mode 2B (`supports_local_card=False`): 100% Passive UDP framebuffer receiver (Card + Panoramas + Video).
- Live client playback controls (`skip`, `pause`, `resume`, `stop`).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from app.application.concurrency import AsyncTaskSupervisor
from app.application.criteria_parser import parse_domain_criteria
from app.application.showcase_state import QueuedOnDemandEntry, ShowcaseRuntimeState
from app.application.source_chain_service import DomainSourceChainService
from app.domain.board_identity import select_best_udp_ip
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    PanelGeometry,
    PreparedShowcasePackage,
)
from app.domain.ports import (
    IDmdProfileRepository,
    IFrameStreamerPort,
    IMediaTranscoderPort,
    IMqttControlGatewayPort,
    ISeenHistoryRepository,
)
from app.domain.style_specs import VALID_DOMAINS

logger = logging.getLogger("dmd_showcase.application.director")


class PassivePlaylistDirectorService:
    """Orchestrates server-managed multi-domain playlists, N+1 prefetch, and live controls."""

    def __init__(
        self,
        state: ShowcaseRuntimeState,
        chain_service: DomainSourceChainService,
        seen_repo: ISeenHistoryRepository,
        transcoder: IMediaTranscoderPort,
        streamer: IFrameStreamerPort,
        mqtt: IMqttControlGatewayPort,
        profile_store: IDmdProfileRepository,
        resolve_target_slot_fn: Callable[[str, DomainType], str],
        cleanup_abandoned_fn: Callable[[str], Awaitable[None]],
        purge_consumed_fn: Callable[[str], Awaitable[None]],
    ) -> None:
        self.state = state
        self.chain_service = chain_service
        self.seen_repo = seen_repo
        self.transcoder = transcoder
        self.streamer = streamer
        self.mqtt = mqtt
        self.profile_store = profile_store
        self._resolve_target_slot = resolve_target_slot_fn
        self._cleanup_abandoned = cleanup_abandoned_fn
        self._purge_consumed = purge_consumed_fn

    async def start_server_managed_profiles(self) -> None:
        """Start background `ServerManagedDirector` loops for all `config_source='server'` profiles."""
        await self.sync_server_managed_profiles()

    async def stop_server_managed_profiles(self) -> None:
        """Stop all active `ServerManagedDirector` loops and UDP streams cleanly on shutdown."""
        for dmd_id, task in list(self.state.director_tasks.items()):
            AsyncTaskSupervisor.safe_cancel_task(task)
            pf_task = self.state.director_prefetch_tasks.pop(dmd_id, None)
            AsyncTaskSupervisor.safe_cancel_task(pf_task)
            await self.streamer.stop_stream(dmd_id)
        self.state.director_tasks.clear()
        self.state.server_managed_ids.clear()

    async def sync_server_managed_profiles(self) -> None:
        """Synchronize running `ServerManagedDirector` tasks with `dmd_profiles.json`."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        profiles = self.profile_store.list_profiles()
        desired_server_ids = {
            p["dmd_id"] for p in profiles if p.get("config_source") == "server"
        }

        for dmd_id in list(self.state.server_managed_ids - desired_server_ids):
            self.state.server_managed_ids.discard(dmd_id)
            task = self.state.director_tasks.pop(dmd_id, None)
            AsyncTaskSupervisor.safe_cancel_task(task)
            pf_task = self.state.director_prefetch_tasks.pop(dmd_id, None)
            AsyncTaskSupervisor.safe_cancel_task(pf_task)
            pf_pkg = self.state.director_prefetched.pop(dmd_id, None)
            if pf_pkg is not None and not pf_pkg.consumed:
                self.state.active_packages.pop(pf_pkg.item_id, None)
                shutil.rmtree(pf_pkg.item_dir, ignore_errors=True)
            await self.streamer.stop_stream(dmd_id)

        for prof in profiles:
            dmd_id = prof["dmd_id"]
            if prof.get("udp_ip"):
                self.state.client_udp_ips[dmd_id] = str(prof["udp_ip"])
            self.state.client_udp_ports[dmd_id] = int(prof.get("udp_port") or 4210)
            if prof.get("config_source") != "server":
                continue

            self.state.server_managed_ids.add(dmd_id)
            for dom_k in VALID_DOMAINS:
                t = self.state.prep_tasks.pop((dmd_id, dom_k), None)
                AsyncTaskSupervisor.safe_cancel_task(t)

            self.state.director_wake_events.setdefault(dmd_id, asyncio.Event())
            existing_task = self.state.director_tasks.get(dmd_id)
            if not AsyncTaskSupervisor.is_task_live(existing_task, loop):
                AsyncTaskSupervisor.safe_cancel_task(existing_task)
                self.state.director_tasks[dmd_id] = loop.create_task(
                    self.run_server_managed_loop(dmd_id)
                )
            else:
                self.state.wake_director(dmd_id)

    async def upsert_dmd_profile(self, dmd_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Save a DMD profile, invalidate incompatible prefetched media, and apply live."""
        prev = self.profile_store.get_profile(dmd_id)
        saved = self.profile_store.upsert_profile(dmd_id, data)
        if prev != saved:
            pf_task = self.state.director_prefetch_tasks.pop(dmd_id, None)
            AsyncTaskSupervisor.safe_cancel_task(pf_task)
            pf_pkg = self.state.director_prefetched.pop(dmd_id, None)
            if pf_pkg is not None and not pf_pkg.consumed:
                self.state.active_packages.pop(pf_pkg.item_id, None)
                shutil.rmtree(pf_pkg.item_dir, ignore_errors=True)
        await self.sync_server_managed_profiles()
        self.state.wake_director(dmd_id)
        return saved

    async def delete_dmd_profile(self, dmd_id: str) -> bool:
        deleted = self.profile_store.delete_profile(dmd_id)
        await self.sync_server_managed_profiles()
        return deleted

    async def control_client(self, dmd_id: str, action: str) -> Dict[str, Any]:
        """Execute a live playback control (`skip`, `pause`, `resume`, `stop`) on `dmd_id`."""
        act = str(action or "").strip().lower()
        prof = self.profile_store.get_profile(dmd_id)

        if act == "pause":
            if prof is not None:
                self.profile_store.upsert_profile(dmd_id, {"paused": True})
            await self.streamer.stop_stream(dmd_id)
            abort_ev = self.state.director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/command",
                {"action": "pause", "dmd_id": dmd_id},
                retain=False,
            )
            self.state.wake_director(dmd_id)
            return {"dmd_id": dmd_id, "action": "pause", "paused": True}

        if act == "resume":
            if prof is not None:
                self.profile_store.upsert_profile(dmd_id, {"paused": False})
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/command",
                {"action": "resume", "dmd_id": dmd_id},
                retain=False,
            )
            self.state.wake_director(dmd_id)
            return {"dmd_id": dmd_id, "action": "resume", "paused": False}

        if act == "stop":
            if prof is not None:
                self.profile_store.upsert_profile(dmd_id, {"paused": True})
            await self._cleanup_abandoned(dmd_id)
            abort_ev = self.state.director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/command",
                {"action": "stop", "dmd_id": dmd_id},
                retain=False,
            )
            self.state.wake_director(dmd_id)
            return {"dmd_id": dmd_id, "action": "stop", "paused": True}

        # Default: `skip`
        self.state.director_skip_gap.add(dmd_id)
        await self.streamer.stop_stream(dmd_id)
        abort_ev = self.state.director_play_abort_events.get(dmd_id)
        if abort_ev is not None:
            abort_ev.set()
        for dom_k in VALID_DOMAINS:
            sibling_id = self._resolve_target_slot(dmd_id, dom_k)
            await self.streamer.stop_stream(sibling_id)
            if (sibling_id, dom_k) in self.state.client_configs:
                eof_msg = {"event": "stream_eof", "action": "skip", "domain": dom_k}
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{sibling_id}/{dom_k}/events",
                    eof_msg,
                    retain=False,
                )
        await self.mqtt.publish_json(
            f"dmd_showcase/clients/{dmd_id}/command",
            {"action": "skip", "dmd_id": dmd_id},
            retain=False,
        )
        self.state.wake_director(dmd_id)
        return {"dmd_id": dmd_id, "action": "skip"}

    def next_playlist_slot_criteria(
        self,
        dmd_id: str,
        prof: Dict[str, Any],
        parse_criteria_fn: Callable[[DomainType, Dict[str, Any], str], FilterCriteria] = parse_domain_criteria,
    ) -> Optional[Tuple[DomainType, PanelGeometry, FilterCriteria]]:
        enabled_slots = [
            s
            for s in (prof.get("playlist") or [])
            if s.get("enabled", True) and s.get("domain") in VALID_DOMAINS
        ]
        if not enabled_slots:
            return None

        slot_idx, used_in_slot = self.state.director_playlist_cursors.get(dmd_id, (0, 0))
        if slot_idx >= len(enabled_slots):
            slot_idx = 0
            used_in_slot = 0
        slot = enabled_slots[slot_idx]
        slot_count = max(1, int(slot.get("count") or 1))

        next_used = used_in_slot + 1
        if next_used >= slot_count:
            self.state.director_playlist_cursors[dmd_id] = (
                (slot_idx + 1) % len(enabled_slots),
                0,
            )
        else:
            self.state.director_playlist_cursors[dmd_id] = (slot_idx, next_used)

        dom: DomainType = slot["domain"]  # type: ignore[assignment]
        geometry = PanelGeometry(
            width=int(prof.get("width") or 128),
            height=int(prof.get("height") or 32),
        )
        domain_style = dict((prof.get("domain_styles") or {}).get(dom) or {})
        base_card_style = dict(prof.get("card_style") or {})
        raw_slot = {
            **base_card_style,
            **slot,
            **domain_style,
            "lang": slot.get("lang") or prof.get("lang") or "fr",
            "render_card_in_stream": not bool(prof.get("supports_local_card", True)),
        }
        criteria = parse_criteria_fn(
            dom,
            raw_slot,
            str(prof.get("lang") or "fr"),
        )
        return dom, geometry, criteria

    async def prepare_next_playlist_package(
        self, dmd_id: str, prof: Dict[str, Any]
    ) -> Optional[PreparedShowcasePackage]:
        picked = self.next_playlist_slot_criteria(dmd_id, prof)
        if picked is None:
            return None
        domain, geometry, criteria = picked

        self.state.client_configs[(dmd_id, domain)] = (geometry, criteria)
        self.state.client_transports[(dmd_id, domain)] = (
            "UDP-PASSIVE" if criteria.render_card_in_stream else "MQTT+UDP"
        )

        active_exclude = {
            pkg.candidate.id
            for pkg in self.state.active_packages.values()
            if pkg.domain == domain
        }
        pf_existing = self.state.director_prefetched.get(dmd_id)
        if pf_existing is not None and pf_existing.domain == domain:
            active_exclude.add(pf_existing.candidate.id)

        candidates = await self.chain_service.acquire_unseen_candidates(
            criteria, count=1, extra_exclude_ids=active_exclude
        )
        if not candidates:
            return None
        pkg = await self.state.build_package_from_candidate(
            transcoder=self.transcoder,
            dmd_id=dmd_id,
            domain=domain,
            cand=candidates[0],
            geometry=geometry,
            criteria=criteria,
        )
        if pkg is not None:
            self.state.active_packages[pkg.item_id] = pkg
        return pkg

    async def pop_next_ready_priority_for_director(
        self, dmd_id: str
    ) -> Optional[Tuple[QueuedOnDemandEntry, PreparedShowcasePackage]]:
        q = self.state.priority_queues.get(dmd_id)
        if not q:
            return None
        while q:
            entry = q[0]
            if entry.prep_task is not None and not entry.prep_task.done():
                try:
                    await entry.prep_task
                except Exception:
                    pass
            if not q or q[0] is not entry:
                continue
            q.popleft()
            if entry.package is not None and entry.package.item_dir.exists():
                entry.status = "playing"
                return entry, entry.package
        return None

    async def run_server_managed_loop(self, dmd_id: str) -> None:
        """ServerManagedDirector coroutine driving passive (Mode 2B) and hybrid (Mode 2A) DMDs."""
        wake_ev = self.state.director_wake_events.setdefault(dmd_id, asyncio.Event())
        try:
            while dmd_id in self.state.server_managed_ids:
                prof = self.profile_store.get_profile(dmd_id)
                if prof is None or prof.get("config_source") != "server":
                    return

                supports_local_card = bool(prof.get("supports_local_card", True))
                has_priority = bool(self.state.priority_queues.get(dmd_id))
                if not has_priority and dmd_id not in self.state.director_prefetched:
                    prefetched = await self.prepare_next_playlist_package(dmd_id, prof)
                    if prefetched is not None:
                        self.state.director_prefetched[dmd_id] = prefetched

                prof = self.profile_store.get_profile(dmd_id) or prof
                udp_ip = select_best_udp_ip(
                    str(prof.get("udp_ip") or ""),
                    self.state.client_udp_ips.get(dmd_id),
                    "",
                )
                udp_port = int(
                    prof.get("udp_port") or self.state.client_udp_ports.get(dmd_id, 4210)
                )
                is_online = self.state.client_online.get(dmd_id, False) or bool(
                    prof.get("always_stream_udp", False)
                )
                can_play = (not bool(prof.get("paused", False))) and is_online

                if not can_play:
                    wake_ev.clear()
                    prof_recheck = self.profile_store.get_profile(dmd_id) or prof
                    still_blocked = bool(prof_recheck.get("paused", False)) or not (
                        self.state.client_online.get(dmd_id, False)
                        or bool(prof_recheck.get("always_stream_udp", False))
                    )
                    if still_blocked:
                        await wake_ev.wait()
                    continue

                prio_pair = await self.pop_next_ready_priority_for_director(dmd_id)
                if prio_pair is not None:
                    _entry, pkg = prio_pair
                else:
                    pkg = self.state.director_prefetched.pop(dmd_id, None)
                    if pkg is None or not pkg.item_dir.exists():
                        pkg = await self.prepare_next_playlist_package(dmd_id, prof)

                if pkg is None:
                    wake_ev.clear()
                    try:
                        await asyncio.wait_for(wake_ev.wait(), timeout=2.5)
                    except asyncio.TimeoutError:
                        pass
                    continue

                loop = asyncio.get_running_loop()
                if (
                    dmd_id not in self.state.director_prefetched
                    and not AsyncTaskSupervisor.is_task_live(
                        self.state.director_prefetch_tasks.get(dmd_id), loop
                    )
                ):

                    async def _bg_prefetch() -> None:
                        cur_prof = self.profile_store.get_profile(dmd_id)
                        if cur_prof and cur_prof.get("config_source") == "server":
                            nxt = await self.prepare_next_playlist_package(dmd_id, cur_prof)
                            if nxt is not None and dmd_id not in self.state.director_prefetched:
                                self.state.director_prefetched[dmd_id] = nxt

                    self.state.director_prefetch_tasks[dmd_id] = loop.create_task(
                        _bg_prefetch()
                    )

                abort_ev = asyncio.Event()
                self.state.director_play_abort_events[dmd_id] = abort_ev
                now_ts = time.time()
                self.state.client_last_seen[(dmd_id, pkg.domain)] = now_ts
                self.state.last_played_titles[(dmd_id, pkg.domain)] = pkg.card.title

                if not supports_local_card:
                    pkg.consumed = True
                    should_mark = self.state.package_mark_seen.pop(pkg.item_id, True)
                    self.state.priority_item_ids.discard(pkg.item_id)
                    if should_mark:
                        self.seen_repo.mark_consumed(pkg.domain, pkg.candidate.id)

                    started_msg = {
                        "event": "stream_started",
                        "action": "play_stream",
                        "mode": "passive_udp",
                        "dmd_id": dmd_id,
                        "domain": pkg.domain,
                        "item_id": pkg.item_id,
                        "title": pkg.card.title,
                        "lang": pkg.card.lang,
                        "frames": pkg.card.stream_frames,
                        "fps": pkg.card.stream_fps,
                    }
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/command", started_msg, retain=False
                    )
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/stream_state", started_msg, retain=False
                    )

                    if (
                        pkg.unified_stream_path is not None
                        and pkg.unified_stream_path.exists()
                        and pkg.card.stream_frames > 0
                    ):
                        self.state.last_claimed_until[(dmd_id, pkg.domain)] = (
                            now_ts + max(3.0, float(pkg.card.stream_duration_s or 0.0) + 2.0)
                        )

                        async def _on_passive_eof(
                            client_dmd_id: str, finished_item_id: str, _ok: bool
                        ) -> None:
                            self.state.last_claimed_until.pop(
                                (client_dmd_id, pkg.domain), None
                            )
                            eof_payload = {
                                "event": "stream_eof",
                                "item_id": finished_item_id,
                                "domain": pkg.domain,
                            }
                            await self.mqtt.publish_json(
                                f"dmd_showcase/clients/{client_dmd_id}/stream_state",
                                eof_payload,
                                retain=False,
                            )
                            abort_ev.set()

                        await self.streamer.start_udp_clock_stream(
                            dmd_id=dmd_id,
                            item_id=pkg.item_id,
                            stream_path=pkg.unified_stream_path,
                            width=pkg.geometry.width,
                            height=pkg.geometry.height,
                            fps=pkg.card.stream_fps,
                            udp_ip=udp_ip,
                            udp_port=udp_port,
                            on_eof=_on_passive_eof,
                        )
                        await abort_ev.wait()
                        await self.streamer.stop_stream(dmd_id)

                    await self._purge_consumed(pkg.item_id)
                else:
                    slot_key = (dmd_id, pkg.domain)
                    self.state.ready_packages[slot_key] = pkg
                    card_dict = pkg.card.to_dict()
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/{pkg.domain}/ready",
                        {
                            "ready": True,
                            "item_id": pkg.item_id,
                            "domain": pkg.domain,
                            "card": card_dict,
                        },
                        retain=True,
                    )
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/command",
                        {
                            "action": "play_card",
                            "mode": "hybrid",
                            "dmd_id": dmd_id,
                            "domain": pkg.domain,
                            "item_id": pkg.item_id,
                            "card": card_dict,
                        },
                        retain=False,
                    )
                    max_wait_s = max(12.0, float(pkg.card.stream_duration_s or 0.0) + 20.0)
                    try:
                        await asyncio.wait_for(abort_ev.wait(), timeout=max_wait_s)
                    except asyncio.TimeoutError:
                        pass
                    self.state.ready_packages.pop(slot_key, None)
                    if pkg.item_id in self.state.active_packages:
                        await self._purge_consumed(pkg.item_id)

                self.state.director_play_abort_events.pop(dmd_id, None)

                if dmd_id in self.state.director_skip_gap:
                    self.state.director_skip_gap.discard(dmd_id)
                    continue
                cur_prof = self.profile_store.get_profile(dmd_id) or prof
                gap_s = max(0.0, float(cur_prof.get("inter_item_gap_s") or 0.0))
                if gap_s > 0.0 and not self.state.priority_queues.get(dmd_id):
                    wake_ev.clear()
                    try:
                        await asyncio.wait_for(wake_ev.wait(), timeout=gap_s)
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            logger.info("[ORCHESTRATOR] Stopped ServerManagedDirector for '%s'", dmd_id)
        except Exception as exc:
            logger.error("[ORCHESTRATOR] ServerManagedDirector error for '%s': %s", dmd_id, exc)
