"""
Application Service: `ConsumeOnceOrchestrator`.

Coordinates:
1. Stateless DMD client registration via MQTT (`dmd_showcase/clients/{dmd_id}/config`, `retain=True`)
   or HTTP (`POST /api/v1/{domain}/config`).
2. Background preparation of 1 unseen `PreparedShowcasePackage` per `(dmd_id, domain)` slot.
3. Strict Consume-Once locking on `start_card` (MQTT) or `GET /api/v1/{domain}/next` (HTTP):
   - Marks candidate ID consumed in `seen_history.json` immediately.
   - Publishes `ready: false` (`retain=True`) on MQTT so it can never be replayed.
4. Real-time UDP streaming via `ffmpeg -re` (`start_stream` event) and immediate disk purge on `stream_eof`.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.application.source_chain_service import DomainSourceChainService, source_chain_service
from app.config import settings
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.domain.ports.frame_streamer import IFrameStreamerPort
from app.domain.ports.media_transcoder import IMediaTranscoderPort
from app.domain.ports.seen_repository import ISeenHistoryRepository
from app.infrastructure.mqtt.showcase_mqtt_gateway import ShowcaseMqttGateway, showcase_mqtt_gateway
from app.infrastructure.persistence.json_seen_repository import seen_repository
from app.infrastructure.streaming.ffmpeg_udp_streamer import udp_clock_streamer
from app.infrastructure.transcoder.dmd_converter_adapter import dmd_transcoder_adapter

logger = logging.getLogger("dmd_showcase.application.orchestrator")

VALID_DOMAINS: Tuple[DomainType, ...] = ("games", "movies", "series", "anime")


class ConsumeOnceOrchestrator:
    """
    Stateless Consume-Once orchestrator managing per-DMD, per-domain prepared slots.
    """

    def __init__(
        self,
        chain_service: Optional[DomainSourceChainService] = None,
        seen_repo: Optional[ISeenHistoryRepository] = None,
        transcoder: Optional[IMediaTranscoderPort] = None,
        streamer: Optional[IFrameStreamerPort] = None,
        mqtt_gateway: Optional[ShowcaseMqttGateway] = None,
    ) -> None:
        self.chain_service = chain_service or source_chain_service
        self.seen_repo = seen_repo or seen_repository
        self.transcoder = transcoder or dmd_transcoder_adapter
        self.streamer = streamer or udp_clock_streamer
        self.mqtt = mqtt_gateway or showcase_mqtt_gateway

        # Per `(dmd_id, domain)` configuration, ready slot, active playing slot, and prep task
        self.client_configs: Dict[Tuple[str, DomainType], Tuple[PanelGeometry, FilterCriteria]] = {}
        self.client_udp_ips: Dict[str, str] = {}
        self.ready_packages: Dict[Tuple[str, DomainType], PreparedShowcasePackage] = {}
        self.active_packages: Dict[str, PreparedShowcasePackage] = {}
        self._prep_tasks: Dict[Tuple[str, DomainType], asyncio.Task[None]] = {}
        self.mqtt.set_handlers(
            on_client_config=self.handle_mqtt_client_config,
            on_client_event=self.handle_mqtt_client_event,
        )

    def _parse_domain_criteria(
        self, domain: DomainType, raw_cfg: Dict[str, Any], top_lang: str = "fr"
    ) -> FilterCriteria:
        lang = str(raw_cfg.get("lang") or top_lang or "fr")
        if lang == "system":
            lang = top_lang or "fr"
        systems = raw_cfg.get("systems")
        if isinstance(systems, str):
            systems = [s.strip() for s in systems.split(",") if s.strip()]
        elif not isinstance(systems, list):
            systems = ["arcade", "mame", "neogeo", "cps1", "cps2", "snes", "megadrive"]

        genres = raw_cfg.get("genres")
        if isinstance(genres, str):
            genres = [g.strip() for g in genres.split(",") if g.strip()]
        elif not isinstance(genres, list):
            genres = []

        decades = raw_cfg.get("decades")
        if isinstance(decades, str):
            decades = [d.strip() for d in decades.split(",") if d.strip()]
        elif not isinstance(decades, list):
            decades = []

        speed = str(raw_cfg.get("text_scroll_speed") or "normal").lower()
        if speed not in ("slow", "normal", "fast"):
            speed = "normal"

        return FilterCriteria(
            domain=domain,
            lang=lang,
            systems=systems,
            genres=genres,
            decades=decades,
            min_rating=float(raw_cfg.get("min_rating") or 6.0),
            video_type=str(raw_cfg.get("video_type") or "any"),
            include_poster=bool(raw_cfg.get("show_poster", raw_cfg.get("include_poster", True))),
            include_synopsis=bool(raw_cfg.get("show_synopsis", raw_cfg.get("include_synopsis", True))),
            text_scroll_speed=speed,  # type: ignore[arg-type]
            jacket_panorama=bool(raw_cfg.get("jacket_panorama", True)),
            marquee_panorama=bool(raw_cfg.get("marquee_panorama", False)),
            snapshot_panorama_count=int(raw_cfg.get("snapshot_panorama_count", 0)),
            include_video=bool(raw_cfg.get("show_video", raw_cfg.get("include_video", True))),
            video_max_duration_s=float(raw_cfg.get("video_max_duration_s") or 8.0),
            video_fps=int(raw_cfg.get("video_fps") or 15),
            video_mode=str(raw_cfg.get("video_mode") or "auto_action"),
            color_boost=bool(raw_cfg.get("color_boost", True)),
        )

    async def handle_mqtt_client_config(self, dmd_id: str, payload: Dict[str, Any]) -> None:
        """
        Handle retained `dmd_showcase/clients/{dmd_id}/config` message published by a DMD
        at boot or when WebUI settings are saved.
        """
        width = int(payload.get("width") or 128)
        height = int(payload.get("height") or 32)
        top_lang = str(payload.get("lang") or "fr")
        geometry = PanelGeometry(width=width, height=height)

        udp_ip = str(payload.get("udp_ip") or payload.get("_peer_ip") or "").strip()
        if udp_ip:
            self.client_udp_ips[dmd_id] = udp_ip

        engines_map = payload.get("domains") or payload.get("engines")
        if isinstance(engines_map, dict):
            for dom_key, dom_cfg in engines_map.items():
                if dom_key in VALID_DOMAINS and isinstance(dom_cfg, dict):
                    dom: DomainType = dom_key  # type: ignore[assignment]
                    criteria = self._parse_domain_criteria(dom, dom_cfg, top_lang=top_lang)
                    await self.register_client_domain(dmd_id, dom, geometry, criteria)
        else:
            dom_raw = str(payload.get("domain") or "games")
            if dom_raw in VALID_DOMAINS:
                dom_single: DomainType = dom_raw  # type: ignore[assignment]
                criteria = self._parse_domain_criteria(dom_single, payload, top_lang=top_lang)
                await self.register_client_domain(dmd_id, dom_single, geometry, criteria)

    async def register_client_domain(
        self,
        dmd_id: str,
        domain: DomainType,
        geometry: PanelGeometry,
        criteria: FilterCriteria,
    ) -> None:
        key = (dmd_id, domain)
        self.client_configs[key] = (geometry, criteria)
        if key not in self.ready_packages and (key not in self._prep_tasks or self._prep_tasks[key].done()):
            self._schedule_preparation(dmd_id, domain)

    def _schedule_preparation(self, dmd_id: str, domain: DomainType) -> None:
        key = (dmd_id, domain)
        existing = self._prep_tasks.get(key)
        if existing and not existing.done():
            return
        loop = asyncio.get_running_loop()
        self._prep_tasks[key] = loop.create_task(self._prepare_slot_task(dmd_id, domain))

    async def _prepare_slot_task(self, dmd_id: str, domain: DomainType) -> None:
        key = (dmd_id, domain)
        cfg_pair = self.client_configs.get(key)
        if not cfg_pair:
            return
        geometry, criteria = cfg_pair
        try:
            candidates = await self.chain_service.acquire_unseen_candidates(criteria, count=1)
            if not candidates:
                await self.mqtt.publish_json(
                    f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                    {"ready": False, "item_id": "", "status": "empty_catalog"},
                    retain=True,
                )
                return

            cand = candidates[0]
            item_id = f"pkg_{domain}_{uuid.uuid4().hex[:8]}_{cand.id}"
            item_dir = settings.cache_dir / "consume_once" / dmd_id / domain / item_id
            files_map = await self.transcoder.transcode_candidate(cand, criteria, geometry, item_dir)
            poster_w, poster_h = geometry.resolved_poster_dims()

            card = ShowcaseCard(
                item_id=item_id,
                domain=domain,
                title=cand.title,
                year=cand.year,
                badge=cand.badge,
                badge_color=cand.badge_color,
                subtitle=cand.subtitle,
                rating=cand.rating,
                synopsis=cand.synopsis if criteria.include_synopsis else "",
                lang=criteria.lang,
                source_used=cand.source_id,
                poster_width=poster_w,
                poster_height=poster_h,
                poster_rgb565_b64=str(files_map.get("poster_rgb565_b64") or ""),
                text_scroll_speed=criteria.text_scroll_speed,
                has_panoramas=bool(files_map.get("has_panoramas")),
                has_video=bool(files_map.get("has_video")),
                stream_frames=int(files_map.get("stream_frames") or 0),
                stream_fps=int(files_map.get("stream_fps") or criteria.video_fps),
                stream_duration_s=float(files_map.get("stream_duration_s") or 0.0),
            )
            unified_path_str = str(files_map.get("unified_stream_path") or "")
            pkg = PreparedShowcasePackage(
                item_id=item_id,
                domain=domain,
                dmd_id=dmd_id,
                candidate=cand,
                card=card,
                geometry=geometry,
                item_dir=item_dir,
                unified_stream_path=Path(unified_path_str) if unified_path_str else None,
                files_map=files_map,
            )
            self.ready_packages[key] = pkg
            self.active_packages[item_id] = pkg

            await self.mqtt.publish_json(
                f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
                {
                    "ready": True,
                    "item_id": item_id,
                    "domain": domain,
                    "card": card.to_dict(),
                },
                retain=True,
            )
            logger.info(
                "[ORCHESTRATOR] Prepared Consume-Once slot for dmd='%s' domain='%s' -> '%s' (source=%s, has_video=%s, frames=%d)",
                dmd_id,
                domain,
                cand.title,
                cand.source_id,
                card.has_video,
                card.stream_frames,
            )
        except Exception as exc:
            logger.error("[ORCHESTRATOR] Failed preparing slot for %s/%s: %s", dmd_id, domain, exc)

    async def claim_next_package(
        self, dmd_id: str, domain: DomainType, item_id: Optional[str] = None
    ) -> Optional[PreparedShowcasePackage]:
        """
        Atomically claim and lock the prepared package for `(dmd_id, domain)`:
        1. Marks the candidate ID as consumed in `seen_history.json`.
        2. Publishes `ready: false` (`retain=True`) on MQTT.
        3. Schedules background preparation of the next unseen title if no stream is needed.
        """
        key = (dmd_id, domain)
        pkg = self.ready_packages.pop(key, None)
        if pkg is None and item_id:
            pkg = self.active_packages.get(item_id)
            if pkg and pkg.consumed:
                return None
        if pkg is None:
            self._schedule_preparation(dmd_id, domain)
            return None

        pkg.consumed = True
        self.seen_repo.mark_consumed(domain, pkg.candidate.id)
        await self.mqtt.publish_json(
            f"dmd_showcase/clients/{dmd_id}/{domain}/ready",
            {"ready": False, "item_id": pkg.item_id, "domain": domain},
            retain=True,
        )
        return pkg

    async def purge_consumed_package(self, item_id: str) -> None:
        """Delete all disk files of a consumed package and schedule the next unseen title."""
        pkg = self.active_packages.pop(item_id, None)
        if pkg is not None:
            shutil.rmtree(pkg.item_dir, ignore_errors=True)
            logger.info(
                "[ORCHESTRATOR] Purged consumed media files for '%s' ('%s') and scheduling next unseen title",
                item_id,
                pkg.candidate.title,
            )
            self._schedule_preparation(pkg.dmd_id, pkg.domain)

    async def handle_mqtt_client_event(self, dmd_id: str, event_payload: Dict[str, Any]) -> None:
        """
        Handle real-time DMD events on `dmd_showcase/clients/{dmd_id}/events`
        or `dmd_showcase/clients/{dmd_id}/{domain}/events`:
        - `start_card`   : locks/consumes the item immediately (`ready=False` retained + `seen_history.json`).
        - `start_stream` : launches `ffmpeg -re` UDP stream to `udp_ip:udp_port`.
        - `stop_stream`  : stops active `ffmpeg -re` UDP stream and purges the consumed item.
        """
        ev_type = str(event_payload.get("event") or event_payload.get("action") or "")
        dom_raw = str(event_payload.get("domain") or "games")
        item_id = str(event_payload.get("item_id") or "")
        domain: DomainType = dom_raw if dom_raw in VALID_DOMAINS else "games"  # type: ignore[assignment]

        if ev_type == "start_card":
            pkg = await self.claim_next_package(dmd_id, domain, item_id=item_id)
            if pkg and pkg.card.stream_frames == 0:
                # No panoramas and no video -> purge disk directory and prepare next right away
                await self.purge_consumed_package(pkg.item_id)

        elif ev_type == "start_stream":
            pkg = self.active_packages.get(item_id)
            if pkg is None:
                pkg = await self.claim_next_package(dmd_id, domain, item_id=item_id)
            if pkg is None or not pkg.unified_stream_path or not pkg.unified_stream_path.exists():
                eof_msg = {"event": "stream_eof", "item_id": item_id, "domain": domain}
                await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/{domain}/events", eof_msg, retain=False)
                await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/stream_state", eof_msg, retain=False)
                if item_id:
                    await self.purge_consumed_package(item_id)
                return

            if not pkg.consumed:
                await self.claim_next_package(dmd_id, domain, item_id=item_id)

            udp_ip = str(
                event_payload.get("udp_ip")
                or self.client_udp_ips.get(dmd_id)
                or event_payload.get("_peer_ip")
                or "127.0.0.1"
            )
            udp_port = int(event_payload.get("udp_port") or 4210)
            started_msg = {"event": "stream_started", "item_id": pkg.item_id, "domain": domain}
            await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/{domain}/events", started_msg, retain=False)
            await self.mqtt.publish_json(f"dmd_showcase/clients/{dmd_id}/stream_state", started_msg, retain=False)

            async def _on_stream_eof(client_dmd_id: str, finished_item_id: str, _ok: bool) -> None:
                eof_msg = {"event": "stream_eof", "item_id": finished_item_id, "domain": domain}
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
            await self.streamer.stop_stream(dmd_id)
            if item_id:
                await self.purge_consumed_package(item_id)


consume_once_orchestrator = ConsumeOnceOrchestrator()
