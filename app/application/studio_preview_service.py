"""
Application Service: `StudioPreviewService` (`app/application/studio_preview_service.py`).

Generates `< 100 ms` Card-only `DMDS` previews via `ICardPreviewRendererPort`, full multi-chapter
`DMDS` simulations via `IMediaTranscoderPort`, and `0 ms` zero-retranscode package cloning into
live DMD priority queues.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from app.application.criteria_parser import assemble_prepared_package, parse_domain_criteria
from app.application.showcase_state import ShowcaseRuntimeState, StudioPreviewEntry
from app.application.source_chain_service import DomainSourceChainService
from app.config import settings
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
)
from app.domain.ports import ICardPreviewRendererPort, IMediaTranscoderPort
from app.domain.style_specs import parse_hex_rgb


class StudioPreviewService:
    """Builds Card Studio binary `DMDS` previews and clones cached artifacts in 0 ms."""

    def __init__(
        self,
        state: ShowcaseRuntimeState,
        chain_service: DomainSourceChainService,
        transcoder: IMediaTranscoderPort,
        card_renderer: ICardPreviewRendererPort,
    ) -> None:
        self.state = state
        self.chain_service = chain_service
        self.transcoder = transcoder
        self.card_renderer = card_renderer

    def prune_studio_previews(self, max_entries: int = 12, ttl_s: float = 1800.0) -> None:
        now = time.time()
        expired = [
            pid
            for pid, p in list(self.state.studio_previews.items())
            if (now - p.created_at) > ttl_s or not p.preview_dir.exists()
        ]
        for pid in expired:
            entry = self.state.studio_previews.pop(pid, None)
            if entry is not None:
                shutil.rmtree(entry.preview_dir, ignore_errors=True)
        while len(self.state.studio_previews) > max_entries:
            oldest_pid = min(
                self.state.studio_previews.keys(),
                key=lambda k: self.state.studio_previews[k].created_at,
            )
            entry = self.state.studio_previews.pop(oldest_pid, None)
            if entry is not None:
                shutil.rmtree(entry.preview_dir, ignore_errors=True)

    def resolve_studio_candidate_fallback(self, req: Any) -> MediaCandidate:
        candidate_id = str(getattr(req, "candidate_id", "") or "")
        if candidate_id:
            staged = self.chain_service.get_staged_candidate(candidate_id)
            if staged is not None:
                return staged
        domain: DomainType = getattr(req, "domain", "games")
        badge_color = str(getattr(req, "badge_color", "") or "#FF6D00")
        palette = parse_hex_rgb(badge_color)
        poster_url = getattr(req, "poster_url", None)
        return MediaCandidate(
            id=candidate_id or f"studio_{domain}_{uuid.uuid4().hex[:6]}",
            domain=domain,
            title=str(getattr(req, "title", "") or "Super Street Fighter II Turbo"),
            year=str(getattr(req, "year", "") or "1994"),
            badge=str(getattr(req, "badge", "") or domain.upper()),
            badge_color=badge_color,
            subtitle=str(getattr(req, "subtitle", "") or ""),
            rating=str(getattr(req, "rating", "") or ""),
            synopsis=str(getattr(req, "synopsis", "") or ""),
            lang=str(getattr(req, "lang", "") or "fr"),
            poster_url=poster_url,
            jacket_url=poster_url,
            source_id="studio_preview",
            theme_palette=palette,
        )

    async def build_studio_card_preview(
        self, req: Any
    ) -> Tuple[bytes, List[Dict[str, object]]]:
        """Fast (< 100 ms) Card-only `DMDS` preview bypassing the heavy video transcode semaphore."""
        cand = self.resolve_studio_candidate_fallback(req)
        geom = PanelGeometry(width=int(req.width), height=int(req.height))
        poster_w, poster_h = geom.resolved_poster_dims()
        loop = asyncio.get_running_loop()
        dmds_bytes, n_frames, _eff_fps, dur_s = await loop.run_in_executor(
            None,
            lambda: self.card_renderer.build_fast_card_preview_sync(
                cache_root=str(settings.cache_dir),
                width=geom.width,
                height=geom.height,
                fps=int(getattr(req, "fps", 20)),
                title=cand.title,
                year=cand.year,
                badge=cand.badge,
                badge_color=cand.badge_color,
                subtitle=cand.subtitle,
                rating=cand.rating,
                synopsis=cand.synopsis,
                poster_url=cand.resolved_jacket_url(),
                poster_w=poster_w,
                poster_h=poster_h,
                include_card_intro=bool(getattr(req, "include_card_intro", True)),
                card_layout=str(getattr(req, "card_layout", "classic_split")),
                card_theme=str(getattr(req, "card_theme", "tinted")),
                card_font=str(getattr(req, "card_font", "arcade_pixel")),
                card_accent_color=str(getattr(req, "card_accent_color", "auto")),
                include_poster=bool(getattr(req, "include_poster", True)),
                show_badge=bool(getattr(req, "show_badge", True)),
                show_year=bool(getattr(req, "show_year", True)),
                show_rating=bool(getattr(req, "show_rating", True)),
                include_synopsis=bool(getattr(req, "include_synopsis", True)),
                text_scroll_speed=str(getattr(req, "text_scroll_speed", "normal")),
                card_hold_s=float(getattr(req, "card_hold_s", 0.9)),
                color_boost=getattr(req, "color_boost", "auto"),
            ),
        )
        chapters: List[Dict[str, object]] = [
            {
                "id": "card",
                "label": "1. Card Info",
                "start_frame": 0,
                "frames": n_frames,
                "duration_s": dur_s,
            }
        ]
        return dmds_bytes, chapters

    async def build_studio_full_preview(
        self,
        req: Any,
        parse_criteria_fn: Callable[[DomainType, Dict[str, Any], str], FilterCriteria] = parse_domain_criteria,
    ) -> Tuple[bytes, str, List[Dict[str, object]]]:
        """Build full `DMDS` sequence (`Card + Panoramas + Video`) and cache under `preview_id`."""
        self.prune_studio_previews()
        req_dict = (
            req.model_dump()
            if hasattr(req, "model_dump")
            else dict(vars(req))
        )
        raw_dict = {**req_dict, "render_card_in_stream": True}
        domain: DomainType = getattr(req, "domain", "games")
        lang = str(getattr(req, "lang", "fr") or "fr")
        criteria = parse_criteria_fn(domain, raw_dict, lang)
        geom = PanelGeometry(width=int(req.width), height=int(req.height))

        cand: Optional[MediaCandidate] = None
        candidate_id = str(getattr(req, "candidate_id", "") or "")
        query = str(getattr(req, "query", "") or "")
        if candidate_id:
            cand = self.chain_service.get_staged_candidate(candidate_id)
        if cand is None and query.strip():
            found = await self.chain_service.search_candidates(
                criteria=criteria, query=query.strip(), limit=1
            )
            if found:
                cand = found[0]
        if cand is None:
            cand = self.resolve_studio_candidate_fallback(req)

        preview_id = f"sim_{uuid.uuid4().hex[:12]}"
        preview_dir = settings.cache_dir / "studio_previews" / preview_id
        files_map = await self.state.execute_with_transcode_gate(
            lambda: self.transcoder.transcode_candidate(cand, criteria, geom, preview_dir),
            should_count_completion=lambda _res: True,
        )

        unified_path = Path(str(files_map.get("unified_stream_path") or ""))
        if not unified_path.exists() or int(files_map.get("stream_frames") or 0) == 0:
            dmds_bytes, fallback_chapters = await self.build_studio_card_preview(req)
            unified_path = preview_dir / f"unified_{geom.width}x{geom.height}.rgb565.stream"
            preview_dir.mkdir(parents=True, exist_ok=True)
            unified_path.write_bytes(dmds_bytes)
            files_map["unified_stream_path"] = str(unified_path)
            files_map["stream_frames"] = int(fallback_chapters[0]["frames"])  # type: ignore[arg-type]
            files_map["stream_duration_s"] = float(fallback_chapters[0]["duration_s"])  # type: ignore[arg-type]
            files_map["stream_chapters"] = fallback_chapters

        chapters = list(files_map.get("stream_chapters") or [])  # type: ignore[arg-type]
        entry = StudioPreviewEntry(
            preview_id=preview_id,
            domain=domain,
            candidate=cand,
            criteria=criteria,
            geometry=geom,
            preview_dir=preview_dir,
            files_map=files_map,
            chapters=chapters,
        )
        self.state.studio_previews[preview_id] = entry
        self.prune_studio_previews()
        return unified_path.read_bytes(), preview_id, chapters

    def clone_package_from_studio_preview(
        self,
        prev: StudioPreviewEntry,
        target_slot_dmd_id: str,
        render_card_in_stream: bool,
    ) -> Optional[PreparedShowcasePackage]:
        if not prev.preview_dir.exists():
            return None
        item_id = f"pkg_{prev.domain}_{uuid.uuid4().hex[:12]}"
        item_dir = (
            settings.cache_dir / "consume_once" / target_slot_dmd_id / prev.domain / item_id
        )
        item_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prev.preview_dir, item_dir, dirs_exist_ok=True)

        files_map = dict(prev.files_map)
        unified_name = f"unified_{prev.geometry.width}x{prev.geometry.height}.rgb565.stream"
        unified_path = item_dir / unified_name

        # If target DMD renders its own local Card (Mode 1 / 2A), strip server-rendered Card segment in < 2 ms
        if not render_card_in_stream and files_map.get("card_stream_rgb565"):
            non_card_paths: List[Path] = []
            for pname in files_map.get("panorama_streams_rgb565") or []:  # type: ignore[union-attr]
                pp = item_dir / str(pname)
                if pp.exists():
                    non_card_paths.append(pp)
            vname = str(files_map.get("video_stream_rgb565") or "")
            if vname and (item_dir / vname).exists():
                non_card_paths.append(item_dir / vname)
            if non_card_paths:
                eff_fps = int(files_map.get("stream_fps") or prev.criteria.video_fps or 15)
                n_frames, dur_s = self.card_renderer.merge_streams(
                    non_card_paths,
                    unified_path,
                    prev.geometry.width,
                    prev.geometry.height,
                    eff_fps,
                )
                files_map["stream_frames"] = n_frames
                files_map["stream_duration_s"] = dur_s

        files_map["unified_stream_path"] = str(unified_path)
        return assemble_prepared_package(
            item_id=item_id,
            domain=prev.domain,
            dmd_id=target_slot_dmd_id,
            cand=prev.candidate,
            geometry=prev.geometry,
            criteria=prev.criteria,
            item_dir=item_dir,
            files_map=files_map,
        )
