"""
Application Service: `SlotPreparationService` (`app/application/slot_preparation_service.py`).

Implements the Mode 1 (`client`-driven) `N / N+1` dual-slot Consume-Once preparation pipeline,
multi-domain fairness yielding, atomic `start_card` locking, real-time UDP `start_stream`
launching, and disk purge on `stream_eof` / `stop_stream`.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace as dc_replace
import logging
import shutil
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Set
import uuid

from app.application.concurrency import AsyncTaskSupervisor
from app.application.criteria_parser import assemble_prepared_package, parse_domain_criteria
from app.application.showcase_state import ShowcaseRuntimeState
from app.application.source_chain_service import DomainSourceChainService
from app.config import settings
from app.domain.board_identity import canonical_dmd_board_key, select_best_udp_ip
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
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

logger = logging.getLogger("dmd_showcase.application.slot_pipeline")


class SlotPreparationService:
    """Manages per-DMD, per-domain `N / N+1` Consume-Once slot lifecycle and MQTT client events."""

    def __init__(
        self,
        state: ShowcaseRuntimeState,
        chain_service: DomainSourceChainService,
        seen_repo: ISeenHistoryRepository,
        transcoder: IMediaTranscoderPort,
        streamer: IFrameStreamerPort,
        mqtt: IMqttControlGatewayPort,
        profile_store: IDmdProfileRepository,
        schedule_hook: Callable[[str, DomainType], None],
        pop_claimed_priority_fn: Callable[[str, str], None],
        take_next_priority_pkg_fn: Callable[
            [str, DomainType], Optional[PreparedShowcasePackage]
        ],
    ) -> None:
        self.state = state
        self.chain_service = chain_service
        self.seen_repo = seen_repo
        self.transcoder = transcoder
        self.streamer = streamer
        self.mqtt = mqtt
        self.profile_store = profile_store
        self._schedule_hook = schedule_hook
        self._pop_claimed_priority = pop_claimed_priority_fn
        self._take_next_priority_pkg = take_next_priority_pkg_fn

    async def apply_runtime_limits(self) -> int:
        """Enforce updated server-wide duration/FPS ceilings across unconsumed N / N+1 slots."""
        max_dur_ceiling = max(1.5, float(settings.video_max_duration_limit_s))
        max_fps_ceiling = max(6, int(settings.video_max_fps_limit))
        invalidated = 0

        for key, (geom, crit) in list(self.state.client_configs.items()):
            dmd_id, domain = key
            clamped_dur = max(1.5, min(max_dur_ceiling, float(crit.video_max_duration_s)))
            clamped_fps = max(6, min(max_fps_ceiling, int(crit.video_fps)))
            crit_changed = (
                abs(clamped_dur - float(crit.video_max_duration_s)) > 1e-6
                or clamped_fps != int(crit.video_fps)
            )
            ready_pkg = self.state.ready_packages.get(key)
            standby_pkg = self.state.standby_packages.get(key)
            ready_exceeds = bool(
                ready_pkg is not None
                and ready_pkg.card.has_video
                and (
                    ready_pkg.card.stream_fps > max_fps_ceiling
                    or ready_pkg.card.stream_duration_s > max_dur_ceiling + 16.0
                )
            )
            standby_exceeds = bool(
                standby_pkg is not None
                and standby_pkg.card.has_video
                and (
                    standby_pkg.card.stream_fps > max_fps_ceiling
                    or standby_pkg.card.stream_duration_s > max_dur_ceiling + 16.0
                )
            )
            if not (crit_changed or ready_exceeds or standby_exceeds):
                continue

            invalidated += 1
            new_crit = dc_replace(
                crit,
                video_max_duration_s=clamped_dur,
                video_fps=clamped_fps,
            )
            self.state.client_configs[key] = (geom, new_crit)
            old_task = self.state.prep_tasks.pop(key, None)
            AsyncTaskSupervisor.safe_cancel_task(old_task)

            stale_standby = self.state.standby_packages.pop(key, None)
            if stale_standby is not None and not stale_standby.consumed:
                self.state.active_packages.pop(stale_standby.item_id, None)
                shutil.rmtree(stale_standby.item_dir, ignore_errors=True)

            stale_ready = self.state.ready_packages.pop(key, None)
            if stale_ready is not None and not stale_ready.consumed:
                self.state.active_packages.pop(stale_ready.item_id, None)
                shutil.rmtree(stale_ready.item_dir, ignore_errors=True)

            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {"ready": False, "item_id": "", "domain": domain, "status": "reconfiguring"},
                retain=True,
            )
            self._schedule_hook(dmd_id, domain)

        return invalidated

    async def cleanup_abandoned_active_packages(self, dmd_id: str) -> None:
        """Stop active UDP stream for `dmd_id` and purge any `consumed=True` abandoned package."""
        await self.streamer.stop_stream(dmd_id)
        for dom_key in VALID_DOMAINS:
            self.state.last_claimed_until.pop((dmd_id, dom_key), None)
        abandoned_ids = [
            item_id
            for item_id, pkg in list(self.state.active_packages.items())
            if pkg.dmd_id == dmd_id and pkg.consumed
        ]
        for item_id in abandoned_ids:
            pkg = self.state.active_packages.pop(item_id, None)
            if pkg is not None:
                shutil.rmtree(pkg.item_dir, ignore_errors=True)
                logger.info(
                    "[ORCHESTRATOR] Purged abandoned media files for '%s' ('%s')",
                    item_id,
                    pkg.candidate.title,
                )

    async def handle_mqtt_client_config(
        self,
        dmd_id: str,
        payload: Dict[str, Any],
        on_hello: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
        parse_criteria_fn: Callable[[DomainType, Dict[str, Any], str], FilterCriteria] = parse_domain_criteria,
    ) -> None:
        """Handle retained `dmd_showcase/clients/{dmd_id}/config` published by a DMD."""
        if (
            payload.get("_is_hello")
            or str(payload.get("config_source") or "").strip().lower() == "server"
        ):
            await on_hello(dmd_id, payload)
            return

        existing_prof = self.profile_store.get_profile(dmd_id)
        if existing_prof is not None and existing_prof.get("config_source") == "server":
            await on_hello(dmd_id, payload)
            return

        self.state.client_online[dmd_id] = True
        width = int(payload.get("width") or 128)
        height = int(payload.get("height") or 32)
        top_lang = str(payload.get("lang") or "fr")
        geometry = PanelGeometry(width=width, height=height)

        declared_ip = str(payload.get("client_ip") or payload.get("udp_ip") or "").strip()
        peer_ip = str(payload.get("_peer_ip") or "").strip()
        best_ip = select_best_udp_ip(
            declared_ip, self.state.client_udp_ips.get(dmd_id), peer_ip
        )
        if best_ip:
            self.state.client_udp_ips[dmd_id] = best_ip
        if payload.get("udp_port"):
            try:
                self.state.client_udp_ports[dmd_id] = max(
                    1, min(65535, int(payload["udp_port"]))
                )
            except (TypeError, ValueError):
                pass

        await self.cleanup_abandoned_active_packages(dmd_id)

        if payload.get("unregister"):
            self.state.client_raw_configs.pop(dmd_id, None)
            for dom_key in VALID_DOMAINS:
                self.state.client_configs.pop((dmd_id, dom_key), None)
                self.state.client_transports.pop((dmd_id, dom_key), None)
                self.state.client_last_seen.pop((dmd_id, dom_key), None)
                self.state.last_played_titles.pop((dmd_id, dom_key), None)
                self.state.last_claimed_until.pop((dmd_id, dom_key), None)
                t = self.state.prep_tasks.pop((dmd_id, dom_key), None)
                AsyncTaskSupervisor.safe_cancel_task(t)
            return

        self.state.client_raw_configs[dmd_id] = {
            str(k): v for k, v in payload.items() if not str(k).startswith("_")
        }

        if best_ip and best_ip != "127.0.0.1":
            for other_dmd_id, other_ip in list(self.state.client_udp_ips.items()):
                if other_dmd_id != dmd_id and other_ip == best_ip:
                    await self.streamer.stop_stream(other_dmd_id)

        now_ts = time.time()
        engines_map = payload.get("domains") or payload.get("engines")
        if isinstance(engines_map, dict):
            for dom_key, dom_cfg in engines_map.items():
                if dom_key in VALID_DOMAINS and isinstance(dom_cfg, dict):
                    dom: DomainType = dom_key  # type: ignore[assignment]
                    self.state.client_transports[(dmd_id, dom)] = "MQTT+UDP"
                    self.state.client_last_seen[(dmd_id, dom)] = now_ts
                    criteria = parse_criteria_fn(dom, dom_cfg, top_lang)
                    await self.register_client_domain(dmd_id, dom, geometry, criteria)
        else:
            dom_raw = str(payload.get("domain") or "games")
            if dom_raw in VALID_DOMAINS:
                dom_single: DomainType = dom_raw  # type: ignore[assignment]
                self.state.client_transports[(dmd_id, dom_single)] = "MQTT+UDP"
                self.state.client_last_seen[(dmd_id, dom_single)] = now_ts
                criteria = parse_criteria_fn(dom_single, payload, top_lang)
                await self.register_client_domain(dmd_id, dom_single, geometry, criteria)

    async def register_client_domain(
        self,
        dmd_id: str,
        domain: DomainType,
        geometry: PanelGeometry,
        criteria: FilterCriteria,
    ) -> None:
        key = (dmd_id, domain)
        old_pair = self.state.client_configs.get(key)
        self.state.client_configs[key] = (geometry, criteria)

        if old_pair is not None and old_pair != (geometry, criteria):
            logger.info(
                "[ORCHESTRATOR] Retained config changed for dmd='%s' domain='%s' "
                "(%dx%d, lang=%s, systems=%s, genres=%s, decades=%s, speed=%s) -> invalidating stale slot",
                dmd_id,
                domain,
                geometry.width,
                geometry.height,
                criteria.lang,
                criteria.systems,
                criteria.genres,
                criteria.decades,
                criteria.text_scroll_speed,
            )
            old_task = self.state.prep_tasks.pop(key, None)
            AsyncTaskSupervisor.safe_cancel_task(old_task)
            stale_standby = self.state.standby_packages.pop(key, None)
            if stale_standby is not None:
                self.state.active_packages.pop(stale_standby.item_id, None)
                shutil.rmtree(stale_standby.item_dir, ignore_errors=True)
            stale_pkg = self.state.ready_packages.pop(key, None)
            if stale_pkg is not None:
                self.state.active_packages.pop(stale_pkg.item_id, None)
                shutil.rmtree(stale_pkg.item_dir, ignore_errors=True)
            for active_id, active_pkg in list(self.state.active_packages.items()):
                if active_pkg.dmd_id == dmd_id and active_pkg.domain == domain:
                    self.state.active_packages.pop(active_id, None)
                    shutil.rmtree(active_pkg.item_dir, ignore_errors=True)
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {"ready": False, "item_id": "", "domain": domain, "status": "reconfiguring"},
                retain=True,
            )
            self._schedule_hook(dmd_id, domain)
            return

        existing_ready = self.state.ready_packages.get(key)
        if existing_ready is not None and not existing_ready.consumed:
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {
                    "ready": True,
                    "item_id": existing_ready.item_id,
                    "domain": domain,
                    "card": existing_ready.card.to_dict(),
                },
                retain=True,
            )
            self._schedule_hook(dmd_id, domain)
            return

        if old_pair is None and key not in self.state.ready_packages:
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {"ready": False, "item_id": "", "domain": domain, "status": "preparing"},
                retain=True,
            )

        if (key not in self.state.ready_packages or key not in self.state.standby_packages) and (
            not AsyncTaskSupervisor.is_task_live(self.state.prep_tasks.get(key))
        ):
            if old_pair is None:
                logger.info(
                    "[ORCHESTRATOR] Registered dmd='%s' domain='%s' (%dx%d, lang=%s, systems=%s, genres=%s, decades=%s, speed=%s)",
                    dmd_id,
                    domain,
                    geometry.width,
                    geometry.height,
                    criteria.lang,
                    criteria.systems,
                    criteria.genres,
                    criteria.decades,
                    criteria.text_scroll_speed,
                )
            self._schedule_hook(dmd_id, domain)

    def schedule_preparation(self, dmd_id: str, domain: DomainType) -> None:
        if dmd_id in self.state.server_managed_ids:
            return
        key = (dmd_id, domain)
        if key in self.state.ready_packages and key in self.state.standby_packages:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        existing = self.state.prep_tasks.get(key)
        if AsyncTaskSupervisor.is_task_live(existing, loop):
            return
        AsyncTaskSupervisor.safe_cancel_task(existing)
        self.state.prep_tasks[key] = loop.create_task(self.prepare_slot_task(dmd_id, domain))

    async def prepare_slot_task(self, dmd_id: str, domain: DomainType) -> None:
        key = (dmd_id, domain)
        try:
            while key in self.state.client_configs and (
                key not in self.state.ready_packages or key not in self.state.standby_packages
            ):
                if key in self.state.ready_packages:
                    sibling_missing_ready = any(
                        other_k != key
                        and other_k not in self.state.ready_packages
                        and AsyncTaskSupervisor.is_task_live(self.state.prep_tasks.get(other_k))
                        for other_k in list(self.state.client_configs.keys())
                    )
                    if sibling_missing_ready:
                        self.state.metrics.fairness_waiting_jobs += 1
                        try:
                            await asyncio.sleep(0.05)
                        finally:
                            self.state.metrics.fairness_waiting_jobs = max(
                                0, self.state.metrics.fairness_waiting_jobs - 1
                            )
                        continue

                cfg_pair = self.state.client_configs.get(key)
                if not cfg_pair:
                    return
                geometry, criteria = cfg_pair

                active_exclude: Set[str] = {
                    pkg.candidate.id
                    for pkg in self.state.active_packages.values()
                    if pkg.domain == domain
                }
                for slot_pkg in list(self.state.ready_packages.values()) + list(
                    self.state.standby_packages.values()
                ):
                    if slot_pkg.domain == domain:
                        active_exclude.add(slot_pkg.candidate.id)

                async def _acquire_and_transcode() -> Optional[
                    tuple[MediaCandidate, str, Any, Dict[str, Any]]
                ]:
                    candidates = await self.chain_service.acquire_unseen_candidates(
                        criteria, count=1, extra_exclude_ids=active_exclude
                    )
                    if not candidates:
                        return None
                    cand_found = candidates[0]
                    active_exclude.add(cand_found.id)
                    iid = f"pkg_{domain}_{uuid.uuid4().hex[:8]}_{cand_found.id}"
                    idir = settings.cache_dir / "consume_once" / dmd_id / domain / iid
                    fmap = await self.transcoder.transcode_candidate(
                        cand_found, criteria, geometry, idir
                    )
                    return cand_found, iid, idir, fmap

                job_res = await self.state.execute_with_transcode_gate(
                    _acquire_and_transcode,
                    should_count_completion=lambda res: res is not None,
                )

                if self.state.client_configs.get(key) != (geometry, criteria):
                    if job_res is not None:
                        shutil.rmtree(job_res[2], ignore_errors=True)
                    continue

                chosen_pkg: Optional[PreparedShowcasePackage] = None
                if job_res is not None:
                    cand, item_id, item_dir, files_map = job_res
                    chosen_pkg = assemble_prepared_package(
                        item_id=item_id,
                        domain=domain,
                        dmd_id=dmd_id,
                        cand=cand,
                        geometry=geometry,
                        criteria=criteria,
                        item_dir=item_dir,
                        files_map=files_map,
                    )

                if chosen_pkg is None:
                    if key not in self.state.ready_packages:
                        await self.mqtt.publish_json(
                            f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                            {"ready": False, "item_id": "", "status": "empty_catalog"},
                            retain=True,
                        )
                    loop = asyncio.get_running_loop()

                    async def _delayed_retry() -> None:
                        await asyncio.sleep(5.0)
                        if key in self.state.client_configs:
                            self._schedule_hook(dmd_id, domain)

                    loop.create_task(_delayed_retry())
                    return

                if not chosen_pkg.item_dir.exists():
                    return

                self.state.active_packages[chosen_pkg.item_id] = chosen_pkg
                if key not in self.state.ready_packages:
                    self.state.ready_packages[key] = chosen_pkg
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                        {
                            "ready": True,
                            "item_id": chosen_pkg.item_id,
                            "domain": domain,
                            "card": chosen_pkg.card.to_dict(),
                        },
                        retain=True,
                    )
                    logger.info(
                        "[ORCHESTRATOR] Prepared Consume-Once slot for dmd='%s' domain='%s' -> '%s' (source=%s, has_video=%s, frames=%d)",
                        dmd_id,
                        domain,
                        chosen_pkg.candidate.title,
                        chosen_pkg.candidate.source_id,
                        chosen_pkg.card.has_video,
                        chosen_pkg.card.stream_frames,
                    )
                else:
                    self.state.standby_packages[key] = chosen_pkg
                    logger.info(
                        "[ORCHESTRATOR] Prepared N+1 standby slot for dmd='%s' domain='%s' -> '%s' (source=%s, has_video=%s, frames=%d)",
                        dmd_id,
                        domain,
                        chosen_pkg.candidate.title,
                        chosen_pkg.candidate.source_id,
                        chosen_pkg.card.has_video,
                        chosen_pkg.card.stream_frames,
                    )
        except asyncio.CancelledError:
            logger.info(
                "[ORCHESTRATOR] Cancelled outdated preparation task for %s/%s", dmd_id, domain
            )
        except Exception as exc:
            logger.error(
                "[ORCHESTRATOR] Failed preparing slot for %s/%s: %s", dmd_id, domain, exc
            )

    async def claim_next_package(
        self, dmd_id: str, domain: DomainType, item_id: Optional[str] = None
    ) -> Optional[PreparedShowcasePackage]:
        """Atomically claim and lock the prepared package for `(dmd_id, domain)`."""
        key = (dmd_id, domain)
        pkg = self.state.ready_packages.pop(key, None)
        if pkg is None and item_id:
            pkg = self.state.active_packages.get(item_id)
            if pkg and pkg.consumed:
                return pkg
        if pkg is None:
            self._schedule_hook(dmd_id, domain)
            return None

        now_ts = time.time()
        pkg.consumed = True
        self.state.last_played_titles[key] = pkg.card.title
        self.state.client_last_seen[key] = now_ts
        board_key = canonical_dmd_board_key(dmd_id, self.state.client_udp_ips.get(dmd_id, ""))
        for other_key in list(self.state.last_claimed_until.keys()):
            if other_key != key:
                other_board = canonical_dmd_board_key(
                    other_key[0], self.state.client_udp_ips.get(other_key[0], "")
                )
                if other_board == board_key:
                    self.state.last_claimed_until.pop(other_key, None)
        display_window_s = max(10.0, float(pkg.card.stream_duration_s or 0.0) + 8.0)
        self.state.last_claimed_until[key] = now_ts + display_window_s
        should_mark_seen = self.state.package_mark_seen.pop(pkg.item_id, True)
        self.state.priority_item_ids.discard(pkg.item_id)
        if should_mark_seen:
            self.seen_repo.mark_consumed(domain, pkg.candidate.id)

        self._pop_claimed_priority(dmd_id, pkg.item_id)

        next_priority_pkg = self._take_next_priority_pkg(dmd_id, domain)
        if next_priority_pkg is not None:
            self.state.ready_packages[key] = next_priority_pkg
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {
                    "ready": True,
                    "item_id": next_priority_pkg.item_id,
                    "domain": domain,
                    "card": next_priority_pkg.card.to_dict(),
                },
                retain=True,
            )
        else:
            standby_pkg = self.state.standby_packages.pop(key, None)
            if standby_pkg is not None and not standby_pkg.consumed:
                self.state.ready_packages[key] = standby_pkg
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                    {
                        "ready": True,
                        "item_id": standby_pkg.item_id,
                        "domain": domain,
                        "card": standby_pkg.card.to_dict(),
                    },
                    retain=True,
                )
                logger.info(
                    "[ORCHESTRATOR] Promoted N+1 standby slot '%s' ('%s') to ready for dmd='%s' domain='%s'",
                    standby_pkg.item_id,
                    standby_pkg.candidate.title,
                    dmd_id,
                    domain,
                )
            else:
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                    {"ready": False, "item_id": pkg.item_id, "domain": domain},
                    retain=True,
                )
        self._schedule_hook(dmd_id, domain)
        return pkg

    async def purge_consumed_package(self, item_id: str) -> None:
        """Delete all disk files of a consumed package and ensure the next slot is prepared."""
        self.state.package_mark_seen.pop(item_id, None)
        self.state.priority_item_ids.discard(item_id)
        pkg = self.state.active_packages.pop(item_id, None)
        if pkg is not None:
            shutil.rmtree(pkg.item_dir, ignore_errors=True)
            logger.info(
                "[ORCHESTRATOR] Purged consumed media files for '%s' ('%s')",
                item_id,
                pkg.candidate.title,
            )
            abort_ev = self.state.director_play_abort_events.get(pkg.dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            self._schedule_hook(pkg.dmd_id, pkg.domain)

    async def handle_mqtt_client_event(
        self, dmd_id: str, event_payload: Dict[str, Any]
    ) -> None:
        """Handle real-time DMD events (`start_card`, `start_stream`, `stop_stream`, presence)."""
        ev_type = str(event_payload.get("event") or event_payload.get("action") or "")
        dom_raw = str(event_payload.get("domain") or "games")
        item_id = str(event_payload.get("item_id") or "")
        domain: DomainType = dom_raw if dom_raw in VALID_DOMAINS else "games"  # type: ignore[assignment]

        declared_ip = str(
            event_payload.get("udp_ip") or event_payload.get("client_ip") or ""
        ).strip()
        if declared_ip and declared_ip not in ("0.0.0.0", "127.0.0.1"):
            self.state.client_udp_ips[dmd_id] = declared_ip
        if (dmd_id, domain) in self.state.client_configs:
            self.state.client_transports[(dmd_id, domain)] = "MQTT+UDP"
            self.state.client_last_seen[(dmd_id, domain)] = time.time()

        if ev_type == "start_card":
            pkg = await self.claim_next_package(dmd_id, domain, item_id=item_id)
            logger.info(
                "[ORCHESTRATOR] Event 'start_card' from dmd='%s' domain='%s' item='%s' -> claimed=%s (N+1 background prep triggered)",
                dmd_id,
                domain,
                item_id,
                pkg.candidate.title if pkg else "none",
            )
            if pkg and pkg.card.stream_frames == 0:
                await self.purge_consumed_package(pkg.item_id)

        elif ev_type == "start_stream":
            pkg = self.state.active_packages.get(item_id)
            if pkg is None:
                pkg = await self.claim_next_package(dmd_id, domain, item_id=item_id)
            if (
                pkg is None
                or not pkg.unified_stream_path
                or not pkg.unified_stream_path.exists()
            ):
                logger.warning(
                    "[ORCHESTRATOR] Event 'start_stream' from dmd='%s' for '%s', but stream file is missing -> sending stream_eof",
                    dmd_id,
                    item_id,
                )
                eof_msg = {"event": "stream_eof", "item_id": item_id, "domain": domain}
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{dmd_id}/{domain}/events", eof_msg, retain=False
                )
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{dmd_id}/stream_state", eof_msg, retain=False
                )
                if item_id:
                    await self.purge_consumed_package(item_id)
                return

            if not pkg.consumed:
                await self.claim_next_package(dmd_id, domain, item_id=item_id)

            udp_ip = select_best_udp_ip(
                declared_ip,
                self.state.client_udp_ips.get(dmd_id),
                str(event_payload.get("_peer_ip") or ""),
            )
            udp_port = int(event_payload.get("udp_port") or 4210)
            logger.info(
                "[ORCHESTRATOR] Event 'start_stream' from dmd='%s' item='%s' ('%s') -> streaming %d frames @ %dfps to UDP %s:%d",
                dmd_id,
                pkg.item_id,
                pkg.candidate.title,
                pkg.card.stream_frames,
                pkg.card.stream_fps,
                udp_ip,
                udp_port,
            )
            started_msg = {
                "event": "stream_started",
                "item_id": pkg.item_id,
                "domain": domain,
            }
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/events", started_msg, retain=False
            )
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/stream_state", started_msg, retain=False
            )

            async def _on_stream_eof(
                client_dmd_id: str, finished_item_id: str, _ok: bool
            ) -> None:
                self.state.last_claimed_until.pop((client_dmd_id, domain), None)
                eof_msg = {
                    "event": "stream_eof",
                    "item_id": finished_item_id,
                    "domain": domain,
                }
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{client_dmd_id}/{domain}/events",
                    eof_msg,
                    retain=False,
                )
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{client_dmd_id}/stream_state",
                    eof_msg,
                    retain=False,
                )
                await self.purge_consumed_package(finished_item_id)

            await self.streamer.start_udp_clock_stream(
                dmd_id=dmd_id,
                item_id=pkg.item_id,
                stream_path=pkg.unified_stream_path,
                width=pkg.geometry.width,
                height=pkg.geometry.height,
                fps=pkg.card.stream_fps,
                udp_ip=udp_ip,
                udp_port=udp_port,
                on_eof=_on_stream_eof,
            )

        elif ev_type == "stop_stream":
            logger.info(
                "[ORCHESTRATOR] Event 'stop_stream' from dmd='%s' item='%s'", dmd_id, item_id
            )
            self.state.last_claimed_until.pop((dmd_id, domain), None)
            await self.streamer.stop_stream(dmd_id)
            if item_id:
                await self.purge_consumed_package(item_id)
            else:
                consumed_ids = [
                    aid
                    for aid, pkg in list(self.state.active_packages.items())
                    if pkg.dmd_id == dmd_id and pkg.domain == domain and pkg.consumed
                ]
                for aid in consumed_ids:
                    await self.purge_consumed_package(aid)

        elif ev_type == "client_offline":
            logger.info(
                "[ORCHESTRATOR] DMD client '%s' disconnected -> stopping active UDP streams (preserving retained slot prep)",
                dmd_id,
            )
            self.state.client_online[dmd_id] = False
            abort_ev = self.state.director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()
            await self.cleanup_abandoned_active_packages(dmd_id)

        elif ev_type == "client_online":
            self.state.client_online[dmd_id] = True
            self.state.wake_director(dmd_id)

        elif ev_type in ("card_done", "item_done"):
            if item_id:
                await self.purge_consumed_package(item_id)
            abort_ev = self.state.director_play_abort_events.get(dmd_id)
            if abort_ev is not None:
                abort_ev.set()
