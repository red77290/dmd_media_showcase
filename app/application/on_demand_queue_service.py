"""
Application Service: `OnDemandQueueService` (`app/application/on_demand_queue_service.py`).

Manages per-DMD priority On-Demand queues (`enqueue_next` / `play_now` preemption),
`0 ms` zero-retranscode Studio simulation promotion, and staged/1-shot candidate lookup.
"""

from __future__ import annotations

import asyncio
from collections import deque
import logging
import shutil
from typing import Any, Callable, Dict, List, Optional
import uuid

from app.application.concurrency import AsyncTaskSupervisor
from app.application.criteria_parser import assemble_prepared_package, parse_domain_criteria
from app.application.showcase_state import QueuedOnDemandEntry, ShowcaseRuntimeState
from app.application.source_chain_service import DomainSourceChainService
from app.application.studio_preview_service import StudioPreviewService
from app.application.sync_slot_rules import (
    is_sync_dmd_id,
    resolve_donor_style_dmd_id,
    search_candidate_with_fallback,
    strip_sync_suffix,
)
from app.config import settings
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
)
from app.domain.on_demand_cache_key import (
    build_on_demand_request_cache_key,
    candidate_from_cache_dict,
    candidate_to_cache_dict,
)
from app.domain.ports import (
    ConversionAbortedError,
    IDmdProfileRepository,
    IFrameStreamerPort,
    IMediaTranscoderPort,
    IMqttControlGatewayPort,
    IOnDemandCachePort,
)

logger = logging.getLogger("dmd_showcase.application.priority_queue")

_STYLE_OVERRIDE_FIELDS = (
    "include_card_intro",
    "card_layout",
    "card_theme",
    "card_font",
    "card_accent_color",
    "show_badge",
    "show_year",
    "show_rating",
    "card_hold_s",
    "include_poster",
    "include_synopsis",
    "text_scroll_speed",
    "jacket_panorama",
    "marquee_panorama",
    "snapshot_panorama_count",
    "panorama_duration_s",
    "include_video",
    "video_max_duration_s",
    "video_fps",
    "video_mode",
    "color_boost",
)


def _style_dict_from_criteria(crit: FilterCriteria) -> Dict[str, Any]:
    return {name: getattr(crit, name) for name in _STYLE_OVERRIDE_FIELDS}


class OnDemandQueueService:
    """Manages priority On-Demand queue entries across Mode 1, Mode 2A, and Mode 2B DMDs."""

    def __init__(
        self,
        state: ShowcaseRuntimeState,
        chain_service: DomainSourceChainService,
        studio_service: StudioPreviewService,
        transcoder: IMediaTranscoderPort,
        streamer: IFrameStreamerPort,
        mqtt: IMqttControlGatewayPort,
        profile_store: IDmdProfileRepository,
        resolve_target_slot_fn: Callable[[str, DomainType], str],
        schedule_hook: Callable[[str, DomainType], None],
        on_demand_cache: Optional[IOnDemandCachePort] = None,
    ) -> None:
        self.state = state
        self.chain_service = chain_service
        self.studio_service = studio_service
        self.transcoder = transcoder
        self.streamer = streamer
        self.mqtt = mqtt
        self.profile_store = profile_store
        self._resolve_target_slot = resolve_target_slot_fn
        self._schedule_hook = schedule_hook
        self.on_demand_cache = on_demand_cache

    def list_queue(self, dmd_id: str) -> List[Dict[str, Any]]:
        q = self.state.priority_queues.get(dmd_id)
        if not q:
            return []
        return [entry.to_dict() for entry in list(q)]

    def pop_claimed_priority_entry(self, slot_dmd_id: str, item_id: str) -> None:
        for q in self.state.priority_queues.values():
            for entry in list(q):
                if (
                    entry.package is not None
                    and entry.package.item_id == item_id
                    and entry.target_slot_dmd_id == slot_dmd_id
                ):
                    try:
                        q.remove(entry)
                    except ValueError:
                        pass
                    return

    def take_next_ready_priority_package_for_slot(
        self, slot_dmd_id: str, domain: DomainType
    ) -> Optional[PreparedShowcasePackage]:
        for q in self.state.priority_queues.values():
            for entry in q:
                if (
                    entry.target_slot_dmd_id == slot_dmd_id
                    and entry.domain == domain
                    and entry.status == "ready"
                    and entry.package is not None
                    and not entry.package.consumed
                ):
                    return entry.package
        return None

    def _cleanup_entry_package(self, entry: QueuedOnDemandEntry) -> Optional[PreparedShowcasePackage]:
        AsyncTaskSupervisor.safe_cancel_task(entry.prep_task)
        pkg = entry.package
        if pkg is None:
            return None
        self.state.package_mark_seen.pop(pkg.item_id, None)
        self.state.priority_item_ids.discard(pkg.item_id)
        self.state.active_packages.pop(pkg.item_id, None)
        shutil.rmtree(pkg.item_dir, ignore_errors=True)
        return pkg

    async def clear_dmd_queue(self, dmd_id: str) -> int:
        """
        Atomically terminate all active conversion processes (`1..N`) for `dmd_id`, cancel all
        pending/ready priority queue entries, stop any active stream, and purge temporary files.
        """
        clean_id = str(dmd_id or "").strip()
        if not clean_id:
            return 0
        self.transcoder.abort_conversions_for_dmd(clean_id)
        q = self.state.priority_queues.pop(clean_id, None)
        entries = list(q) if q else []
        affected_slots: Dict[tuple[str, DomainType], PreparedShowcasePackage] = {}
        for entry in entries:
            slot_key = (entry.target_slot_dmd_id, entry.domain)
            pkg = self._cleanup_entry_package(entry)
            if (
                pkg is not None
                and slot_key in self.state.client_configs
                and self.state.ready_packages.get(slot_key) is pkg
            ):
                affected_slots[slot_key] = pkg

        for (slot_dmd, dom), _pkg in affected_slots.items():
            self.state.ready_packages.pop((slot_dmd, dom), None)
            standby_pkg = self.state.standby_packages.pop((slot_dmd, dom), None)
            if standby_pkg is not None and not standby_pkg.consumed:
                self.state.ready_packages[(slot_dmd, dom)] = standby_pkg
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{slot_dmd}/{dom}/ready",
                    {"ready": True, "item_id": standby_pkg.item_id, "domain": dom, "card": standby_pkg.card.to_dict()},
                    retain=True,
                )
            else:
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{slot_dmd}/{dom}/ready",
                    {"ready": False, "item_id": "", "domain": dom},
                    retain=True,
                )
            self._schedule_hook(slot_dmd, dom)

        await self.streamer.stop_stream(clean_id)
        if is_sync_dmd_id(clean_id) and not any(k[0] == clean_id for k in self.state.client_configs):
            for aid, apkg in list(self.state.active_packages.items()):
                if apkg.dmd_id == clean_id:
                    self.state.active_packages.pop(aid, None)
                    self.state.package_mark_seen.pop(aid, None)
                    self.state.priority_item_ids.discard(aid)
                    shutil.rmtree(apkg.item_dir, ignore_errors=True)
            shutil.rmtree(settings.cache_dir / "consume_once" / clean_id, ignore_errors=True)
        return len(entries)

    async def remove_queue_item(self, dmd_id: str, queue_id: str) -> bool:
        q = self.state.priority_queues.get(dmd_id)
        if not q:
            return False
        target: Optional[QueuedOnDemandEntry] = None
        for entry in list(q):
            if entry.queue_id == queue_id:
                target = entry
                q.remove(entry)
                break
        if target is None:
            return False

        AsyncTaskSupervisor.safe_cancel_task(target.prep_task)
        has_other_preparing = any(e.status in ("queued", "preparing") for e in q)
        if target.status in ("queued", "preparing") and not has_other_preparing:
            self.transcoder.abort_conversions_for_dmd(dmd_id)
            if target.target_slot_dmd_id != dmd_id:
                self.transcoder.abort_conversions_for_dmd(target.target_slot_dmd_id)

        if target.package is not None:
            pkg = target.package
            self.state.package_mark_seen.pop(pkg.item_id, None)
            self.state.priority_item_ids.discard(pkg.item_id)
            slot_key = (target.target_slot_dmd_id, target.domain)
            if slot_key in self.state.client_configs and self.state.ready_packages.get(slot_key) is pkg:
                self.state.ready_packages.pop(slot_key, None)
                standby_pkg = self.state.standby_packages.pop(slot_key, None)
                if standby_pkg is not None and not standby_pkg.consumed:
                    self.state.ready_packages[slot_key] = standby_pkg
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{target.target_slot_dmd_id}/{target.domain}/ready",
                        {"ready": True, "item_id": standby_pkg.item_id, "domain": target.domain, "card": standby_pkg.card.to_dict()},
                        retain=True,
                    )
                else:
                    await self.mqtt.publish_json(
                        f"dmd_showcase/clients/{target.target_slot_dmd_id}/{target.domain}/ready",
                        {"ready": False, "item_id": "", "domain": target.domain},
                        retain=True,
                    )
                self._schedule_hook(target.target_slot_dmd_id, target.domain)
            if pkg.consumed:
                await self.streamer.stop_stream(target.target_slot_dmd_id)
            self.state.active_packages.pop(pkg.item_id, None)
            shutil.rmtree(pkg.item_dir, ignore_errors=True)
        return True

    async def activate_ready_priority_entry(
        self,
        entry: QueuedOnDemandEntry,
        pkg: PreparedShowcasePackage,
        is_server_managed: bool,
    ) -> None:
        self.state.package_mark_seen[pkg.item_id] = entry.mark_seen
        self.state.priority_item_ids.add(pkg.item_id)
        self.state.active_packages[pkg.item_id] = pkg
        entry.package = pkg
        entry.status = "ready"

        slot_key = (entry.target_slot_dmd_id, entry.domain)
        is_ephemeral_sync = (
            is_sync_dmd_id(entry.dmd_id) or is_sync_dmd_id(entry.target_slot_dmd_id)
        ) and slot_key not in self.state.client_configs
        if is_ephemeral_sync:
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.dmd_id}/command",
                {
                    "action": "play_now",
                    "queue_id": entry.queue_id,
                    "domain": entry.domain,
                    "item_id": pkg.item_id,
                    "cache_hit": entry.cache_hit,
                    "card": pkg.card.to_dict(),
                },
                retain=False,
            )
            return

        if is_server_managed:
            if entry.mode == "play_now":
                self.state.director_skip_gap.add(entry.dmd_id)
                await self.streamer.stop_stream(entry.dmd_id)
                abort_ev = self.state.director_play_abort_events.get(entry.dmd_id)
                if abort_ev is not None:
                    abort_ev.set()
            self.state.wake_director(entry.dmd_id)
            return

        slot_key = (entry.target_slot_dmd_id, entry.domain)
        current_ready = self.state.ready_packages.get(slot_key)
        should_promote = (
            entry.mode == "play_now"
            or current_ready is None
            or current_ready.item_id not in self.state.priority_item_ids
        )
        if should_promote:
            if current_ready is not None and not current_ready.consumed:
                if current_ready.item_id not in self.state.priority_item_ids:
                    old_standby = self.state.standby_packages.get(slot_key)
                    if old_standby is not None and not old_standby.consumed:
                        self.state.active_packages.pop(old_standby.item_id, None)
                        shutil.rmtree(old_standby.item_dir, ignore_errors=True)
                    self.state.standby_packages[slot_key] = current_ready

            self.state.ready_packages[slot_key] = pkg
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/{entry.domain}/ready",
                {
                    "ready": True,
                    "queue_id": entry.queue_id,
                    "item_id": pkg.item_id,
                    "domain": entry.domain,
                    "on_demand": True,
                    "cache_hit": entry.cache_hit,
                    "card": pkg.card.to_dict(),
                },
                retain=True,
            )

        if entry.mode == "play_now":
            await self.streamer.stop_stream(entry.target_slot_dmd_id)
            eof_msg = {
                "event": "stream_eof",
                "action": "preempt_play_now",
                "queue_id": entry.queue_id,
                "item_id": pkg.item_id,
                "domain": entry.domain,
            }
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/{entry.domain}/events",
                eof_msg,
                retain=False,
            )
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/stream_state",
                eof_msg,
                retain=False,
            )
            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{entry.target_slot_dmd_id}/command",
                {
                    "action": "play_now",
                    "queue_id": entry.queue_id,
                    "domain": entry.domain,
                    "item_id": pkg.item_id,
                    "cache_hit": entry.cache_hit,
                    "card": pkg.card.to_dict(),
                },
                retain=False,
            )

    async def enqueue_on_demand(
        self,
        dmd_id: str,
        req: Any,
        parse_criteria_fn: Callable[[DomainType, Dict[str, Any], str], FilterCriteria] = parse_domain_criteria,
    ) -> Dict[str, Any]:
        """Enqueue (`enqueue_next`) or immediately preempt (`play_now`) a media item on `dmd_id`."""
        is_sync_slot = is_sync_dmd_id(dmd_id)
        if is_sync_slot:
            # Guarantee at most 1 conversion process & queue entry on `FrontendSync` (`*_sync`) slots.
            await self.clear_dmd_queue(dmd_id)

        preview_id = str(getattr(req, "preview_id", "") or "")
        cached_sim = self.state.studio_previews.get(preview_id) if preview_id else None
        eff_domain: DomainType = (
            cached_sim.domain if cached_sim is not None else getattr(req, "domain", "games")
        )

        prof = self.profile_store.get_profile(dmd_id)
        is_server_managed = bool(
            (prof and prof.get("config_source") == "server")
            or dmd_id in self.state.server_managed_ids
        )
        donor_id = (
            resolve_donor_style_dmd_id(
                dmd_id, eff_domain, self.state.client_configs, self.state.client_udp_ips
            )
            if is_sync_slot
            else dmd_id
        )
        if prof is None and is_sync_slot:
            prof = self.profile_store.get_profile(strip_sync_suffix(dmd_id)) or (
                self.profile_store.get_profile(donor_id) if donor_id else None
            )
        target_slot_dmd_id = (
            dmd_id
            if (is_server_managed or is_sync_slot)
            else self._resolve_target_slot(dmd_id, eff_domain)
        )

        style_lookup_id = donor_id if is_sync_slot else target_slot_dmd_id
        existing_cfg = self.state.client_configs.get((style_lookup_id, eff_domain))
        if existing_cfg is None:
            for (oid, _odom), pair in self.state.client_configs.items():
                if oid in (style_lookup_id, target_slot_dmd_id, dmd_id):
                    existing_cfg = pair
                    break

        req_width = getattr(req, "width", None)
        req_height = getattr(req, "height", None)
        req_lang = getattr(req, "lang", None)

        if prof is not None:
            base_w = int(req_width or prof.get("width") or 128)
            base_h = int(req_height or prof.get("height") or 32)
            base_lang = str(req_lang or prof.get("lang") or "fr").strip().lower()
            matching_slot = next(
                (s for s in (prof.get("playlist") or []) if s.get("domain") == eff_domain),
                {},
            )
            domain_style = dict((prof.get("domain_styles") or {}).get(eff_domain) or {})
            raw_merged: Dict[str, Any] = {
                **(prof.get("card_style") or {}),
                **matching_slot,
                **domain_style,
            }
            render_card = bool(
                is_server_managed and not bool(prof.get("supports_local_card", True))
            )
        elif existing_cfg is not None:
            base_geom, base_crit = existing_cfg
            base_w = int(req_width or base_geom.width)
            base_h = int(req_height or base_geom.height)
            base_lang = str(req_lang or base_crit.lang or "fr").strip().lower()
            raw_merged = {
                "systems": list(base_crit.systems),
                "genres": list(base_crit.genres),
                "decades": list(base_crit.decades),
                "min_rating": base_crit.min_rating,
                "video_type": base_crit.video_type,
                **_style_dict_from_criteria(base_crit),
            }
            render_card = base_crit.render_card_in_stream
        else:
            base_w = int(req_width or (cached_sim.geometry.width if cached_sim else 128))
            base_h = int(req_height or (cached_sim.geometry.height if cached_sim else 32))
            base_lang = str(
                req_lang
                or (cached_sim.criteria.lang if cached_sim else settings.default_lang)
                or "fr"
            ).strip().lower()
            raw_merged = {}
            render_card = bool(cached_sim is not None)

        if cached_sim is not None:
            raw_merged.update(_style_dict_from_criteria(cached_sim.criteria))

        override_render_card = getattr(req, "render_card_in_stream", None)
        if override_render_card is not None:
            render_card = bool(override_render_card)

        raw_merged["lang"] = base_lang
        raw_merged["render_card_in_stream"] = render_card
        req_query = str(getattr(req, "query", "") or "")
        req_fallback_query = str(getattr(req, "fallback_query", "") or "").strip()
        if not req_query.strip() and req_fallback_query:
            req_query = req_fallback_query
        req_systems = getattr(req, "systems", None)
        req_genres = getattr(req, "genres", None)
        req_decades = getattr(req, "decades", None)
        req_video_type = getattr(req, "video_type", None)

        if req_systems:
            raw_merged["systems"] = req_systems
        elif req_query.strip():
            raw_merged["systems"] = []
        if req_genres:
            raw_merged["genres"] = req_genres
        elif req_query.strip():
            raw_merged["genres"] = []
        if req_decades:
            raw_merged["decades"] = req_decades
        elif req_query.strip():
            raw_merged["decades"] = []
        if req_video_type and req_video_type != "any":
            raw_merged["video_type"] = req_video_type

        for field_name in _STYLE_OVERRIDE_FIELDS:
            override_val = getattr(req, field_name, None)
            if override_val is not None:
                raw_merged[field_name] = override_val

        geometry = PanelGeometry(width=base_w, height=base_h)
        criteria = parse_criteria_fn(eff_domain, raw_merged, base_lang)

        if (
            not is_server_managed
            and not is_sync_dmd_id(target_slot_dmd_id)
            and (target_slot_dmd_id, eff_domain) not in self.state.client_configs
        ):
            self.state.client_configs[(target_slot_dmd_id, eff_domain)] = (geometry, criteria)

        from_studio = bool(
            cached_sim is not None
            or preview_id
            or getattr(req, "card_layout", None) is not None
            or getattr(req, "card_theme", None) is not None
        )
        req_mode = getattr(req, "mode", "enqueue_next")
        req_mark_seen = bool(getattr(req, "mark_seen", True))

        # Fast-Path (0 ms Zero-Retranscode): reuse Studio full-preview artifact when geometry matches
        if (
            cached_sim is not None
            and cached_sim.preview_dir.exists()
            and cached_sim.geometry.width == geometry.width
            and cached_sim.geometry.height == geometry.height
        ):
            cloned_pkg = self.studio_service.clone_package_from_studio_preview(
                cached_sim,
                target_slot_dmd_id=target_slot_dmd_id,
                render_card_in_stream=render_card,
            )
            if cloned_pkg is not None:
                entry = QueuedOnDemandEntry(
                    queue_id=f"q_{uuid.uuid4().hex[:10]}",
                    dmd_id=dmd_id,
                    target_slot_dmd_id=target_slot_dmd_id,
                    domain=eff_domain,
                    candidate=cached_sim.candidate,
                    criteria=criteria,
                    geometry=geometry,
                    mode=req_mode,
                    mark_seen=req_mark_seen,
                    from_studio_sim=True,
                    status="ready",
                    package=cloned_pkg,
                )
                q = self.state.priority_queues.setdefault(dmd_id, deque())
                if req_mode == "play_now":
                    q.appendleft(entry)
                else:
                    q.append(entry)
                await self.activate_ready_priority_entry(
                    entry, cloned_pkg, is_server_managed=is_server_managed
                )
                logger.info(
                    "[ORCHESTRATOR] Promoted Studio simulation '%s' ('%s') into '%s' queue in 0ms (zero re-transcode)",
                    cached_sim.preview_id,
                    cached_sim.candidate.title,
                    dmd_id,
                )
                return entry.to_dict()

        cand: Optional[MediaCandidate] = cached_sim.candidate if cached_sim is not None else None
        req_candidate_id = str(getattr(req, "candidate_id", "") or "")
        req_async_prepare = bool(getattr(req, "async_prepare", False))

        req_cache_key = ""
        req_render_sig = ""
        if cached_sim is None and not req_candidate_id and req_query.strip():
            cache_spec = build_on_demand_request_cache_key(
                domain=eff_domain,
                raw_systems=criteria.systems,
                raw_query=req_query,
                geometry=geometry,
                criteria=criteria,
            )
            req_cache_key = cache_spec.cache_key
            req_render_sig = cache_spec.render_signature

            # Fast-Path (0 ms Request-Keyed Persistent Cache Hit): skip BOTH scraper search AND transcode
            if self.on_demand_cache is not None and self.on_demand_cache.is_enabled():
                item_id = f"pkg_{eff_domain}_{uuid.uuid4().hex[:12]}"
                item_dir = (
                    settings.cache_dir
                    / "consume_once"
                    / target_slot_dmd_id
                    / eff_domain
                    / item_id
                )
                cached_pkg = self.on_demand_cache.checkout_to_dir(
                    cache_key=req_cache_key,
                    dest_dir=item_dir,
                    expected_signature=req_render_sig,
                )
                if cached_pkg is not None:
                    restored_cand = candidate_from_cache_dict(cached_pkg.candidate_dict)
                    restored_package = assemble_prepared_package(
                        item_id=item_id,
                        domain=eff_domain,
                        dmd_id=target_slot_dmd_id,
                        cand=restored_cand,
                        geometry=geometry,
                        criteria=criteria,
                        item_dir=item_dir,
                        files_map=cached_pkg.files_map,
                    )
                    entry = QueuedOnDemandEntry(
                        queue_id=f"q_{uuid.uuid4().hex[:10]}",
                        dmd_id=dmd_id,
                        target_slot_dmd_id=target_slot_dmd_id,
                        domain=eff_domain,
                        candidate=restored_cand,
                        criteria=criteria,
                        geometry=geometry,
                        mode=req_mode,
                        mark_seen=req_mark_seen,
                        from_studio_sim=from_studio,
                        cache_hit=True,
                        raw_query=req_query.strip(),
                        cache_key=req_cache_key,
                        render_signature=req_render_sig,
                        status="ready",
                        package=restored_package,
                    )
                    q = self.state.priority_queues.setdefault(dmd_id, deque())
                    if req_mode == "play_now":
                        q.appendleft(entry)
                    else:
                        q.append(entry)
                    await self.activate_ready_priority_entry(
                        entry, restored_package, is_server_managed=is_server_managed
                    )
                    logger.info(
                        "[ORCHESTRATOR] Instant On-Demand cache hit '%s' ('%s') -> '%s' queue (zero search & zero transcode)",
                        req_cache_key,
                        restored_cand.title,
                        dmd_id,
                    )
                    return entry.to_dict()

        if cand is None and req_candidate_id:
            cand = self.chain_service.get_staged_candidate(req_candidate_id)
            if cand is not None and req_lang and cand.lang != criteria.lang and req_query:
                localized = await self.chain_service.search_candidates(
                    criteria=criteria, query=req_query, limit=3
                )
                cand = next(
                    (c for c in localized if c.id == req_candidate_id),
                    localized[0] if localized else cand,
                )

        if cand is None and req_async_prepare and req_query.strip():
            placeholder_cand = MediaCandidate(
                id="pending",
                domain=eff_domain,
                title=req_fallback_query or req_query.strip(),
                year="",
                badge=eff_domain.upper(),
                badge_color="#FF6D00",
                subtitle="",
                rating="",
                synopsis="",
                lang=criteria.lang,
            )
            entry = QueuedOnDemandEntry(
                queue_id=f"q_{uuid.uuid4().hex[:10]}",
                dmd_id=dmd_id,
                target_slot_dmd_id=target_slot_dmd_id,
                domain=eff_domain,
                candidate=placeholder_cand,
                criteria=criteria,
                geometry=geometry,
                mode=req_mode,
                mark_seen=req_mark_seen,
                from_studio_sim=from_studio,
                raw_query=req_query.strip(),
                fallback_query=req_fallback_query,
                cache_key=req_cache_key,
                render_signature=req_render_sig,
                status="preparing",
            )
            q = self.state.priority_queues.setdefault(dmd_id, deque())
            q.appendleft(entry) if req_mode == "play_now" else q.append(entry)
            entry.prep_task = asyncio.get_running_loop().create_task(
                self.prepare_priority_entry(entry, is_server_managed=is_server_managed)
            )
            return entry.to_dict()

        if cand is None:
            if req_candidate_id and not req_query.strip():
                raise ValueError(
                    f"Candidate '{req_candidate_id}' expired or not found; please run /search again."
                )
            cand = await search_candidate_with_fallback(
                self.chain_service, eff_domain, criteria, req_query, req_fallback_query
            )
            if cand is None:
                raise ValueError(
                    f"No matching media found in '{eff_domain}' for query='{req_query}' (lang='{criteria.lang}')."
                )

        entry = QueuedOnDemandEntry(
            queue_id=f"q_{uuid.uuid4().hex[:10]}",
            dmd_id=dmd_id,
            target_slot_dmd_id=target_slot_dmd_id,
            domain=eff_domain,
            candidate=cand,
            criteria=criteria,
            geometry=geometry,
            mode=req_mode,
            mark_seen=req_mark_seen,
            from_studio_sim=from_studio,
            raw_query=req_query.strip(),
            fallback_query=req_fallback_query,
            cache_key=req_cache_key,
            render_signature=req_render_sig,
            status="preparing",
        )
        q = self.state.priority_queues.setdefault(dmd_id, deque())
        q.appendleft(entry) if req_mode == "play_now" else q.append(entry)
        entry.prep_task = asyncio.get_running_loop().create_task(
            self.prepare_priority_entry(entry, is_server_managed=is_server_managed)
        )
        try:
            await asyncio.wait_for(asyncio.shield(entry.prep_task), timeout=0.15)
        except asyncio.TimeoutError:
            pass
        return entry.to_dict()

    async def prepare_priority_entry(
        self,
        entry: QueuedOnDemandEntry,
        is_server_managed: bool,
    ) -> None:
        try:
            if entry.candidate.id == "pending" and entry.raw_query:
                found_cand = await search_candidate_with_fallback(
                    self.chain_service,
                    entry.domain,
                    entry.criteria,
                    entry.raw_query,
                    entry.fallback_query,
                )
                q_now = self.state.priority_queues.get(entry.dmd_id)
                if found_cand is None or q_now is None or entry not in q_now:
                    if q_now is not None and entry in q_now:
                        q_now.remove(entry)
                    logger.info(
                        "[ORCHESTRATOR] Async On-Demand query '%s' (fallback='%s') yielded no match (queue_id='%s')",
                        entry.raw_query,
                        entry.fallback_query,
                        entry.queue_id,
                    )
                    return
                entry.candidate = found_cand

            pkg = await self.state.build_package_from_candidate(
                transcoder=self.transcoder,
                dmd_id=entry.dmd_id if is_sync_dmd_id(entry.dmd_id) else entry.target_slot_dmd_id,
                domain=entry.domain,
                cand=entry.candidate,
                geometry=entry.geometry,
                criteria=entry.criteria,
            )
            q = self.state.priority_queues.get(entry.dmd_id)
            if pkg is None or q is None or entry not in q:
                if pkg is not None:
                    shutil.rmtree(pkg.item_dir, ignore_errors=True)
                return

            if (
                self.on_demand_cache is not None
                and self.on_demand_cache.is_enabled()
                and entry.cache_key
            ):
                sys_label = ",".join(entry.criteria.systems) if entry.criteria.systems else ""
                self.on_demand_cache.store_package(
                    cache_key=entry.cache_key,
                    source_dir=pkg.item_dir,
                    files_map=pkg.files_map,
                    candidate_dict=candidate_to_cache_dict(entry.candidate),
                    meta={
                        "domain": entry.domain,
                        "system": sys_label,
                        "query": entry.raw_query,
                        "title": entry.candidate.title,
                        "width": entry.geometry.width,
                        "height": entry.geometry.height,
                        "duration_s": pkg.card.stream_duration_s,
                        "render_signature": entry.render_signature,
                    },
                )

            await self.activate_ready_priority_entry(
                entry, pkg, is_server_managed=is_server_managed
            )
        except (asyncio.CancelledError, ConversionAbortedError):
            logger.info("[ORCHESTRATOR] Cancelled priority queue prep '%s'", entry.queue_id)
        except Exception as exc:
            logger.error(
                "[ORCHESTRATOR] Failed preparing priority queue item '%s': %s",
                entry.queue_id,
                exc,
            )
