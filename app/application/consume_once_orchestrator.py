"""
Application Facade: `ConsumeOnceOrchestrator` (`app/application/consume_once_orchestrator.py`).

Thin, modular Application Facade composing five cohesive, single-responsibility services:
- `ClientRegistryService` (`app/application/client_registry_service.py`)
- `SlotPreparationService` (`app/application/slot_preparation_service.py`)
- `StudioPreviewService` (`app/application/studio_preview_service.py`)
- `OnDemandQueueService` (`app/application/on_demand_queue_service.py`)
- `PassivePlaylistDirectorService` (`app/application/passive_director_service.py`)

Depends exclusively on Domain entities, Domain value objects, and Hexagonal Domain Ports
(`app/domain/ports/*`).
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from app.application.client_registry_service import ClientRegistryService
from app.application.concurrency import AsyncTaskSupervisor, DynamicAsyncLimiter
from app.application.criteria_parser import parse_domain_criteria
from app.application.on_demand_queue_service import OnDemandQueueService
from app.application.passive_director_service import PassivePlaylistDirectorService
from app.application.showcase_state import (
    QueuedOnDemandEntry,
    ShowcaseRuntimeState,
    StudioPreviewEntry,
)
from app.application.slot_preparation_service import SlotPreparationService
from app.application.source_chain_service import (
    DomainSourceChainService,
    source_chain_service,
)
from app.application.studio_preview_service import StudioPreviewService
from app.domain.board_identity import (
    canonical_dmd_board_key,
    is_usable_physical_lan_ip,
    select_best_udp_ip,
    strip_mqtt_gen_suffix,
)
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    PanelGeometry,
    PreparedShowcasePackage,
)
from app.domain.ports import (
    ICardPreviewRendererPort,
    IDmdProfileRepository,
    IFrameStreamerPort,
    IMediaTranscoderPort,
    IMqttControlGatewayPort,
    IOnDemandCachePort,
    ISeenHistoryRepository,
    default_port_registry,
)
from app.domain.style_specs import VALID_DOMAINS, VALID_SCROLL_SPEEDS


class ConsumeOnceOrchestrator:
    """
    Hexagonal Application Facade orchestrating Mode 1 (`client` N/N+1 slots), Mode 2A/2B
    (`server` playlists), Studio previews, and On-Demand priority queues.
    """

    def __init__(
        self,
        chain_service: Optional[DomainSourceChainService] = None,
        seen_repo: Optional[ISeenHistoryRepository] = None,
        transcoder: Optional[IMediaTranscoderPort] = None,
        streamer: Optional[IFrameStreamerPort] = None,
        mqtt_gateway: Optional[IMqttControlGatewayPort] = None,
        profile_store: Optional[IDmdProfileRepository] = None,
        card_renderer: Optional[ICardPreviewRendererPort] = None,
        on_demand_cache: Optional[IOnDemandCachePort] = None,
    ) -> None:
        self.chain_service: DomainSourceChainService = chain_service or source_chain_service
        self.seen_repo: ISeenHistoryRepository = (
            seen_repo
            if seen_repo is not None
            else default_port_registry.resolve("seen_repository")
        )
        self.transcoder: IMediaTranscoderPort = (
            transcoder
            if transcoder is not None
            else default_port_registry.resolve("media_transcoder")
        )
        self.streamer: IFrameStreamerPort = (
            streamer
            if streamer is not None
            else default_port_registry.resolve("frame_streamer")
        )
        self.mqtt: IMqttControlGatewayPort = (
            mqtt_gateway
            if mqtt_gateway is not None
            else default_port_registry.resolve("mqtt_gateway")
        )
        self.profile_store: IDmdProfileRepository = (
            profile_store
            if profile_store is not None
            else default_port_registry.resolve("profile_repository")
        )
        self.card_renderer: ICardPreviewRendererPort = (
            card_renderer
            if card_renderer is not None
            else default_port_registry.resolve("card_preview_renderer")
        )
        self.on_demand_cache: IOnDemandCachePort = (
            on_demand_cache
            if on_demand_cache is not None
            else default_port_registry.resolve("on_demand_cache")
        )

        self.state = ShowcaseRuntimeState()

        self.clients = ClientRegistryService(
            state=self.state,
            profile_store=self.profile_store,
            streamer=self.streamer,
        )
        self.studio = StudioPreviewService(
            state=self.state,
            chain_service=self.chain_service,
            transcoder=self.transcoder,
            card_renderer=self.card_renderer,
        )
        self.queue = OnDemandQueueService(
            state=self.state,
            chain_service=self.chain_service,
            studio_service=self.studio,
            transcoder=self.transcoder,
            streamer=self.streamer,
            mqtt=self.mqtt,
            profile_store=self.profile_store,
            resolve_target_slot_fn=self.clients.resolve_target_slot_dmd_id,
            schedule_hook=lambda dmd_id, dom: self._schedule_preparation(dmd_id, dom),
            on_demand_cache=self.on_demand_cache,
        )
        self.slots = SlotPreparationService(
            state=self.state,
            chain_service=self.chain_service,
            seen_repo=self.seen_repo,
            transcoder=self.transcoder,
            streamer=self.streamer,
            mqtt=self.mqtt,
            profile_store=self.profile_store,
            schedule_hook=lambda dmd_id, dom: self._schedule_preparation(dmd_id, dom),
            pop_claimed_priority_fn=self.queue.pop_claimed_priority_entry,
            take_next_priority_pkg_fn=self.queue.take_next_ready_priority_package_for_slot,
        )
        self.director = PassivePlaylistDirectorService(
            state=self.state,
            chain_service=self.chain_service,
            seen_repo=self.seen_repo,
            transcoder=self.transcoder,
            streamer=self.streamer,
            mqtt=self.mqtt,
            profile_store=self.profile_store,
            resolve_target_slot_fn=self.clients.resolve_target_slot_dmd_id,
            cleanup_abandoned_fn=self.slots.cleanup_abandoned_active_packages,
            purge_consumed_fn=self.slots.purge_consumed_package,
        )

        self.mqtt.set_handlers(
            on_client_config=self.handle_mqtt_client_config,
            on_client_event=self.handle_mqtt_client_event,
            on_client_hello=self.handle_client_hello,
        )

    # ── Direct State Aliases & Telemetry Properties ────────────────────────────

    @property
    def client_configs(self) -> Dict[Tuple[str, DomainType], Tuple[PanelGeometry, FilterCriteria]]:
        return self.state.client_configs

    @property
    def client_raw_configs(self) -> Dict[str, Dict[str, Any]]:
        return self.state.client_raw_configs

    @property
    def client_udp_ips(self) -> Dict[str, str]:
        return self.state.client_udp_ips

    @property
    def client_udp_ports(self) -> Dict[str, int]:
        return self.state.client_udp_ports

    @property
    def client_online(self) -> Dict[str, bool]:
        return self.state.client_online

    @property
    def client_transports(self) -> Dict[Tuple[str, DomainType], str]:
        return self.state.client_transports

    @property
    def client_last_seen(self) -> Dict[Tuple[str, DomainType], float]:
        return self.state.client_last_seen

    @property
    def last_played_titles(self) -> Dict[Tuple[str, DomainType], str]:
        return self.state.last_played_titles

    @property
    def last_played_sources(self) -> Dict[Tuple[str, DomainType], str]:
        return self.state.last_played_sources

    @property
    def preparing_sources(self) -> Dict[Tuple[str, DomainType], str]:
        return self.state.preparing_sources

    @property
    def last_claimed_until(self) -> Dict[Tuple[str, DomainType], float]:
        return self.state.last_claimed_until

    @property
    def ready_packages(self) -> Dict[Tuple[str, DomainType], PreparedShowcasePackage]:
        return self.state.ready_packages

    @property
    def standby_packages(self) -> Dict[Tuple[str, DomainType], PreparedShowcasePackage]:
        return self.state.standby_packages

    @property
    def active_packages(self) -> Dict[str, PreparedShowcasePackage]:
        return self.state.active_packages

    @property
    def package_mark_seen(self) -> Dict[str, bool]:
        return self.state.package_mark_seen

    @property
    def priority_item_ids(self) -> Set[str]:
        return self.state.priority_item_ids

    @property
    def priority_queues(self) -> Dict[str, Deque[QueuedOnDemandEntry]]:
        return self.state.priority_queues

    @property
    def studio_previews(self) -> Dict[str, StudioPreviewEntry]:
        return self.state.studio_previews

    @property
    def _prep_tasks(self) -> Dict[Tuple[str, DomainType], asyncio.Task[None]]:
        return self.state.prep_tasks

    @property
    def _director_playlist_cursors(self) -> Dict[str, Tuple[int, int]]:
        return self.state.director_playlist_cursors

    @property
    def active_workers(self) -> int:
        return self.state.metrics.active_workers

    @property
    def queue_depth(self) -> int:
        return self.state.metrics.queue_depth

    @property
    def _completed_jobs_total(self) -> int:
        return self.state.metrics.completed_jobs_total

    @_completed_jobs_total.setter
    def _completed_jobs_total(self, val: int) -> None:
        self.state.metrics.completed_jobs_total = int(val)

    @property
    def _total_job_duration_s(self) -> float:
        return self.state.metrics.total_job_duration_s

    @_total_job_duration_s.setter
    def _total_job_duration_s(self, val: float) -> None:
        self.state.metrics.total_job_duration_s = float(val)

    @property
    def completed_jobs_total(self) -> int:
        return self.state.metrics.completed_jobs_total

    @property
    def total_job_duration_s(self) -> float:
        return self.state.metrics.total_job_duration_s

    @property
    def avg_job_duration_s(self) -> float:
        return self.state.metrics.avg_job_duration_s

    @property
    def ready_slots_count(self) -> int:
        return len(self.state.ready_packages) + len(self.state.director_prefetched)

    @property
    def standby_slots_count(self) -> int:
        return len(self.state.standby_packages)

    @property
    def distinct_dmd_count(self) -> int:
        return len(self.distinct_board_keys())

    @property
    def active_broadcasts_count(self) -> int:
        return self.clients.active_broadcasts_count()

    # ── Delegated Use-Case Methods ─────────────────────────────────────────────

    def distinct_board_keys(self) -> Set[str]:
        return self.clients.distinct_board_keys()

    def record_completed_job(self, duration_s: float, count: int = 1) -> None:
        self.state.metrics.record_completed_job(duration_s, count=count)

    def record_http_client(
        self, dmd_id: str, domain: DomainType, client_ip: Optional[str] = None
    ) -> None:
        self.clients.record_http_client(dmd_id, domain, client_ip=client_ip)

    @staticmethod
    def _safe_cancel_task(task: Optional[asyncio.Task[Any]]) -> None:
        AsyncTaskSupervisor.safe_cancel_task(task)

    def _get_transcode_semaphore(self) -> DynamicAsyncLimiter:
        return self.state.get_transcode_semaphore()

    def reconfigure_concurrency(self, max_workers: Optional[int] = None) -> int:
        return self.state.reconfigure_concurrency(max_workers=max_workers)

    async def apply_runtime_limits(self) -> int:
        return await self.slots.apply_runtime_limits()

    def describe_connected_clients(self) -> List[Dict[str, Any]]:
        return self.clients.describe_connected_clients(
            queue_length_fn=lambda did: len(self.list_queue(did))
        )

    def is_live_client_managed_dmd(
        self, dmd_id: str, extra_http_sessions: Optional[List[Tuple[Any, str]]] = None
    ) -> bool:
        return self.clients.is_live_client_managed_dmd(
            dmd_id, extra_http_sessions=extra_http_sessions
        )

    def synthesize_live_client_profile(
        self, dmd_id: str, extra_http_sessions: Optional[List[Tuple[Any, str]]] = None
    ) -> Optional[Dict[str, Any]]:
        return self.clients.synthesize_live_client_profile(
            dmd_id,
            extra_http_sessions=extra_http_sessions,
            parse_criteria_fn=self._parse_domain_criteria,
        )

    def _parse_domain_criteria(
        self, domain: DomainType, raw_cfg: Dict[str, Any], top_lang: str = "fr"
    ) -> FilterCriteria:
        return parse_domain_criteria(domain, raw_cfg, top_lang=top_lang)

    async def handle_client_hello(
        self, dmd_id: str, payload: Dict[str, Any], detected_ip: str = ""
    ) -> Dict[str, Any]:
        async def _on_server_ready(did: str) -> None:
            await self.sync_server_managed_profiles()
            self.state.wake_director(did)

        return await self.clients.handle_client_hello(
            dmd_id, payload, detected_ip=detected_ip, on_server_profile_ready=_on_server_ready
        )

    async def handle_mqtt_client_config(self, dmd_id: str, payload: Dict[str, Any]) -> None:
        await self.slots.handle_mqtt_client_config(
            dmd_id,
            payload,
            on_hello=self.handle_client_hello,
            parse_criteria_fn=self._parse_domain_criteria,
        )

    async def register_client_domain(
        self,
        dmd_id: str,
        domain: DomainType,
        geometry: PanelGeometry,
        criteria: FilterCriteria,
    ) -> None:
        await self.slots.register_client_domain(dmd_id, domain, geometry, criteria)

    def _schedule_preparation(self, dmd_id: str, domain: DomainType) -> None:
        self.slots.schedule_preparation(dmd_id, domain)

    async def claim_next_package(
        self, dmd_id: str, domain: DomainType, item_id: Optional[str] = None
    ) -> Optional[PreparedShowcasePackage]:
        return await self.slots.claim_next_package(dmd_id, domain, item_id=item_id)

    async def purge_consumed_package(self, item_id: str) -> None:
        await self.slots.purge_consumed_package(item_id)

    async def handle_mqtt_client_event(
        self, dmd_id: str, event_payload: Dict[str, Any]
    ) -> None:
        await self.slots.handle_mqtt_client_event(dmd_id, event_payload)

    def list_queue(self, dmd_id: str) -> List[Dict[str, Any]]:
        return self.queue.list_queue(dmd_id)

    async def remove_queue_item(self, dmd_id: str, queue_id: str) -> bool:
        return await self.queue.remove_queue_item(dmd_id, queue_id)

    async def enqueue_on_demand(self, dmd_id: str, req: Any) -> Dict[str, Any]:
        return await self.queue.enqueue_on_demand(
            dmd_id, req, parse_criteria_fn=self._parse_domain_criteria
        )

    async def build_studio_card_preview(
        self, req: Any
    ) -> Tuple[bytes, List[Dict[str, object]]]:
        return await self.studio.build_studio_card_preview(req)

    async def build_studio_full_preview(
        self, req: Any
    ) -> Tuple[bytes, str, List[Dict[str, object]]]:
        return await self.studio.build_studio_full_preview(
            req, parse_criteria_fn=self._parse_domain_criteria
        )

    async def start_server_managed_profiles(self) -> None:
        await self.director.start_server_managed_profiles()

    async def stop_server_managed_profiles(self) -> None:
        await self.director.stop_server_managed_profiles()

    async def sync_server_managed_profiles(self) -> None:
        await self.director.sync_server_managed_profiles()

    async def upsert_dmd_profile(self, dmd_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.director.upsert_dmd_profile(dmd_id, data)

    async def delete_dmd_profile(self, dmd_id: str) -> bool:
        return await self.director.delete_dmd_profile(dmd_id)

    async def control_client(self, dmd_id: str, action: str) -> Dict[str, Any]:
        return await self.director.control_client(dmd_id, action)

    def _next_playlist_slot_criteria(
        self, dmd_id: str, prof: Dict[str, Any]
    ) -> Optional[Tuple[DomainType, PanelGeometry, FilterCriteria]]:
        return self.director.next_playlist_slot_criteria(
            dmd_id, prof, parse_criteria_fn=self._parse_domain_criteria
        )


_is_usable_physical_lan_ip = is_usable_physical_lan_ip
_select_best_udp_ip = select_best_udp_ip

consume_once_orchestrator = ConsumeOnceOrchestrator()

__all__ = [
    "ConsumeOnceOrchestrator",
    "QueuedOnDemandEntry",
    "StudioPreviewEntry",
    "VALID_DOMAINS",
    "VALID_SCROLL_SPEEDS",
    "_is_usable_physical_lan_ip",
    "_select_best_udp_ip",
    "canonical_dmd_board_key",
    "consume_once_orchestrator",
    "deque",
    "strip_mqtt_gen_suffix",
]
